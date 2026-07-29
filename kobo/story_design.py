from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path

from .concept import repair_particle_of, text_quality_problems
from .orchestrator import KoboError, atomic_write, now, safe_path


BIBLE_HEADINGS = ("作品の核", "世界のルール", "主要舞台", "主要人物", "人物関係", "組織・勢力", "時系列", "資源・能力・制約", "謎・伏線候補", "テーマと読書体験", "禁止事項・模倣回避", "整合性ルール", "仮定・未決事項・リスク")
PLOT_HEADINGS = ("全体構造", "主要アーク", "序盤", "中盤", "終盤", "章・話のビート", "主人公の変化", "主要人物の変化", "伏線と回収", "葛藤・賭けるもの", "因果関係", "ペーシングと中だるみ対策", "結末", "未決事項・リスク")
BIBLE_AUDIT = ("CONCEPT・URS適合", "世界ルールの明確性", "人物と関係の整合性", "時系列・資源・能力の整合性", "テーマと禁止事項", "模倣リスク", "長編運用可能性")
PLOT_AUDIT = ("CONCEPT・バイブル適合", "因果関係", "主人公の能動性", "アークと人物変化", "先読み欲求", "伏線と回収", "中盤持続性", "結末の納得感", "設定矛盾・模倣リスク")


def uid(prefix: str) -> str: return f"{prefix}-{uuid.uuid4().hex}"


