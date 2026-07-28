from __future__ import annotations

import html
import re
import sqlite3
import uuid
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now, safe_path


REQUIRED_CANDIDATE_HEADINGS = ("ログライン", "一行コンセプト", "想定読者と読後感", "主人公", "中心人物", "物語の始まり", "第一話のあらすじ", "連載の推進力", "この企画の弱点")
EVALUATION_AXES = ("ログライン明瞭度", "主人公の願望と能動性", "主人公への共感または関心", "中心人物関係の強さ", "第一話の満足", "意外な転換の有効性", "先読み欲求", "想定読者と読後感の明瞭さ", "説明過多リスク", "連載の推進力")
ACTIONS = {"select", "hold", "reject_all", "regenerate", "revise"}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _sections(text: str) -> dict:
    result = {}
    for heading in REQUIRED_CANDIDATE_HEADINGS:
        marker = f"## {heading}\n\n"
        if marker not in text: continue
        part = text.split(marker, 1)[1]
        result[heading] = part.split("\n\n## ", 1)[0].strip()
    return result


class ConceptManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator = orchestrator
        self.dummy = dummy
        self.initialize()

    def initialize(self) -> None:
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS concept_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), urs_document_id INTEGER NOT NULL REFERENCES urs_documents(id), urs_path TEXT NOT NULL, urs_version INTEGER NOT NULL, candidate_count INTEGER NOT NULL, status TEXT NOT NULL, adapter TEXT NOT NULL, source_mail_id INTEGER, planning_mail_id INTEGER, comparison_mail_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_concept_session ON concept_sessions(work_id) WHERE status IN ('generating','evaluating','awaiting_selection','selected','held');
            CREATE TABLE IF NOT EXISTS concept_candidates(candidate_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), ordinal INTEGER NOT NULL, title TEXT NOT NULL, path TEXT NOT NULL UNIQUE, generation_run_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,ordinal));
            CREATE TABLE IF NOT EXISTS concept_evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), candidate_id TEXT NOT NULL REFERENCES concept_candidates(candidate_id), path TEXT NOT NULL UNIQUE, evaluation_run_id TEXT NOT NULL UNIQUE, evaluator_id TEXT NOT NULL, recommendation_rank INTEGER, created_at TEXT NOT NULL, UNIQUE(session_id,candidate_id));
            CREATE TABLE IF NOT EXISTS concept_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES concept_sessions(session_id), action TEXT NOT NULL, candidate_id TEXT, instruction_path TEXT, note TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS concept_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE REFERENCES concept_sessions(session_id), version INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, candidate_id TEXT NOT NULL, urs_path TEXT NOT NULL, created_at TEXT NOT NULL);
            """)

    def _work(self, work_id=None): return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work = self._work(work_id)
        with self.orchestrator.connection() as db:
            row = db.execute("SELECT * FROM concept_sessions WHERE session_id=? AND work_id=?", (session_id,work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM concept_sessions WHERE work_id=? ORDER BY created_at DESC LIMIT 1",(work["work_id"],)).fetchone()
        if not row: raise KoboError("企画セッションが見つかりません")
        return row

    def _latest_urs(self, work_id: str):
        with self.orchestrator.connection() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS urs_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), status TEXT NOT NULL, question_version TEXT NOT NULL, next_question_id TEXT, urs_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_mail_id INTEGER); CREATE TABLE IF NOT EXISTS urs_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(session_id, version));")
            row=db.execute("SELECT d.id,d.version,d.path,s.session_id FROM urs_documents d JOIN urs_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.status='final' ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
        if not row:
            profile = self.orchestrator.config.root / "novels" / work_id / "READER_PROFILE.v001.md"
            if not profile.is_file(): raise KoboError("確定済みURSまたは読者プロファイルがありません")
            timestamp = now(); sid = f"profile-{work_id}"
            with self.orchestrator.connection() as db:
                db.executescript("CREATE TABLE IF NOT EXISTS urs_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), status TEXT NOT NULL, question_version TEXT NOT NULL, next_question_id TEXT, urs_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_mail_id INTEGER); CREATE TABLE IF NOT EXISTS urs_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(session_id, version));")
                db.execute("INSERT OR IGNORE INTO urs_sessions VALUES(?,?,?,?,?,?,?,?,?)", (sid, work_id, "completed", "reader-profile", None, "final", timestamp, timestamp, None))
                db.execute("INSERT OR IGNORE INTO urs_documents(session_id,version,status,path,created_at) VALUES(?,?,?,?,?)", (sid, 1, "final", str(profile.relative_to(self.orchestrator.config.root)), timestamp))
                row = db.execute("SELECT id,version,path,session_id FROM urs_documents WHERE session_id=? AND version=1", (sid,)).fetchone()
        path=safe_path(self.orchestrator.config.root,row["path"],must_exist=True)
        return row,path

    def _handoff_mail(self, work_id: str, urs_path: Path) -> int | None:
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='urs-maker' AND recipient_id='planner' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%urs_path={urs_path}%")).fetchone()
        return row[0] if row else None

    def start(self, work_id=None, candidate_count: int | None=None, *, generate: bool=True) -> dict:
        work=self._work(work_id); count=5 if candidate_count is None else candidate_count
        if not isinstance(count,int) or not 1 <= count <= 5: raise KoboError("候補数は1〜5で指定してください")
        urs,path=self._latest_urs(work["work_id"]); timestamp=now(); session_id=new_id("concept")
        mail_id=self._handoff_mail(work["work_id"],path)
        with self.orchestrator.connection() as db:
            try: db.execute("INSERT INTO concept_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_id,work["work_id"],urs["id"],str(path),urs["version"],count,"generating","dummy" if self.dummy else "gemini",mail_id,None,None,timestamp,timestamp,None))
            except sqlite3.IntegrityError as error: raise KoboError("この作品には進行中の企画セッションがあります") from error
        if mail_id:
            planning_mail_id=self.orchestrator.mail.send("planner","concept-reviewer",f"企画開始 work_id={work['work_id']} session_id={session_id} urs_path={path}",parent_message_id=mail_id)
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET planning_mail_id=? WHERE session_id=?",(planning_mail_id,session_id))
        return self.resume(work["work_id"],session_id) if generate else self.status(work["work_id"],session_id)

    def _candidate_path(self,session,ordinal):
        return self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/session["session_id"]/f"candidate-c{ordinal:02d}.md"

    def _dummy_candidate(self,session,ordinal):
        data=[
        ("幼馴染と嘘の婚約",
         "見合いから逃げたい令嬢が、幼馴染の秘書に偽装婚約を頼み、隠していた本音との距離を測る話。",
         "断ったはずの恋に、契約という名目で戻ってしまう年の差なし幼馴染ラブコメ。",
         "恋愛の駆け引きを甘酸っぱく読みたい20代女性向け。読後は、素直になれない二人がすれ違いながらも近づく温かさと、次の一歩を見たくなる期待感を残す。",
         "24歳女性。老舗呉服店の跡取り令嬢で、家の決めた見合いを一度断った。今は、幼馴染の秘書に頼れる関係を保ちながら、本当の望みを言えずにいる。人前では気が強いが、身内には弱い。『命令じゃなくてお願いです』と、初めて敬語を崩さず頼み込む。",
         "- 佐倉美咲(主人公): 呉服店の跡取り。秘書の海斗に強がるが、素の弱さを唯一見せている。\n- 常盤海斗: 美咲付きの秘書。五年前に想いを飲み込んだまま距離を保ち、婚約者役を引き受ける。\n- 美咲の祖母: 縁談を進める張本人。海斗の本心に気づいており、二人の背中を押す。",
         "見合い相手が本社に押しかけた朝、美咲はとっさに『婚約者がいます』と口走り、その場にいた秘書の海斗を指してしまう。",
         "美咲は老舗呉服店の跡取りとして、祖母が決めた見合いを望んでいない。しかし見合い相手が本社まで押しかけ、断り切れず、とっさに隣にいた秘書の海斗を『婚約者です』と紹介してしまう。海斗は動揺を見せず話を合わせるが、二人の間には五年前に告げられなかった想いが横たわっている。祖母は縁談を進めたい張本人でありながら、二人の様子に何かを察している。美咲は事務的な芝居のつもりで、契約婚約の条件を紙に書き出し、恋愛感情は持ち込まないと自分に言い聞かせる。ところが取引先との会食で、海斗が『美咲さんの好きな柄はこれです』と迷わず答え、美咲は自分より自分をよく見ている海斗に動揺する。祖母は二人きりで出かける用事を増やし、見合い相手は『本当に婚約者なら証拠を見せてほしい』と迫ってくる。追い詰められた美咲は、開き直って海斗の手を取り、初めて自分から距離を詰める。その夜、海斗は五年間黙っていた理由を短く口にし、声が震えているのを美咲は初めて聞く。美咲は聞かなかったふりをせず、初めて『続きを聞かせて』と自分から返す。祖母は台所で二人の様子をこっそり見ながら、縁談話を進めるふりをやめる決心を固める。芝居のはずの婚約に、本当の言葉が一つ紛れ込んだ夜、美咲は自分がどこまで本気なのか、まだ言葉にできずにいる。",
         "偽装婚約という嘘の関係が、祖母の後押し、取引先との駆け引き、五年分の想いの開示によって少しずつ本物へ近づく過程が話を進める。",
         "すれ違いを長引かせると中盤が停滞しやすい。毎話、関係を進める具体的な出来事（取引先対応、祖母の介入、過去の告白）を入れる必要がある。"),
        ("雨の日だけ見える先輩",
         "人付き合いが苦手な契約社員が、雨の日だけ現れる屋上の先輩(幽霊)との約束を守るため、消える理由を探す話。",
         "会えるのは雨の日だけ。会えない日が愛おしくなる不思議な社内すれ違いラブストーリー。",
         "不思議な出会いと静かな感情の機微を好む20〜30代向け。読後は、儚さの中にある温かさと、次に雨が降る日を待ちたくなる余韻を残す。",
         "26歳女性。契約社員で人付き合いが苦手。目立たず定時で帰ることを望んでいたが、屋上で出会った先輩との時間を失いたくないと思い始めている。頑固で、頼ることが苦手。『別に、時間が余ってるだけです』と強がる。",
         "- 深雪(主人公): 契約社員。感情表現が苦手だが、屋上の先輩には素直になれる。\n- 屋上の先輩・伊織: 雨の日だけ屋上に現れる。理由を話さず、深雪の話を静かに聞く。\n- 同期の遥: 深雪の数少ない友人。先輩の正体に気づき始めている。",
         "深雪が雨宿りに上がった屋上で、社員名簿にない先輩と名乗る男に出会い、次の雨の日にまた会う約束をしてしまう。",
         "深雪は人と深く関わらずに定時で帰りたいと思っている。ある雨の日、屋上で伊織と名乗る先輩に出会い、社員証を見せられないまま世間話をして帰る。翌週の晴れの日、伊織を捜しても社内のどこにもおらず、名簿にも名前がなく、深雪は自分の記憶を疑い始める。次に雨が降った日、伊織は何事もなかったように屋上に現れ、深雪は『あなたは一体誰なんですか』と問い詰める。伊織は答えをはぐらかし、代わりに深雪が誰にも話していない残業続きの悩みを静かに言い当てる。同期の遥は、五年前に屋上から姿を消した社員がいたという噂話を教え、深雪は伊織がその人物ではないかと疑い始め、落ち着かない気持ちで次の雨を待つようになる。次の雨の日、伊織は『次に晴れたら、もう会えないかもしれない』と初めて弱さを見せ、深雪は自分でも驚くほど動揺する。深雪は帰りたかったはずのいつもの日常より、伊織との約束を守りたいと気づき、傘を差し出して『晴れの日も、私が捜しに行きます』と言う。伊織は驚いた顔を見せたあと、初めて名字を名乗る。天気予報を確認する深雪の様子を、遥は少し複雑な顔で見守り、何かに気づいたように黙って給湯室へ向かう。雨があがり、二人の間には小さな約束と、消えた理由という謎だけが残った。",
         "伊織が姿を消した理由という秘密と、深雪が距離を縮めたいという願望が交互に進み、雨の日ごとに関係と謎が少しずつ進展する。",
         "最後の謎だけに頼ると中だるみする。毎話、深雪と伊織の関係の変化を先に置き、謎は関係の副産物として進める必要がある。"),
        ("十年前に埋めた手紙",
         "疎遠になった幼馴染との十年前の約束を、亡き祖母の遺品から知った女性が、約束を果たすか隠すか選ぶ話。",
         "叶えられなかった約束が、十年越しに二人の今を試す。",
         "静かな感情の機微と、過去との和解を読みたい30代前後向け。読後は、痛みを含んだ懐かしさと、関係が再生する予感を残す。",
         "32歳女性。祖母の家を整理している最中で、過去の人間関係に区切りをつけたいと願っている。本音を言わずに済ませる癖がある。『今更、なんて言えばいいの』とひとり呟く。",
         "- 綾(主人公): 祖母の家を整理する会社員。過去の約束から目を逸らしてきた。\n- 拓海: 幼馴染。十年前に引っ越し、綾に何も言わず疎遠になった張本人。\n- 祖母(故人): 二人の約束を知っていた唯一の人物。手紙という形で真実を残す。",
         "祖母の遺品整理中、綾は自分宛てではない、十年前の日付が入った拓海への未投函の手紙を見つける。",
         "綾は祖母の家を整理する中で、祖母が拓海宛てに書いた未投函の手紙を見つける。開けるべきか迷ったまま、綾は喪主として久しぶりに拓海へ連絡を取らざるを得なくなる。十年前、拓海は何も言わずに引っ越し、綾との『大人になったら会う』という約束を一方的に破ったはずだった。久しぶりに会った拓海は淡々としていて、綾は昔のことを聞けないまま葬儀の準備を進める。手紙を渡すべきか迷う綾に、祖母の隣人が『あの子は本当は最後まで来たがっていた』と意外な話をし、綾の中で積み上げてきた恨みが揺らぐ。動揺した綾は、その場しのぎで手紙を鞄にしまい込むが、片付けの最中に拓海に見つかってしまう。追い詰められた綾が事情を話すと、拓海は初めて、当時親の借金で夜逃げ同然に引っ越したことと、恥ずかしくて綾に合わせる顔がなかったことを打ち明ける。綾は責める代わりに、祖母の手紙をまだ渡さず、自分の言葉で『十年分、ちゃんと話してくれる』と伝える。答えの代わりに、拓海は十年前と同じ場所で待つ約束をする。線香の匂いが残る居間で、綾は手紙を仏壇の奥にしまい、いつか二人で読む日を思い描きながら、拓海の連絡先を古い電話帳から新しい手帳へ書き写す。手紙の中身は、まだ誰も読んでいないまま、二人の間に置かれている。",
         "祖母の手紙の内容という秘密と、綾と拓海の関係の再構築が並行し、二人の過去の事情が少しずつ明かされることで話が進む。",
         "過去語りに寄りすぎると停滞する。現在の綾と拓海の選択や行動を毎話中心に置く必要がある。"),
        ("墜落した飛行船と、二人の借り",
         "護衛任務で墜落した傭兵が、敵国の若い密偵と行動を共にし、互いの命を借りながら山を越える話。",
         "殺し合うはずだった二人が、生き延びるためだけに手を組む一夜限りの共犯関係。",
         "緊張感のある冒険と、対立から芽生える信頼を読みたい男女問わない層向け。読後は、張り詰めた緊張がほどけていく安堵と、二人の今後を見たくなる期待感を残す。",
         "29歳男性。傭兵で、依頼を確実にこなしつつ仲間を巻き込みたくないと望んでいる。表向き冷静だが、過去の任務失敗を引きずっている。『借りは返す。それだけだ』と最低限しか語らない。",
         "- レイン(主人公): 護衛任務中の傭兵。任務失敗の過去から、他人と組むことを避けてきた。\n- ミラ: 敵国の若い密偵。墜落に巻き込まれ、レインと利害だけで手を組む。\n- レインの元隊長: 回想と手配書を通じて登場。レインの過去の失敗に関わる人物。",
         "護送していた飛行船が撃墜され、レインは敵国の密偵ミラと二人だけ、雪山の斜面に投げ出される。",
         "レインは護衛任務中の飛行船を撃墜され、敵国の密偵ミラと二人だけ雪山の斜面に取り残される。互いに殺し合う立場だが、下山するには相手の技能が要ることに、二人ともすぐ気づく。レインは道を知り、ミラは薬草の知識で怪我を治せる。二人は『山を降りるまでの間だけ』という条件で、渋々手を組む。夜営の最中、ミラはレインの過去の任務失敗を知っていることをほのめかし、レインは口数を減らして警戒を強める。翌日、雪崩に巻き込まれかけたレインをミラが自分を危険にさらして助け、レインは借りができたことに苛立ちながらも、態度をわずかに変える。山を下りる途中、追手の斥候に囲まれ、ミラは自国側に助けを求めれば一人だけ逃げられる状況にありながら、レインを見捨てずに囮を買って出る。窮地を切り抜けた後、レインは初めて『次は俺が借りを返す番だ』と言い、任務ではなく個人としての約束を交わす。夜の焚き火越しに、ミラは初めて素の表情で小さく笑い、レインはその変化に戸惑いながらも目をそらさない。麓が見えたところで、ミラは自国に戻れば処罰されると初めて弱音をこぼし、レインはその横顔をしばらく黙って見てから、自分の外套を無言でミラの肩にかける。レインは彼女をどうするか、まだ決めていない。",
         "敵対関係から生まれた借りの応酬と、ミラの処遇という選択が、山を越えたあとも二人の関係を動かし続ける。",
         "設定説明が増えると冒険活劇に寄りすぎる。毎話、レインとミラの信頼関係の変化を中心に置く必要がある。"),
        ("黒猫は嘘を見抜く",
         "本音を隠すのが得意なOLが、他人の嘘だけに反応する拾い猫と暮らし始め、自分の本心と向き合う話。",
         "猫が見抜くのは他人の嘘だけじゃない。飼い主の本音まで炙り出す同居系ハートフルコメディ。",
         "猫との暮らしと、自分の気持ちに気づく物語を読みたい層向け。読後は、くすぐったい気恥ずかしさと、明日も本音で話したくなる前向きな気持ちを残す。",
         "27歳女性。営業事務で、本音を言わず穏便に済ませたいと望んでいる。捨て猫を拾ったことで生活が変わり始めている。『猫に説教されるとは思わなかった』とぼやく。",
         "- 加奈(主人公): 本音を隠す癖がある会社員。猫の反応で自分の嘘にも気づき始める。\n- 黒猫・墨: 他人の嘘に対してだけ低く唸る拾い猫。正体や理由は不明。\n- 同僚の坂本: 加奈に好意を持つが遠回しにしか態度を示さない同僚。",
         "雨の夜に拾った黒猫が、取引先の見え透いたお世辞に低く唸ったことで、加奈は猫が嘘に反応することに気づく。",
         "加奈は本音を隠して場を丸く収める癖がある。雨の夜に拾った黒猫の墨は、家に来た同僚の坂本の見え透いたお世辞に低く唸り、加奈は墨が嘘に反応することに気づく。面白半分で職場に連れていけない代わりに、電話越しの会話を聞かせると、墨は上司の口先だけの謝罪にも唸り、加奈はひそかに周囲の本音を見抜く道具として墨を使い始める。ある夜、自分が『大丈夫です』と繰り返す言葉にも墨が唸ることに気づき、加奈は自分自身の嘘に初めて向き合わされて動揺する。加奈は墨を撫でながら、これまで本音を隠すことで何を守ってきたのかを考え込み、いつもより長く天井を見つめる。翌日、坂本から思い切った誘いを受けた加奈は、いつもの当たり障りない返事でやり過ごそうとするが、墨が足元でひときわ大きく唸る。加奈は初めて『本当は嬉しいけど、傷つくのが怖くて断ってた』と本音を口にする。坂本は一瞬驚いた顔をしたあと笑い、加奈は自分の言葉で人と向き合えたことに安堵する。窓の外で雨が上がり、部屋の空気が少しだけ軽くなる。夜、墨は静かに丸くなり、もう唸らなかった。加奈は、墨がこれからも自分の嘘を見抜くのか、それとも今夜だけの奇跡だったのかを考えながら眠りにつく。",
         "猫が嘘を見抜く理由という謎と、加奈が本音を出せるようになる成長が並行し、坂本との関係の変化が次話への関心を作る。",
         "猫の可愛さだけに頼ると単発ネタで終わる。加奈自身の成長と坂本との関係進展を毎話動かす必要がある。"),
        ]
        title,logline,concept,reader,protagonist,central,opening,synopsis,drive,weakness=data[ordinal-1]
        values={"ログライン":logline,"一行コンセプト":concept,"想定読者と読後感":reader,"主人公":protagonist,"中心人物":central,"物語の始まり":opening,"第一話のあらすじ":synopsis,"連載の推進力":drive,"この企画の弱点":weakness}
        return f"# 企画候補 C{ordinal:02d}: {title}\n\n- 候補ID: `C{ordinal:02d}`\n- 参照URS: `{session['urs_path']}`（v{session['urs_version']:03d}）\n- 生成アダプター: `dummy`（実Gemini生成物ではない）\n\n"+"\n\n".join(f"## {h}\n\n{values[h]}" for h in REQUIRED_CANDIDATE_HEADINGS)+"\n"

    def _validate_candidate(self,text: str):
        missing=[h for h in REQUIRED_CANDIDATE_HEADINGS if f"## {h}" not in text]
        if missing: raise KoboError(f"企画候補の必須項目が不足しています: {missing}")
        sections=_sections(text)
        logline=sections["ログライン"]
        if len(logline) > 80: raise KoboError(f"ログラインは80字以内です（{len(logline)}字）")
        synopsis=sections["第一話のあらすじ"]
        if not 500 <= len(synopsis) <= 800: raise KoboError(f"第一話のあらすじは500〜800字です（{len(synopsis)}字）")
        protagonist=sections["主人公"]
        if not protagonist: raise KoboError("主人公欄が空です")
        if not re.search(r"\d+歳|\d+代",protagonist): raise KoboError("主人公欄に年齢層の記載がありません")
        if not any(token in protagonist for token in ("男性","女性")): raise KoboError("主人公欄に性別の記載がありません")
        if not any(token in protagonist for token in ("望","願")): raise KoboError("主人公欄に願望の記載がありません")
        if not sections["想定読者と読後感"]: raise KoboError("想定読者と読後感が空です")
        central=sections["中心人物"]
        people=[line for line in central.splitlines() if line.startswith("- ")]
        if not people: raise KoboError("中心人物が記載されていません")
        if len(people) > 3: raise KoboError(f"中心人物は3人以内です（{len(people)}人）")

    def _generate_one(self,session,ordinal):
        path=self._candidate_path(session,ordinal); run_id=self.orchestrator._new_run_id(); candidate_id=f"{session['session_id']}-c{ordinal:02d}"
        if path.exists(): raise KoboError("既存企画候補の上書きを拒否しました")
        if self.dummy: text=self._dummy_candidate(session,ordinal)
        else:
            task=path.parent/f"task-c{ordinal:02d}.md"; urs=Path(session["urs_path"]).read_text(encoding="utf-8")
            atomic_write(task,f"# 企画候補生成\n\n候補ID C{ordinal:02d}。本文を書かず、次の確定URSから他候補と明確に異なる企画を作ること。必須見出し: {', '.join(REQUIRED_CANDIDATE_HEADINGS)}\n\n## 確定URS\n\n{urs}")
            agent=self.orchestrator.agents["planner"]; refs={"task_path":str(task),"output_path":str(path),"model":self.orchestrator.config.models.get("planner",self.orchestrator.config.models.get("gemini",agent.model)),"run_id":run_id,"run_dir":str(path.parent),"agent_path":str(agent.path),"mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,path); text=path.read_text(encoding="utf-8")
        self._validate_candidate(text); atomic_write(path,text); timestamp=now(); title=text.splitlines()[0].split(":",1)[-1].strip()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO concept_candidates VALUES(?,?,?,?,?,?,?,?)",(candidate_id,session["session_id"],ordinal,title,str(path),run_id,"generated",timestamp))

    def _evaluate_one(self,session,candidate):
        path=Path(candidate["path"]).with_name(f"evaluation-c{candidate['ordinal']:02d}.md"); run_id=self.orchestrator._new_run_id()
        if path.exists(): raise KoboError("既存評価の上書きを拒否しました")
        body=f"# 企画評価 {candidate['candidate_id']}\n\n- 評価担当: `concept-reviewer`\n- 評価実行ID: `{run_id}`\n- 候補生成実行ID: `{candidate['generation_run_id']}`\n\n"
        for axis in EVALUATION_AXES:
            body+=f"## {axis}\n\n根拠: 候補成果物と確定URSを独立に照合。\n\n長所: 案{candidate['ordinal']}固有の推進力がある。\n\n弱点・リスク: 仮定の検証が必要。\n\n改善案: 企画確定前に未決事項を明示する。\n\n"
            if axis=="先読み欲求": body+="判定方針: 最後に謎を置いただけの引きより、人物関係・願望・秘密が途中から自然に動いて次を読みたくなる構造を高く評価する。\n\n"
        body+=f"## AI推奨\n\n暫定順位: {candidate['ordinal']}。数値だけで採否を決めず、最終選択は利用者が行う。\n"
        atomic_write(path,body); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO concept_evaluations(session_id,candidate_id,path,evaluation_run_id,evaluator_id,recommendation_rank,created_at) VALUES(?,?,?,?,?,?,?)",(session["session_id"],candidate["candidate_id"],str(path),run_id,"concept-reviewer",candidate["ordinal"],timestamp))

    def resume(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"] in ("final","rejected","superseded"): return self.status(session["work_id"],session["session_id"])
        try:
            with self.orchestrator.connection() as db: existing={r[0] for r in db.execute("SELECT ordinal FROM concept_candidates WHERE session_id=?",(session["session_id"],))}
            for ordinal in range(1,session["candidate_count"]+1):
                if ordinal not in existing: self._generate_one(session,ordinal)
            with self.orchestrator.connection() as db:
                db.execute("UPDATE concept_sessions SET status='evaluating',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                candidates=[dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]; evaluated={r[0] for r in db.execute("SELECT candidate_id FROM concept_evaluations WHERE session_id=?",(session["session_id"],))}
            for candidate in candidates:
                if candidate["candidate_id"] not in evaluated: self._evaluate_one(session,candidate)
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET status='awaiting_selection',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
            if session["planning_mail_id"]:
                comparison_mail_id=self.orchestrator.mail.send("concept-reviewer","planner",f"企画比較完了 session_id={session['session_id']} 状態=選択待ち",parent_message_id=session["planning_mail_id"])
                with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET comparison_mail_id=? WHERE session_id=?",(comparison_mail_id,session["session_id"]))
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE concept_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def status(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db:
            generated=db.execute("SELECT COUNT(*) FROM concept_candidates WHERE session_id=?",(session["session_id"],)).fetchone()[0]; evaluated=db.execute("SELECT COUNT(*) FROM concept_evaluations WHERE session_id=?",(session["session_id"],)).fetchone()[0]; selected=db.execute("SELECT candidate_id FROM concept_actions WHERE session_id=? AND action='select' ORDER BY id DESC LIMIT 1",(session["session_id"],)).fetchone()
        return {"session_id":session["session_id"],"work_id":session["work_id"],"status":session["status"],"adapter":session["adapter"],"urs_path":session["urs_path"],"urs_version":session["urs_version"],"candidate_count":session["candidate_count"],"generated":generated,"evaluated":evaluated,"selected_candidate_id":selected[0] if selected else None,"error":session["error"]}

    def candidates(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: return [dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]

    def candidate(self,candidate_id,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if len(candidate_id)==3 and candidate_id[0].lower()=="c" and candidate_id[1:].isdigit():
            ordinal=int(candidate_id[1:])
            with self.orchestrator.connection() as db: short=db.execute("SELECT candidate_id FROM concept_candidates WHERE session_id=? AND ordinal=?",(session["session_id"],ordinal)).fetchone()
            if short: candidate_id=short[0]
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM concept_candidates WHERE candidate_id=? AND session_id=?",(candidate_id,session["session_id"])).fetchone()
        if not row: raise KoboError("候補が見つかりません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def comparisons(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: rows=[dict(r) for r in db.execute("SELECT e.*,c.ordinal,c.title FROM concept_evaluations e JOIN concept_candidates c ON c.candidate_id=e.candidate_id WHERE e.session_id=? ORDER BY e.recommendation_rank",(session["session_id"],))]
        for row in rows: row["content"]=Path(row["path"]).read_text(encoding="utf-8")
        return rows

    def board(self, work_id=None, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM concept_candidates WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]
        cards=[]
        for row in rows:
            content=Path(row["path"]).read_text(encoding="utf-8")
            sections=_sections(content)
            summary=(f'<div class="summary">'
                     f'<p><strong>ログライン:</strong> {html.escape(sections.get("ログライン",""))}</p>'
                     f'<p><strong>一行コンセプト:</strong> {html.escape(sections.get("一行コンセプト",""))}</p>'
                     f'<p><strong>主人公:</strong> {html.escape(sections.get("主人公",""))}</p>'
                     f'<p><strong>想定読者と読後感:</strong> {html.escape(sections.get("想定読者と読後感",""))}</p>'
                     f'</div>')
            judge=('<div class="judge">'
                   '<p>面白そう度: 1 2 3 4 5</p>'
                   '<p>続きを読みたい: はい / いいえ</p>'
                   '<p>最も気になる人物: </p>'
                   '<p>弱いと感じる点: </p>'
                   '<p>判定: 選ぶ / 修正候補 / 保留 / 却下</p>'
                   '</div>')
            cards.append(f'<article class="card"><h2>C{row["ordinal"]:02d} {html.escape(row["title"])}</h2>{summary}<pre>{html.escape(content)}</pre>{judge}</article>')
        path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/session["session_id"]/"editorial-board"/"index.html"
        path.parent.mkdir(parents=True,exist_ok=True)
        body="".join(cards)
        page=f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>編集会議 - 企画比較</title><style>body{{margin:0;background:#f5f1ea;color:#28231f;font-family:system-ui,"Yu Gothic",sans-serif}}main{{max-width:1100px;margin:auto;padding:1rem}}.board{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:1rem}}.card{{background:#fff;border:1px solid #d8cec2;border-radius:12px;padding:1rem;box-shadow:0 2px 8px #0001}}pre{{white-space:pre-wrap;font:inherit;line-height:1.7;margin:0}}h1{{font-size:1.5rem}}h2{{font-size:1.1rem;border-bottom:2px solid #c96;padding-bottom:.4rem}}.summary{{background:#fbf6ee;border:1px solid #e6dccb;border-radius:8px;padding:.6rem .8rem;margin:.6rem 0;font-size:.92rem;line-height:1.6}}.summary p{{margin:.2rem 0}}.judge{{border-top:1px dashed #c9bda8;margin-top:.8rem;padding-top:.6rem;font-size:.92rem;line-height:1.8}}.judge p{{margin:.2rem 0}}</style></head><body><main><h1>編集会議用 企画比較</h1><p>候補を比較し、人間が面白そう・惜しい・保留・全却下を判断する。AI推奨だけでは制作へ進まない。</p><section class="board">{body}</section></main></body></html>'''
        atomic_write(path,page)
        return {"path":str(path),"session_id":session["session_id"],"candidate_count":len(rows),"status":session["status"]}

    def action(self,action,candidate_id=None,*,instruction_path=None,note=None,work_id=None,session_id=None):
        if action not in ACTIONS: raise KoboError("企画操作が不正です")
        session=self._session(work_id,session_id)
        if session["status"] not in ("awaiting_selection","selected","held"): raise KoboError("現在の状態では選択操作できません")
        if action in ("select","revise"):
            if not candidate_id: raise KoboError("候補IDが必要です")
            candidate_id=self.candidate(candidate_id,session["work_id"],session["session_id"])["candidate_id"]
        path=None
        if instruction_path:
            path=safe_path(self.orchestrator.config.root,instruction_path,must_exist=True)
            if path.suffix.lower() not in (".md",".json"): raise KoboError("修正指示はUTF-8 MarkdownまたはJSONにしてください")
            path.read_text(encoding="utf-8")
        if action=="revise" and not path: raise KoboError("修正指示ファイルが必要です")
        timestamp=now()
        with self.orchestrator.connection() as db:
            db.execute("INSERT INTO concept_actions(session_id,action,candidate_id,instruction_path,note,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],action,candidate_id,str(path) if path else None,note,timestamp))
            state={"select":"selected","hold":"held","reject_all":"rejected","revise":"selected","regenerate":"superseded"}[action]
            db.execute("UPDATE concept_sessions SET status=?,updated_at=? WHERE session_id=?",(state,timestamp,session["session_id"]))
        if action=="regenerate": return self.start(session["work_id"],session["candidate_count"])
        return self.status(session["work_id"],session["session_id"])

    def history(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: return [dict(r) for r in db.execute("SELECT * FROM concept_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]

    def _selected(self,session):
        with self.orchestrator.connection() as db: action=db.execute("SELECT * FROM concept_actions WHERE session_id=? AND action IN ('select','revise') ORDER BY id DESC LIMIT 1",(session["session_id"],)).fetchone()
        if not action: raise KoboError("利用者による候補選択が必要です")
        return action,self.candidate(action["candidate_id"],session["work_id"],session["session_id"])

    def _render(self,session,version,final):
        action,candidate=self._selected(session); revision="なし"
        if action["instruction_path"]: revision=Path(action["instruction_path"]).read_text(encoding="utf-8")
        return f"# 作品企画（CONCEPT）\n\n- 版: {version}\n- 状態: {'確定' if final else 'プレビュー'}\n- work_id: `{session['work_id']}`\n- 参照URS: `{session['urs_path']}`（v{session['urs_version']:03d}、固定）\n- 選択候補: `{candidate['candidate_id']}`\n- 選択根拠: 利用者の明示選択。AI推奨のみでは確定していない。\n\n## 作品ブリーフ\n\n{candidate['title']}を基礎とする。\n\n{candidate['content']}\n\n## 利用者の修正指示\n\n{revision}\n\n## 必須・禁止条件\n\n確定URSの必須・禁止条件を変更しない。\n\n## 仮決定・未決事項・リスク\n\n候補成果物に記載された仮定とリスクを次工程で検証する。\n\n## 次工程入力\n\n参照URSと本CONCEPTのパスを入力とし、未実装の作品設計工程へ引き渡す。本文生成はまだ実行しない。\n"

    def preview(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/"CONCEPT.preview.md"; atomic_write(path,self._render(session,0,False)); return {"path":str(path),"status":"preview"}

    def finalize(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]=="final": raise KoboError("同じ企画セッションの二重確定を拒否しました")
        action,candidate=self._selected(session)
        with self.orchestrator.connection() as db: version=db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM concept_documents d JOIN concept_sessions s ON s.session_id=d.session_id WHERE s.work_id=?",(session["work_id"],)).fetchone()[0]
        path=self.orchestrator.config.store/"works"/session["work_id"]/"concepts"/f"CONCEPT.v{version:03d}.md"
        if path.exists(): raise KoboError("確定CONCEPTの上書きを拒否しました")
        atomic_write(path,self._render(session,version,True)); timestamp=now()
        parent=session["source_mail_id"]; mail_id=self.orchestrator.mail.send("planner","story-architect",f"CONCEPT確定 work_id={session['work_id']} concept_path={path} 次工程=ストーリーバイブル設計",parent_message_id=parent) if parent else None
        with self.orchestrator.connection() as db:
            db.execute("INSERT INTO concept_documents(session_id,version,path,candidate_id,urs_path,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],version,str(path),candidate["candidate_id"],session["urs_path"],timestamp)); db.execute("UPDATE concept_sessions SET status='final',updated_at=? WHERE session_id=?",(timestamp,session["session_id"])); db.execute("UPDATE works SET current_agent='planner',next_agent='story-architect',status='pending',updated_at=? WHERE work_id=?",(timestamp,session["work_id"]))
        return {"path":str(path),"version":version,"candidate_id":candidate["candidate_id"],"urs_path":session["urs_path"],"mail_id":mail_id,"next_agent":"story-architect","next_stage_implemented":True}