class StoryDesignManager:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator=orchestrator; self.dummy=dummy; self.initialize()

    def initialize(self):
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS story_design_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), concept_document_id INTEGER NOT NULL REFERENCES concept_documents(id), concept_path TEXT NOT NULL, concept_version INTEGER NOT NULL, urs_path TEXT NOT NULL, status TEXT NOT NULL, adapter TEXT NOT NULL, source_mail_id INTEGER, bible_mail_id INTEGER, plot_mail_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_story_design ON story_design_sessions(work_id) WHERE status NOT IN ('completed','failed','superseded');
            CREATE TABLE IF NOT EXISTS story_design_artifacts(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), kind TEXT NOT NULL, revision INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, run_id TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL, status TEXT NOT NULL, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,kind,revision));
            CREATE TABLE IF NOT EXISTS story_design_actions(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), action TEXT NOT NULL, stage TEXT NOT NULL, instruction_path TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS story_design_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES story_design_sessions(session_id), kind TEXT NOT NULL, version INTEGER NOT NULL, path TEXT NOT NULL UNIQUE, source_path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(session_id,kind), UNIQUE(path));
            """)

    def _work(self, work_id=None): return self.orchestrator.get_work(work_id)

    def _session(self, work_id=None, session_id=None):
        work=self._work(work_id)
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT * FROM story_design_sessions WHERE session_id=? AND work_id=?",(session_id,work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM story_design_sessions WHERE work_id=? ORDER BY created_at DESC LIMIT 1",(work["work_id"],)).fetchone()
        if not row: raise KoboError("ストーリー設計セッションが見つかりません")
        return row

    def _latest_concept(self, work_id):
        with self.orchestrator.connection() as db:
            row=db.execute("SELECT d.id,d.version,d.path,d.urs_path FROM concept_documents d JOIN concept_sessions s ON s.session_id=d.session_id WHERE s.work_id=? ORDER BY d.version DESC LIMIT 1",(work_id,)).fetchone()
            # 版付き改訂（CONCEPT.vNNN）がある場合は、より新しい版を正本にする。
            try: amendment=db.execute("SELECT version,path FROM concept_amendments WHERE work_id=? ORDER BY version DESC LIMIT 1",(work_id,)).fetchone()
            except sqlite3.OperationalError: amendment=None
        if not row: raise KoboError("確定済みCONCEPTがありません")
        if amendment and amendment["version"]>row["version"]:
            row=dict(row); row["version"]=amendment["version"]; row["path"]=amendment["path"]
        return row,safe_path(self.orchestrator.config.root,row["path"],must_exist=True)

    def _handoff_mail(self, work_id, concept_path):
        with self.orchestrator.mail.connection() as db:
            row=db.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id='planner' AND recipient_id='story-architect' AND body LIKE ? ORDER BY id DESC LIMIT 1",(f"work-{work_id}",f"%concept_path={concept_path}%")).fetchone()
        return row[0] if row else None

    def start(self, work_id=None):
        work=self._work(work_id); concept,path=self._latest_concept(work["work_id"]); timestamp=now(); session_id=uid("story") ; source=self._handoff_mail(work["work_id"],path)
        with self.orchestrator.connection() as db:
            try: db.execute("INSERT INTO story_design_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(session_id,work["work_id"],concept["id"],str(path),concept["version"],concept["urs_path"],"bible_generating","dummy" if self.dummy else "agy",source,None,None,timestamp,timestamp,None))
            except sqlite3.IntegrityError as error: raise KoboError("この作品には進行中のストーリー設計があります") from error
        return self.resume(work["work_id"],session_id)

    def _dir(self, session): return self.orchestrator.config.store/"works"/session["work_id"]/"story-design"/session["session_id"]
    def _artifact(self, session_id, kind):
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM story_design_artifacts WHERE session_id=? AND kind=? ORDER BY revision DESC LIMIT 1",(session_id,kind)).fetchone()
        return row

    def _dummy(self, session, stage):
        headings=BIBLE_HEADINGS if stage=="bible" else PLOT_HEADINGS
        values={heading:f"{heading}を、固定参照したCONCEPTと上流資料に整合する形で定義する。未決事項は確定せず、後工程の検証点として残す。" for heading in headings}
        if stage=="bible":
            values["主要人物"]="主人公、協力者、対立する立場の人物について、欲求・役割・制約を定義する。"; values["整合性ルール"]="時系列、資源、能力、関係変化は確定版を基準に追跡し、変更理由を残す。"
        else:
            values["全体構造"]="導入・拡大・転換・決着の四段階。各段階で状況と関係を不可逆に変化させる。"; values["ペーシングと中だるみ対策"]="各話に部分決着と次の具体的問題を置き、同型の障害を連続させない。"
        title="ストーリーバイブル草案" if stage=="bible" else "全体プロット草案"
        sources=f"- 参照CONCEPT: `{session['concept_path']}`（v{session['concept_version']:03d}、固定）\n- 参照URS: `{session['urs_path']}`\n- 生成アダプター: `dummy`（実Gemini生成物ではない）"
        return f"# {title}\n\n{sources}\n\n"+"\n\n".join(f"## {h}\n\n{values[h]}" for h in headings)+"\n"

    @staticmethod
    def _split_sections(text, headings):
        """`## 見出し`単位に分解する。最初の見出しより前は前文として返す。"""
        preamble, current, buffer, sections = [], None, [], {}
        for line in text.splitlines():
            match=re.match(r"^##\s+(.+?)\s*$",line)
            if match:
                if current: sections[current]="\n".join(buffer).strip()
                elif buffer: preamble=buffer
                current, buffer = match.group(1).strip(), []
            else: buffer.append(line)
        if current: sections[current]="\n".join(buffer).strip()
        return "\n".join(preamble).strip(), {k:v for k,v in sections.items() if k in headings}

    @staticmethod
    def _join_sections(preamble, sections, headings):
        body="\n\n".join(f"## {h}\n\n{sections[h]}" for h in headings if h in sections)
        return (f"{preamble}\n\n{body}\n" if preamble else f"{body}\n")

    def _validate(self, text, headings, label):
        missing=[h for h in headings if f"## {h}" not in text]
        if missing: raise KoboError(f"{label}の必須項目が不足しています: {missing}")

    def _generate(self, session, stage):
        kind=f"{stage}_draft"; existing=self._artifact(session["session_id"],kind)
        if existing: return existing
        directory=self._dir(session); revision=1; path=directory/f"{kind}.r{revision:03d}.md"; run_id=self.orchestrator._new_run_id(); headings=BIBLE_HEADINGS if stage=="bible" else PLOT_HEADINGS
        source=Path(session["concept_path"]) if stage=="bible" else Path(self._document(session["session_id"],"bible")["path"])
        if self.dummy:
            text=self._dummy(session,stage); self._validate(text,headings,stage)
        else:
            text=self._run_draft(session,stage,source,headings,run_id,directory)
        atomic_write(path,text); timestamp=now(); agent_id="story-architect" if stage=="bible" else "plotter"
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,revision,str(path),run_id,agent_id,"generated",str(source),timestamp))
        return self._artifact(session["session_id"],kind)

    def _draft_prompt(self, session, stage, upstream, headings, feedback=None, include_upstream=False)->str:
        concept=Path(session["concept_path"]).read_text(encoding="utf-8")
        label="ストーリーバイブル" if stage=="bible" else "全体プロット"
        parts=[f"あなたは{label}の設計担当です。確定済みCONCEPTを正本として、{label}草案を作成してください。","",
               "## 絶対条件",
               "- **ファイルを作成・保存しない。** 成果物は標準出力へ直接書き出す。",
               "- 出力はMarkdown本体のみ。前置き、後書き、コードフェンス、報告文、リンクを付けない。",
               "- 小説の本文は書かない。設計資料だけを書く。",
               f"- 見出しは次の{len(headings)}個を、この順で過不足なく `## 見出し` として書く: {' / '.join(headings)}",
               "- 確定済みCONCEPTの内容を変更・否定しない。未確定事項は「仮定・未決事項・リスク」へ書く。",
               "- 日本語本文へハングル、置換文字、制御文字を混入させない。日本語の語の間へ英語の機能語を挟まない。",
               "- 既存作品の固有名詞、人物設定、台詞、場面、事件を直接流用しない。","",
               "## 設計方針",
               "- 内部設計として厳密に確定する。ただし本文で全部説明する前提では書かない。",
               "- 重要な能力、証拠、規則、秘密には、初出の違和感 → 小規模な使用 → 制約の提示 → 本格的な使用 → 真相開示、の順序を割り当てる。",
               "- 各設定について、作者側で確定した事実／各人物が知っていること／読者へ既に示したこと／将来明かすこと、の四層を区別して書く。",
               "- 人物関係と感情を中心に置く。専門知識や制度の説明を主軸にしない。",
               "- 主人公の目的が全体を動かし、各話の解決が目的へ具体的につながる構造にする。",
               "- 敵の動機と行動を一貫させ、簡単な証拠を放置させない。",
               "- 万能な解決手段を作らない。能力には距離、疲労、代償などの制約を与える。","",
               "## 正本（確定CONCEPT。変更禁止）",concept]
        if include_upstream:
            parts += ["","## 直接入力（確定済みバイブル）",upstream]
        if feedback: parts += ["","## 前回出力の書式エラー（必ず直す）",feedback]
        return "\n".join(parts)

    def _run_draft(self, session, stage, source, headings, run_id, directory)->str:
        """草案を実AIで生成する。検証合格した出力だけを正式成果物にする。"""
        agent_id="story-architect" if stage=="bible" else "plotter"
        agent=self.orchestrator.agents[agent_id]; limit=max(1,min(agent.max_attempts,3))
        upstream=source.read_text(encoding="utf-8")
        attempts=directory/"attempts"; attempts.mkdir(parents=True,exist_ok=True)
        feedback=None; failures=[]
        for attempt in range(1,limit+1):
            raw=attempts/f"{stage}-draft-attempt-{attempt}.md"
            prompt=self._draft_prompt(session,stage,upstream,headings,feedback,include_upstream=(stage!="bible"))
            refs={"prompt":prompt,"output_path":str(raw),"model":self.orchestrator.config.models.get(agent_id,agent.model),
                  "run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),
                  "mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,raw)
            text=raw.read_text(encoding="utf-8").strip()
            lines=text.splitlines()
            if len(lines)>=2 and lines[0].startswith("```") and lines[-1].strip()=="```": text="\n".join(lines[1:-1]).strip()
            text,repaired=repair_particle_of(text)
            if repaired: atomic_write(raw.with_name(raw.stem+".repaired.txt"),f"助詞「の」へ戻した`of`の件数: {repaired}")
            try:
                self._validate(text,headings,stage)
                problems=text_quality_problems(text)
                if problems: raise KoboError("文字品質エラー: "+" / ".join(problems))
                return text
            except KoboError as error:
                feedback=str(error); failures.append(f"attempt {attempt}: {error}")
                atomic_write(attempts/f"{stage}-draft-attempt-{attempt}.error.txt",f"{error}\n")
        raise KoboError(f"{stage}草案の実AI生成が{limit}回とも検証に失敗しました（ダミーへ代替しません）: "+" / ".join(failures))

    def _run_audit(self, session, stage, draft, axes, run_id, directory, reviewer, *, extra=None, label=None)->str:
        """監査担当を実AIで実行する。ダミーへ代替しない（instruction-20260729-02 §11/§12）。"""
        agent=self.orchestrator.agents[reviewer]; limit=max(1,min(agent.max_attempts,3))
        draft_text=Path(draft["path"]).read_text(encoding="utf-8")
        attempts=directory/"attempts"; attempts.mkdir(parents=True,exist_ok=True)
        prefix=label or stage
        feedback=None; failures=[]
        for attempt in range(1,limit+1):
            raw=attempts/f"{prefix}-audit-attempt-{attempt}.md"
            prompt=self._audit_prompt(session,stage,draft_text,axes,feedback,extra)
            refs={"prompt":prompt,"output_path":str(raw),"model":self.orchestrator.config.models.get(reviewer,agent.model),
                  "run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),
                  "mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,raw)
            text=raw.read_text(encoding="utf-8").strip()
            lines=text.splitlines()
            if len(lines)>=2 and lines[0].startswith("```") and lines[-1].strip()=="```": text="\n".join(lines[1:-1]).strip()
            text,repaired=repair_particle_of(text)
            if repaired: atomic_write(raw.with_name(raw.stem+".repaired.txt"),f"助詞「の」へ戻した`of`の件数: {repaired}")
            try:
                self._validate_audit(text,axes); return text
            except KoboError as error:
                feedback=str(error); failures.append(f"attempt {attempt}: {error}")
                atomic_write(attempts/f"{stage}-audit-attempt-{attempt}.error.txt",f"{error}\n")
        raise KoboError(f"{stage}の実AI独立監査が{limit}回とも検証に失敗しました（ダミーへ代替しません）: "+" / ".join(failures))

    def _audit_prompt(self, session, stage, draft_text, axes, feedback=None, extra=None):
        concept=Path(session["concept_path"]).read_text(encoding="utf-8")
        parts=[f"あなたは{'ストーリーバイブル' if stage=='bible' else '全体プロット'}の独立監査担当です。草案を固定上流資料へ照合し、監査結果だけを書いてください。","",
               "## 絶対条件",
               "- 草案を書き換えない。作品本文も書かない。監査結果だけを出力する。",
               "- 出力はMarkdown本体のみ。前置き、コードフェンスを付けない。",
               f"- 次の軸を、この順で過不足なく `## 軸名` として書く: {' / '.join(axes)}",
               "- 各軸に「根拠:」「長所:」「弱点:」「改善案:」「判定:」の5行を必ず含める。",
               "- 「根拠:」は草案中の具体的な記述を引用または要約して示す。一般論だけで書かない。",
               "- 「判定:」は ok / warn / stop のいずれか1語だけを書く。",
               "- 最後に `## 監査結論` を置き、重大矛盾の有無と、利用者承認前に解消すべき点を3件以内でまとめる。",
               "- 確定可否は利用者承認に委ねる。監査だけで確定しないと明記する。",
               "- 日本語本文へハングル、置換文字、制御文字を混入させない。日本語の語の間へ英語の機能語を挟まない。","",
               "## 重点的に確認する点",
               "- 主人公の目的が全体を動かしているか。各話の解決が目的へ影響するか。",
               "- 敵の動機と行動が一貫し、故意の関与が過失へ後退していないか。",
               "- 人物の立場の変化が段階的か。救命直後に全面的な味方になっていないか。",
               "- 能力・証拠・規則に先行提示と制約があるか。後出しの能力や都合のよい偶然がないか。",
               "- 人物関係が専門設定より中心にあるか。",
               "- 固有名、年齢、地理、時系列、制度に矛盾がないか。",
               "- 既存作品の直接模倣がないか。","",
               "## 固定CONCEPT（正本）",concept,"",
               "## 監査対象の草案",draft_text]
        if feedback: parts += ["","## 前回出力の書式エラー（必ず直す）",feedback]
        return "\n".join(parts)

    def _validate_audit(self, text, axes):
        found={m.group(1).strip() for m in re.finditer(r"^##\s+(.+?)\s*$",text,re.MULTILINE)}
        missing=[axis for axis in axes if axis not in found]
        if missing: raise KoboError(f"監査の必須軸が不足しています: {missing}")
        for label in ("根拠","長所","弱点","改善案"):
            if len(re.findall(rf"^{label}\s*[:：]",text,re.MULTILINE)) < len(axes):
                raise KoboError(f"各軸に「{label}:」を書いてください")
        if len(re.findall(r"^判定\s*[:：]\s*(ok|warn|stop)",text,re.MULTILINE|re.IGNORECASE)) < len(axes):
            raise KoboError("各軸に「判定: ok / warn / stop」を書いてください")
        problems=text_quality_problems(text)
        if problems: raise KoboError("文字品質エラー: "+" / ".join(problems))

    def _audit(self, session, stage):
        kind=f"{stage}_audit"; existing=self._artifact(session["session_id"],kind)
        if existing:return existing
        draft=self._artifact(session["session_id"],f"{stage}_draft"); directory=self._dir(session); path=directory/f"{kind}.r001.md"; run_id=self.orchestrator._new_run_id(); axes=BIBLE_AUDIT if stage=="bible" else PLOT_AUDIT; reviewer="continuity-reviewer" if stage=="bible" else "plot-reviewer"
        header=f"# {'ストーリーバイブル' if stage=='bible' else '全体プロット'}独立監査\n\n- 監査担当: `{reviewer}`\n- 生成種別: {'テスト用ダミー。承認判断に使えない' if self.dummy else '実Antigravity'}\n- アダプター: `{session['adapter']}`\n- 生成実行ID: `{draft['run_id']}`\n- 監査実行ID: `{run_id}`\n\n"
        if self.dummy:
            body=header+"\n\n".join(f"## {axis}\n\n根拠: 固定上流資料と草案を独立照合。\n\n長所: 構造化され、後工程から参照できる。\n\n弱点・リスク: 未決事項の追跡が必要。\n\n改善案: 確定前に矛盾と仮定を明示する。\n\n判定: ok" for axis in axes)+"\n\n## 監査結論\n\n確定可否は利用者承認に委ねる。監査だけでは確定しない。\n"
        else:
            body=header+self._run_audit(session,stage,draft,axes,run_id,directory,reviewer)+"\n"
        atomic_write(path,body); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],kind,1,str(path),run_id,reviewer,"completed",draft["path"],timestamp))
        return self._artifact(session["session_id"],kind)

    def _document(self, session_id, kind):
        with self.orchestrator.connection() as db: row=db.execute("SELECT * FROM story_design_documents WHERE session_id=? AND kind=?",(session_id,kind)).fetchone()
        return row

    REAUDIT_CHECKS = (
        "ギルベルトが命令違反を認識した上で葛藤しているか。",
        "救命直後に主人公を全面信用していないか。",
        "「ただのスープ」が都合のよい法的解決になっていないか。",
        "黒猫の名前が全箇所で「ミルラ」に統一されているか。「ルナ」が残っていないか。",
        "刺客の連続襲撃が連載の基本構造になっていないか。",
        "調合、採取、事件解決、薬草小屋の発展が通常回の中心にあるか。",
        "敵の妨害が政治、流通、評判、証拠隠蔽へ分散されているか。",
        "CONCEPT.v001.mdの確定事項を変更していないか。",
    )

    @staticmethod
    def _extract_checks(instruction_path):
        """改訂指示の「再監査の重点項目」節を、再監査プロンプトの確認項目として取り込む。"""
        text=Path(instruction_path).read_text(encoding="utf-8")
        match=re.search(r"^#+\s*再監査の重点項目\s*$(.*?)(?=^#+\s|\Z)",text,re.MULTILINE|re.DOTALL)
        if not match: return ()
        return tuple(line.strip()[2:].strip() for line in match.group(1).splitlines() if line.strip().startswith("- "))

    def _bible_revision_prompt(self, session, draft_text, audit_text, instructions, headings, feedback=None, preserve=())->str:
        concept=Path(session["concept_path"]).read_text(encoding="utf-8")
        parts=["あなたはストーリーバイブルの設計担当です。既存の草案を、確定した改訂指示に沿って改訂してください。","",
               "## 絶対条件",
               "- **ファイルを作成・保存しない。** 成果物は標準出力へ直接書き出す。",
               "- 出力はMarkdown本体のみ。前置き、後書き、コードフェンス、報告文を付けない。",
               "- 小説の本文は書かない。設計資料だけを書く。",
               f"- 見出しは次の{len(headings)}個を、この順で過不足なく `## 見出し` として書く: {' / '.join(headings)}",
               "- 確定済みCONCEPTの確定事項を変更・否定しない。",
               "- 改訂指示に書かれた3点は必ず反映する。",
               "- 作り直しではなく改訂である。指示された箇所以外の設計を不必要に作り替えない。",
               "- 日本語本文へハングル、置換文字、制御文字を混入させない。日本語の語の間へ英語の機能語を挟まない。",
               "- 名詞と名詞をつなぐ位置へ英語の前置詞を書かない。日本語の連体助詞を使う。この規則自体を本文へ書き写さない。",
               "- 既存作品の固有名詞、人物設定、台詞、場面、事件を直接流用しない。","",
               "## 改訂指示（確定事項。最優先）",instructions,"",
               "## 正本（確定CONCEPT。変更禁止）",concept,"",
               "## 改訂対象の草案（r001）",draft_text,"",
               "## 前回の独立監査結果（この指摘を踏まえて直す）",audit_text]
        if preserve:
            parts += ["","## 変更してはいけない見出し",
                      "次の見出しは既存草案の本文をそのまま維持する。書き換え、要約、加筆をしない。",
                      *[f"- {h}" for h in preserve],
                      "これら以外の見出しだけを改訂指示に沿って直す。"]
        if feedback: parts += ["","## 前回出力の書式エラー（必ず直す）",feedback]
        return "\n".join(parts)

    def _quality_repair_prompt(self, problems, previous)->str:
        """文字混入だけを直させる。内容・人物・構造は変更させない。"""
        return "\n".join([
            "次のMarkdownは、日本語本文へ不正な文字が混入しているため不合格です。",
            "",
            "## 検出箇所",problems,
            "",
            "## 修復の指示",
            "- **ファイルを作成・保存しない。** 修復後の全文を標準出力へ書き出す。",
            "- 設計内容、登場人物、構造、見出し、順序を一切変更しない。",
            "- 検出された混入文字だけを、文脈に合う自然な日本語へ直す。",
            "- 名詞間の英語前置詞は、日本語の連体助詞へ戻す。",
            "- 修復の指示文そのものを本文へ書き写さない。",
            "- 新しい設定や表現を足さない。",
            "- 出力はMarkdown本体のみ。前置き、コードフェンス、説明を付けない。",
            "",
            "## 修復対象（この全文を直して返す）",previous,
        ])

    def _run_bible_revision(self, session, draft, audit, instruction_path, run_id, directory, headings, preserve=(), revision=None)->str:
        agent=self.orchestrator.agents["story-architect"]; limit=max(1,min(agent.max_attempts,3))
        draft_text=Path(draft["path"]).read_text(encoding="utf-8")
        audit_text=Path(audit["path"]).read_text(encoding="utf-8")
        instructions=Path(instruction_path).read_text(encoding="utf-8").strip()
        attempts=directory/"attempts"; attempts.mkdir(parents=True,exist_ok=True)
        feedback=None; failures=[]; repair_source=None
        for attempt in range(1,limit+1):
            raw=attempts/f"bible-r{revision:03d}-revise-attempt-{attempt}.md" if revision else attempts/f"bible-revise-attempt-{attempt}.md"
            # 文字混入だけが原因なら、作り直させず前回出力の修復を求める。
            prompt=(self._quality_repair_prompt(feedback,repair_source) if repair_source
                    else self._bible_revision_prompt(session,draft_text,audit_text,instructions,headings,feedback,preserve))
            refs={"prompt":prompt,"output_path":str(raw),"model":self.orchestrator.config.models.get("story-architect",agent.model),
                  "run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),
                  "mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,raw)
            text=raw.read_text(encoding="utf-8").strip()
            lines=text.splitlines()
            if len(lines)>=2 and lines[0].startswith("```") and lines[-1].strip()=="```": text="\n".join(lines[1:-1]).strip()
            text,repaired=repair_particle_of(text)
            if repaired: atomic_write(raw.with_name(raw.stem+".repaired.txt"),f"助詞「の」へ戻した`of`の件数: {repaired}")
            try:
                self._validate(text,headings,"bible")
                if preserve:
                    # 変更対象外の見出しは、モデルの転記に頼らず原文を機械的に差し戻す。
                    keep=self._split_sections(draft_text,headings)[1]
                    preamble,sections=self._split_sections(text,headings)
                    for heading in preserve:
                        if heading in keep: sections[heading]=keep[heading]
                    text=self._join_sections(preamble,sections,headings)
                    self._validate(text,headings,"bible")
                problems=text_quality_problems(text)
                if problems: raise KoboError("文字品質エラー: "+" / ".join(problems))
                return text
            except KoboError as error:
                feedback=str(error); failures.append(f"attempt {attempt}: {error}")
                # 見出し欠落など構造的な失敗は作り直し、文字混入は修復パスへ切り替える。
                repair_source=text if ("文字品質エラー" in feedback and text) else None
                atomic_write(attempts/f"bible-revise-attempt-{attempt}.error.txt",f"{error}\n")
        raise KoboError(f"バイブル改訂の実AI生成が{limit}回とも検証に失敗しました（ダミーへ代替しません）: "+" / ".join(failures))

    REBASE_CHECKS = (
        "CONCEPT.v002とバイブルで黒猫の名前が一致しているか（ミルラ）。",
        "CONCEPT v001からの変更が、黒猫の名前と敵の妨害トーンの2点だけか。",
        "武官が治療禁止違反を認識した上で処分を保留しているか。",
        "通常回が刺客との戦闘中心になっていないか。",
        "辺境での実績と証拠が宮廷復帰へ届く具体的な経路（記録者、運搬者、受理組織、妨害点、簡単に復権できない理由）があるか。",
        "主要な薬草・毒物・魔獣の固有名に明白な模倣リスクがないか。",
        "後出し能力や万能治療がないか。",
    )

    def _sha256(self, path)->str:
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _rebase_prompt(self, session, concept_text, draft_text, audit_text, instructions, headings, feedback=None)->str:
        parts=["あなたはストーリーバイブルの設計担当です。正本CONCEPTが新しい版へ差し替わりました。既存の草案を新しい正本へ整合させてください。","",
               "## 絶対条件",
               "- **ファイルを作成・保存しない。** 成果物は標準出力へ直接書き出す。",
               "- 出力はMarkdown本体のみ。前置き、後書き、コードフェンス、報告文を付けない。",
               "- 小説の本文は書かない。設計資料だけを書く。",
               f"- 見出しは次の{len(headings)}個を、この順で過不足なく `## 見出し` として書く: {' / '.join(headings)}",
               "- 新しい正本CONCEPT（v002）の確定事項を変更・否定しない。",
               "- **既存草案で既に改善済みの内容を後退させない。** 特に、武官が治療禁止違反を認識した上で処分を保留する葛藤と、敵の妨害を政治・流通・評判・証拠隠蔽へ分散した連載トーンは維持する。",
               "- 黒猫の名前は全箇所「ミルラ」に統一する。「ルナ」を残さない。ただし敵役「ベルナール」は変更しない。",
               "- 日本語本文へハングル、置換文字、制御文字を混入させない。日本語の語の間へ英語の機能語を挟まない。",
               "- 名詞と名詞をつなぐ位置へ英語の前置詞を書かない。日本語の連体助詞を使う。この規則自体を本文へ書き写さない。",
               "- 既存作品の固有名詞、人物設定、台詞、場面、事件を直接流用しない。","",
               "## 再接続指示（確定事項。最優先）",instructions,"",
               "## 新しい正本（CONCEPT v002。変更禁止）",concept_text,"",
               "## 基礎にする既存草案（r002）",draft_text,"",
               "## 直前の独立監査結果（残る指摘も解消する）",audit_text]
        if feedback: parts += ["","## 前回出力の書式エラー（必ず直す）",feedback]
        return "\n".join(parts)

    def _run_bible_rebase(self, session, concept_path, draft, audit, instruction_path, run_id, directory, headings, revision=None)->str:
        agent=self.orchestrator.agents["story-architect"]; limit=max(1,min(agent.max_attempts,3))
        concept_text=Path(concept_path).read_text(encoding="utf-8")
        draft_text=Path(draft["path"]).read_text(encoding="utf-8")
        audit_text=Path(audit["path"]).read_text(encoding="utf-8")
        instructions=Path(instruction_path).read_text(encoding="utf-8").strip()
        attempts=directory/"attempts"; attempts.mkdir(parents=True,exist_ok=True)
        feedback=None; failures=[]; repair_source=None
        for attempt in range(1,limit+1):
            raw=attempts/f"bible-r{revision:03d}-rebase-attempt-{attempt}.md" if revision else attempts/f"bible-rebase-attempt-{attempt}.md"
            prompt=(self._quality_repair_prompt(feedback,repair_source) if repair_source
                    else self._rebase_prompt(session,concept_text,draft_text,audit_text,instructions,headings,feedback))
            refs={"prompt":prompt,"output_path":str(raw),"model":self.orchestrator.config.models.get("story-architect",agent.model),
                  "run_id":run_id,"run_dir":str(directory),"agent_path":str(agent.path),
                  "mail_db":str(self.orchestrator.config.mail_db),"mail_id":str(session["source_mail_id"] or "none")}
            self.orchestrator._adapter(agent).execute(agent,refs,raw)
            text=raw.read_text(encoding="utf-8").strip()
            lines=text.splitlines()
            if len(lines)>=2 and lines[0].startswith("```") and lines[-1].strip()=="```": text="\n".join(lines[1:-1]).strip()
            text,repaired=repair_particle_of(text)
            if repaired: atomic_write(raw.with_name(raw.stem+".repaired.txt"),f"助詞「の」へ戻した`of`の件数: {repaired}")
            try:
                self._validate(text,headings,"bible")
                problems=text_quality_problems(text)
                if problems: raise KoboError("文字品質エラー: "+" / ".join(problems))
                if re.search(r"(?<!ベ)(?<!ベル)ルナ(?!ール)",text):
                    raise KoboError("黒猫の名前が「ミルラ」へ統一されていません（「ルナ」が残っています）")
                return text
            except KoboError as error:
                feedback=str(error); failures.append(f"attempt {attempt}: {error}")
                repair_source=text if ("文字品質エラー" in feedback and text) else None
                atomic_write(attempts/f"bible-rebase-attempt-{attempt}.error.txt",f"{error}\n")
        raise KoboError(f"バイブル再接続の実AI生成が{limit}回とも検証に失敗しました（ダミーへ代替しません）: "+" / ".join(failures))

    def rebase_bible(self, work_id=None, session_id=None, *, concept=None, instructions=None):
        """正本CONCEPTを新しい版へ差し替え、草案を整合させて独立再監査する。承認・確定はしない。"""
        session=self._session(work_id,session_id)
        retryable=session["status"]=="failed" and self._artifact(session["session_id"],"bible_draft") and not self._document(session["session_id"],"bible")
        if session["status"]!="bible_awaiting_approval" and not retryable:
            raise KoboError("承認待ちのバイブルだけを再接続できます")
        if not concept: raise KoboError("新しいCONCEPTのパスが必要です")
        if not instructions: raise KoboError("再接続指示ファイルが必要です")
        concept_path=safe_path(self.orchestrator.config.root,concept,must_exist=True)
        instruction_path=safe_path(self.orchestrator.config.root,instructions,must_exist=True)
        if str(concept_path)==str(session["concept_path"]): raise KoboError("現在と同じCONCEPTへは再接続できません")
        draft=self._artifact(session["session_id"],"bible_draft"); audit=self._artifact(session["session_id"],"bible_audit")
        if not draft or not audit: raise KoboError("再接続の基礎になる草案と監査がそろっていません")
        old_path=Path(session["concept_path"]); old_version=session["concept_version"]
        new_version=self._concept_version(concept_path,old_version)
        directory=self._dir(session); revision=draft["revision"]+1
        path=directory/f"bible_draft.r{revision:03d}.md"; audit_path=directory/f"bible_audit.r{revision:03d}.md"
        if path.exists() or audit_path.exists(): raise KoboError("既存改訂版の上書きを拒否しました")
        try:
            run_id=self.orchestrator._new_run_id()
            if self.dummy:
                text=self._dummy(session,"bible"); self._validate(text,BIBLE_HEADINGS,"bible")
            else:
                text=self._run_bible_rebase(session,concept_path,draft,audit,instruction_path,run_id,directory,BIBLE_HEADINGS,revision)
            atomic_write(path,text); timestamp=now()
            note=(f"concept_rebase: {old_path.name}(v{old_version:03d}, sha256={self._sha256(old_path)}) -> "
                  f"{concept_path.name}(v{new_version:03d}, sha256={self._sha256(concept_path)}) instructions={instruction_path.name}")
            with self.orchestrator.connection() as db:
                db.execute("UPDATE story_design_sessions SET concept_path=?,concept_version=?,updated_at=? WHERE session_id=?",
                           (str(concept_path),new_version,timestamp,session["session_id"]))
                db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (session["session_id"],"bible_draft",revision,str(path),run_id,"story-architect","rebased",str(draft["path"]),timestamp))
                db.execute("INSERT INTO story_design_actions(session_id,action,stage,instruction_path,created_at) VALUES(?,?,?,?,?)",
                           (session["session_id"],"concept_rebase","bible",note,timestamp))
                db.execute("INSERT INTO story_design_actions(session_id,action,stage,instruction_path,created_at) VALUES(?,?,?,?,?)",
                           (session["session_id"],"rebase","bible",str(instruction_path),timestamp))
            session=self._session(session["work_id"],session["session_id"])
            rebased=self._artifact(session["session_id"],"bible_draft")
            audit_run_id=self.orchestrator._new_run_id()
            if self.dummy:
                body=("# ストーリーバイブル独立監査\n\n- 監査担当: `continuity-reviewer`\n- 生成種別: テスト用ダミー。承認判断に使えない\n\n"
                      +"\n\n".join(f"## {axis}\n\n根拠: 再接続草案を新正本へ独立照合。\n\n長所: 整合している。\n\n弱点・リスク: 追跡が必要。\n\n改善案: 明示する。\n\n判定: ok" for axis in BIBLE_AUDIT)
                      +"\n\n## 監査結論\n\n確定可否は利用者承認に委ねる。監査だけでは確定しない。\n")
            else:
                header=(f"# ストーリーバイブル独立監査\n\n- 監査担当: `continuity-reviewer`\n- 生成種別: 実Antigravity\n"
                        f"- アダプター: `{session['adapter']}`\n- 対象草案: 第{revision}版\n- 参照CONCEPT: `{concept_path.name}`（v{new_version:03d}）\n"
                        f"- 生成実行ID: `{run_id}`\n- 監査実行ID: `{audit_run_id}`\n\n")
                body=header+self._run_audit(session,"bible",rebased,BIBLE_AUDIT,audit_run_id,directory,"continuity-reviewer",
                                            extra=self.REBASE_CHECKS,label=f"bible-r{revision:03d}")+"\n"
            atomic_write(audit_path,body); timestamp=now()
            with self.orchestrator.connection() as db:
                db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (session["session_id"],"bible_audit",revision,str(audit_path),audit_run_id,"continuity-reviewer","completed",str(path),timestamp))
                db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',error=NULL,updated_at=? WHERE session_id=?",(timestamp,session["session_id"]))
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def _concept_version(self, concept_path, fallback):
        match=re.search(r"CONCEPT\.v(\d+)\.md$",Path(concept_path).name)
        return int(match.group(1)) if match else fallback+1

    def revise_bible(self, work_id=None, session_id=None, *, instructions=None, preserve=()):
        """承認待ちのバイブル草案を版付きで改訂し、独立再監査まで行う。承認・確定はしない。"""
        session=self._session(work_id,session_id)
        # 改訂が途中で失敗したセッションは、成果物を何も確定していないため再試行を許す。
        retryable=session["status"]=="failed" and self._artifact(session["session_id"],"bible_draft") and self._artifact(session["session_id"],"bible_audit") and not self._document(session["session_id"],"bible")
        if session["status"]!="bible_awaiting_approval" and not retryable:
            raise KoboError("承認待ちのバイブルだけを改訂できます")
        if not instructions: raise KoboError("改訂指示ファイルが必要です")
        instruction_path=safe_path(self.orchestrator.config.root,instructions,must_exist=True)
        if instruction_path.suffix.lower() not in (".md",".json"): raise KoboError("改訂指示はUTF-8 MarkdownまたはJSONにしてください")
        draft=self._artifact(session["session_id"],"bible_draft"); audit=self._artifact(session["session_id"],"bible_audit")
        if not draft or not audit: raise KoboError("改訂対象の草案と監査がそろっていません")
        directory=self._dir(session); revision=draft["revision"]+1
        path=directory/f"bible_draft.r{revision:03d}.md"; audit_path=directory/f"bible_audit.r{revision:03d}.md"
        if path.exists() or audit_path.exists(): raise KoboError("既存改訂版の上書きを拒否しました")
        try:
            run_id=self.orchestrator._new_run_id()
            if self.dummy:
                text=self._dummy(session,"bible"); self._validate(text,BIBLE_HEADINGS,"bible")
            else:
                text=self._run_bible_revision(session,draft,audit,instruction_path,run_id,directory,BIBLE_HEADINGS,preserve,revision)
            atomic_write(path,text); timestamp=now()
            with self.orchestrator.connection() as db:
                db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (session["session_id"],"bible_draft",revision,str(path),run_id,"story-architect","revised",str(draft["path"]),timestamp))
                db.execute("INSERT INTO story_design_actions(session_id,action,stage,instruction_path,created_at) VALUES(?,?,?,?,?)",
                           (session["session_id"],"revise","bible",str(instruction_path),timestamp))
            revised=self._artifact(session["session_id"],"bible_draft")
            audit_run_id=self.orchestrator._new_run_id()
            if self.dummy:
                body=(f"# ストーリーバイブル独立監査\n\n- 監査担当: `continuity-reviewer`\n- 生成種別: テスト用ダミー。承認判断に使えない\n"
                      f"- アダプター: `{session['adapter']}`\n- 生成実行ID: `{run_id}`\n- 監査実行ID: `{audit_run_id}`\n\n"
                      +"\n\n".join(f"## {axis}\n\n根拠: 改訂草案と固定上流資料を独立照合。\n\n長所: 指摘が反映されている。\n\n弱点・リスク: 未決事項の追跡が必要。\n\n改善案: 確定前に仮定を明示する。\n\n判定: ok" for axis in BIBLE_AUDIT)
                      +"\n\n## 監査結論\n\n確定可否は利用者承認に委ねる。監査だけでは確定しない。\n")
            else:
                header=(f"# ストーリーバイブル独立監査\n\n- 監査担当: `continuity-reviewer`\n- 生成種別: 実Antigravity\n"
                        f"- アダプター: `{session['adapter']}`\n- 対象草案: 第{revision}版\n- 生成実行ID: `{run_id}`\n- 監査実行ID: `{audit_run_id}`\n\n")
                checks=self._extract_checks(instruction_path) or self.REAUDIT_CHECKS
                body=header+self._run_audit(session,"bible",revised,BIBLE_AUDIT,audit_run_id,directory,"continuity-reviewer",
                                            extra=checks,label=f"bible-r{revision:03d}")+"\n"
            atomic_write(audit_path,body); timestamp=now()
            with self.orchestrator.connection() as db:
                db.execute("INSERT INTO story_design_artifacts(session_id,kind,revision,path,run_id,agent_id,status,source_path,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (session["session_id"],"bible_audit",revision,str(audit_path),audit_run_id,"continuity-reviewer","completed",str(path),timestamp))
                # 承認はしない。再び利用者承認待ちへ戻す。
                db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',error=NULL,updated_at=? WHERE session_id=?",(timestamp,session["session_id"]))
        except Exception as error:
            # r002を保存していないので、承認待ちへ戻して再試行できる状態を保つ。
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def resume(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        status=session["status"]
        if status=="failed":
            # 失敗したセッションを再開可能にする。確定済みバイブルの有無で工程を判定する。
            status="plot_generating" if self._document(session["session_id"],"bible") else "bible_generating"
        try:
            if status in ("bible_generating","bible_review"):
                self._generate(session,"bible"); self._audit(session,"bible")
                with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='bible_awaiting_approval',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                if session["source_mail_id"]:
                    mail_id=self.orchestrator.mail.send("story-architect","continuity-reviewer",f"バイブル監査完了 session_id={session['session_id']} 状態=承認待ち",parent_message_id=session["source_mail_id"])
                    with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET bible_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
            elif status in ("plot_generating","plot_review"):
                self._generate(session,"plot"); self._audit(session,"plot")
                with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='plot_awaiting_approval',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
                if session["bible_mail_id"]:
                    mail_id=self.orchestrator.mail.send("plotter","plot-reviewer",f"プロット監査完了 session_id={session['session_id']} 状態=承認待ち",parent_message_id=session["bible_mail_id"])
                    with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET plot_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session["session_id"]))
            raise
        return self.status(session["work_id"],session["session_id"])

    def status(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: artifacts=[dict(r) for r in db.execute("SELECT * FROM story_design_artifacts WHERE session_id=? ORDER BY id",(session["session_id"],))]; actions=[dict(r) for r in db.execute("SELECT * FROM story_design_actions WHERE session_id=? ORDER BY id",(session["session_id"],))]; docs=[dict(r) for r in db.execute("SELECT * FROM story_design_documents WHERE session_id=? ORDER BY id",(session["session_id"],))]
        return {"session_id":session["session_id"],"work_id":session["work_id"],"status":session["status"],"adapter":session["adapter"],"concept_path":session["concept_path"],"concept_version":session["concept_version"],"artifacts":artifacts,"actions":actions,"documents":docs,"error":session["error"]}

    def show(self,kind,work_id=None,session_id=None):
        if kind not in ("bible_draft","bible_audit","plot_draft","plot_audit","bible","plot"): raise KoboError("成果物種別が不正です")
        session=self._session(work_id,session_id); row=self._document(session["session_id"],kind) if kind in ("bible","plot") else self._artifact(session["session_id"],kind)
        if not row: raise KoboError("成果物がまだありません")
        result=dict(row); result["content"]=Path(row["path"]).read_text(encoding="utf-8"); return result

    def approve(self,stage,work_id=None,session_id=None):
        if stage not in ("bible","plot"):raise KoboError("承認段階が不正です")
        session=self._session(work_id,session_id); expected=f"{stage}_awaiting_approval"
        if session["status"]!=expected:raise KoboError("現在の状態では承認できません")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_actions(session_id,action,stage,created_at) VALUES(?,?,?,?)",(session["session_id"],"approve",stage,now())); db.execute("UPDATE story_design_sessions SET status=?,updated_at=? WHERE session_id=?",(f"{stage}_approved",now(),session["session_id"]))
        return self.status(session["work_id"],session["session_id"])

    def _finalize(self,stage,session):
        if session["status"]!=f"{stage}_approved":raise KoboError("利用者承認後にだけ確定できます")
        if self._document(session["session_id"],stage):raise KoboError("同じセッションの二重確定を拒否しました")
        draft=self._artifact(session["session_id"],f"{stage}_draft"); label="STORY_BIBLE" if stage=="bible" else "PLOT"
        with self.orchestrator.connection() as db: version=db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM story_design_documents d JOIN story_design_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND d.kind=?",(session["work_id"],stage)).fetchone()[0]
        path=self.orchestrator.config.store/"works"/session["work_id"]/"story-design"/f"{label}.v{version:03d}.md"
        if path.exists():raise KoboError("確定成果物の上書きを拒否しました")
        header=f"# {'ストーリーバイブル' if stage=='bible' else '全体プロット'}\n\n- 版: {version}\n- 状態: 確定\n- work_id: `{session['work_id']}`\n- 参照CONCEPT: `{session['concept_path']}`（v{session['concept_version']:03d}、固定）\n"
        if stage=="plot": header+=f"- 参照バイブル: `{self._document(session['session_id'],'bible')['path']}`（固定）\n"
        atomic_write(path,header+"\n"+Path(draft["path"]).read_text(encoding="utf-8")); timestamp=now()
        with self.orchestrator.connection() as db: db.execute("INSERT INTO story_design_documents(session_id,kind,version,path,source_path,created_at) VALUES(?,?,?,?,?,?)",(session["session_id"],stage,version,str(path),draft["path"],timestamp)); db.execute("UPDATE story_design_sessions SET status=?,updated_at=? WHERE session_id=?",("bible_final" if stage=="bible" else "completed",timestamp,session["session_id"]))
        return {"path":str(path),"version":version,"stage":stage}

    def finalize_bible(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); result=self._finalize("bible",session); session=self._session(session["work_id"],session["session_id"])
        parent=session["source_mail_id"]
        if parent:
            mail_id=self.orchestrator.mail.send("story-architect","plotter",f"バイブル確定 work_id={session['work_id']} bible_path={result['path']}",parent_message_id=parent); result["mail_id"]=mail_id
            with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET bible_mail_id=? WHERE session_id=?",(mail_id,session["session_id"]))
        return result

    def start_plot(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="bible_final" or not self._document(session["session_id"],"bible"):raise KoboError("確定バイブルが必要です")
        with self.orchestrator.connection() as db: db.execute("UPDATE story_design_sessions SET status='plot_generating',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return self.resume(session["work_id"],session["session_id"])

    def finalize_plot(self,work_id=None,session_id=None):
        session=self._session(work_id,session_id); result=self._finalize("plot",session); session=self._session(session["work_id"],session["session_id"]); parent=session["bible_mail_id"]
        if parent:
            mail_id=self.orchestrator.mail.send("plotter","scene-planner",f"全体プロット確定 work_id={session['work_id']} plot_path={result['path']} 次工程=章・シーン設計",parent_message_id=parent); result["mail_id"]=mail_id
        with self.orchestrator.connection() as db: db.execute("UPDATE works SET current_agent='plotter',next_agent='scene-planner',status='pending',updated_at=? WHERE work_id=?",(now(),session["work_id"]))
        result.update({"next_agent":"scene-planner","next_stage_implemented":True}); return result
