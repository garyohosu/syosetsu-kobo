from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from .orchestrator import KoboError, atomic_write, now


STATUSES = {"confirmed", "provisional", "deferred", "unanswered"}
EVIDENCE = {"user", "known", "ai_inference", "source"}


@dataclass(frozen=True)
class Question:
    question_id: str
    section: str
    text: str
    intent: str
    choices: tuple[tuple[str, str], ...]
    recommendation: str
    reason: str

    def as_dict(self) -> dict:
        return {"question_id": self.question_id, "section": self.section, "text": self.text, "intent": self.intent, "choices": [{"value": value, "impact": impact} for value, impact in self.choices], "recommendation": self.recommendation, "recommendation_reason": self.reason, "allows_defer": True, "allows_free_text": True}


QUESTIONS = (
    Question("work-name", "作品概要", "作品の仮題または呼び名は？", "対話中に作品を識別しやすくします。", (("仮題を付ける", "方向性を言葉で固定しやすい"), ("テーマだけ", "内容を先に固められる")), "テーマだけ", "題名は後から低コストで変更できます。"),
    Question("core-axis", "作品概要", "一番読みたい物語の主軸は？", "中心となる読書体験を決めます。", (("店", "日常と客との交流が中心"), ("生産・開拓", "成果の蓄積を楽しめる"), ("領地・国家経営", "制度と大規模な変化が中心"), ("冒険", "発見と移動が中心")), "生産・開拓", "既知資料では改善と蓄積への嗜好が推定されていますが未確認です。"),
    Question("genre-setting", "ジャンル、舞台、テーマ", "ジャンルと主な舞台は？", "世界観と企画候補の幅を決めます。", (("異世界ファンタジー", "自由な制度・技術設計が可能"), ("現代", "身近さと現実感が強い"), ("SF", "技術と社会変化を扱いやすい")), "異世界ファンタジー", "既知資料との相性がよい仮判断です。"),
    Question("protagonist-growth", "登場人物と関係", "主人公の成長のさせ方は？", "爽快感と試行錯誤の比重を決めます。", (("最初から有能", "活躍を早く楽しめる"), ("知識と工夫で成長", "達成の過程を厚く描ける"), ("弱いが知恵で勝つ", "準備と逆転が中心")), "知識と工夫で成長", "能力と改善の両方を見せられます。"),
    Question("relationships", "登場人物と関係", "重視したい人間関係は？", "感情的な居場所と対立の形を決めます。", (("仲間・共同体", "協力と信頼の蓄積が中心"), ("家族", "親密で継続的な絆が中心"), ("恋愛", "二者関係の変化が中心"), ("主従", "責任と信頼を描きやすい")), "仲間・共同体", "安心できる関係性という推定に合いますが未確認です。"),
    Question("tone-style", "語り方、文体、雰囲気、読後感", "望む雰囲気・文体・読後感は？", "文章密度と感情温度を決めます。", (("軽快で温かい", "読みやすく回復感が強い"), ("静かで余韻重視", "内面と時間感覚を深める"), ("緊張感重視", "危機と先読み欲求を強める")), "軽快で温かい", "長編を負担なく読み続けやすい選択です。"),
    Question("theme-avoid", "必須条件・禁止事項", "扱いたいテーマと避けたい内容は？", "作品の必須条件と安全境界を定めます。", (("改善と居場所", "積み上げと安心感が中心"), ("発見と冒険", "未知への好奇心が中心"), ("社会変革", "制度の変化が中心")), "改善と居場所", "既知資料からのAI推定であり、回答でのみ確定します。"),
    Question("target-reader", "目的と想定読者", "主な想定読者は？", "説明量と前提知識を調整します。", (("自分が楽しむ", "個人の嗜好を最優先"), ("同ジャンル読者", "ジャンル慣習との整合を重視"), ("幅広い読者", "説明と導入を厚くする")), "自分が楽しむ", "小説工房の目的に直接合致します。"),
    Question("length-viewpoint", "構成と分量", "長さ・章構成・視点の希望は？", "制作単位と語りの距離を決めます。", (("長編・章立て・三人称", "世界と複数人物を扱いやすい"), ("連載・話単位・一人称", "主人公への没入が強い"), ("短編・単章", "早く完成形を確認できる")), "長編・章立て・三人称", "長期的な蓄積を描きやすい仮案です。"),
    Question("favorite-elements", "根拠と確度", "特に好きな作品と、それぞれの好きな具体的要素は？", "固有要素を模倣せず、好みを抽象化します。", (("上位作品を自由記入", "明示的な嗜好根拠になる"), ("今後の企画反応から確認", "回答負担を後へ回せる")), "上位作品を自由記入", "作品名だけでなく好きな理由が精度向上に必要です。"),
    Question("success-scope", "確定事項・仮決定", "主人公の成功はどこまで広げたい？", "個人の幸福と社会改善の比重を決めます。", (("本人の快適な生活", "身近な満足を丁寧に描く"), ("周囲の共同体まで", "人間関係と波及を両立"), ("社会全体", "制度と大規模変化が中心")), "周囲の共同体まで", "個人と社会の中間で両方の快感を得やすい案です。"),
    Question("absolute-rules", "必須条件・禁止事項", "絶対に守る条件は？", "企画が外してはいけない合格条件を定めます。", (("安心して読める", "回復不能な苦難を避ける"), ("主人公が能動的", "受け身の展開を避ける"), ("成果が蓄積する", "進展を目に見える形で残す")), "成果が蓄積する", "物語の進展を継続的に感じやすい条件です。"),
)

QUESTION_MAP = {question.question_id: question for question in QUESTIONS}


class UrsManager:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.initialize()

    def initialize(self) -> None:
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS urs_sessions(session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id), status TEXT NOT NULL, question_version TEXT NOT NULL, next_question_id TEXT, urs_status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_mail_id INTEGER);
            CREATE UNIQUE INDEX IF NOT EXISTS one_open_urs_session ON urs_sessions(work_id) WHERE status='active';
            CREATE TABLE IF NOT EXISTS urs_answers(session_id TEXT NOT NULL REFERENCES urs_sessions(session_id), question_id TEXT NOT NULL, question_order INTEGER NOT NULL, question_version TEXT NOT NULL, answer TEXT, status TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(session_id, question_id));
            CREATE TABLE IF NOT EXISTS urs_answer_history(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, question_id TEXT NOT NULL, old_answer TEXT, old_status TEXT, new_answer TEXT, new_status TEXT NOT NULL, evidence TEXT NOT NULL, changed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS urs_documents(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, UNIQUE(session_id, version));
            """)

    def _session(self, work_id: str | None = None, session_id: str | None = None) -> sqlite3.Row:
        work = self.orchestrator.get_work(work_id)
        with self.orchestrator.connection() as db:
            row = db.execute("SELECT * FROM urs_sessions WHERE session_id=? AND work_id=?", (session_id, work["work_id"])).fetchone() if session_id else db.execute("SELECT * FROM urs_sessions WHERE work_id=? ORDER BY created_at DESC LIMIT 1", (work["work_id"],)).fetchone()
        if not row:
            raise KoboError("URSセッションが見つかりません")
        return row

    def start(self, work_id: str | None = None, known: dict[str, str] | None = None) -> dict:
        work = self.orchestrator.get_work(work_id); session_id = f"urs-{uuid.uuid4().hex}"
        timestamp = now(); known = known or {}
        unknown = set(known) - QUESTION_MAP.keys()
        if unknown: raise KoboError(f"未知の質問IDです: {sorted(unknown)}")
        first = next((q.question_id for q in QUESTIONS if q.question_id not in known), None)
        mail_id = self.orchestrator.mail.send("manager", "urs-maker", f"URS対話開始 work_id={work['work_id']} session_id={session_id}", conversation_id=f"work-{work['work_id']}")
        with self.orchestrator.connection() as db:
            try:
                db.execute("INSERT INTO urs_sessions VALUES(?,?,?,?,?,?,?,?,?)", (session_id, work["work_id"], "active", "1", first, "draft", timestamp, timestamp, mail_id))
            except sqlite3.IntegrityError as error:
                raise KoboError("この作品には進行中のURSセッションがあります") from error
            for question_id, answer in known.items():
                order = tuple(QUESTION_MAP).index(question_id) + 1
                db.execute("INSERT INTO urs_answers VALUES(?,?,?,?,?,?,?,?,?)", (session_id, question_id, order, "1", answer, "provisional", "known", timestamp, timestamp))
                db.execute("INSERT INTO urs_answer_history(session_id,question_id,new_answer,new_status,evidence,changed_at) VALUES(?,?,?,?,?,?)", (session_id, question_id, answer, "provisional", "known", timestamp))
        return self.status(work["work_id"], session_id)

    def current(self, work_id: str | None = None, session_id: str | None = None) -> dict | None:
        session = self._session(work_id, session_id)
        question_id = session["next_question_id"]
        return QUESTION_MAP[question_id].as_dict() if question_id else None

    def _next_unanswered(self, db, session_id: str) -> str | None:
        answered = {row[0] for row in db.execute("SELECT question_id FROM urs_answers WHERE session_id=?", (session_id,))}
        return next((q.question_id for q in QUESTIONS if q.question_id not in answered), None)

    def answer(self, question_id: str, answer: str | None, *, status: str = "confirmed", evidence: str = "user", work_id: str | None = None, session_id: str | None = None, revise: bool = False) -> dict:
        session = self._session(work_id, session_id)
        if session["status"] != "active": raise KoboError("完了済みURSセッションは回答できません")
        if question_id not in QUESTION_MAP: raise KoboError(f"未知の質問IDです: {question_id}")
        if status not in STATUSES - {"unanswered"}: raise KoboError(f"回答状態が不正です: {status}")
        if evidence not in EVIDENCE: raise KoboError(f"根拠種別が不正です: {evidence}")
        if status != "deferred" and (answer is None or not answer.strip()): raise KoboError("回答が空です")
        if not revise and question_id != session["next_question_id"]: raise KoboError("現在の質問以外へ回答できません")
        timestamp = now(); order = tuple(QUESTION_MAP).index(question_id) + 1
        with self.orchestrator.connection() as db:
            existing = db.execute("SELECT * FROM urs_answers WHERE session_id=? AND question_id=?", (session["session_id"], question_id)).fetchone()
            if existing and not revise: raise KoboError("同じ質問への二重回答です")
            if revise and not existing: raise KoboError("修正対象の回答がありません")
            if existing:
                db.execute("UPDATE urs_answers SET answer=?,status=?,evidence=?,updated_at=? WHERE session_id=? AND question_id=?", (answer,status,evidence,timestamp,session["session_id"],question_id))
                old_answer, old_status = existing["answer"], existing["status"]
            else:
                db.execute("INSERT INTO urs_answers VALUES(?,?,?,?,?,?,?,?,?)", (session["session_id"],question_id,order,"1",answer,status,evidence,timestamp,timestamp)); old_answer=old_status=None
            db.execute("INSERT INTO urs_answer_history(session_id,question_id,old_answer,old_status,new_answer,new_status,evidence,changed_at) VALUES(?,?,?,?,?,?,?,?)", (session["session_id"],question_id,old_answer,old_status,answer,status,evidence,timestamp))
            next_id = self._next_unanswered(db, session["session_id"])
            db.execute("UPDATE urs_sessions SET next_question_id=?,updated_at=? WHERE session_id=?", (next_id,timestamp,session["session_id"]))
        return self.status(session["work_id"], session["session_id"])

    def status(self, work_id: str | None = None, session_id: str | None = None) -> dict:
        session = self._session(work_id, session_id)
        with self.orchestrator.connection() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM urs_answers WHERE session_id=? ORDER BY question_order", (session["session_id"],))]
        return {"session_id": session["session_id"], "work_id": session["work_id"], "status": session["status"], "urs_status": session["urs_status"], "answered": len(rows), "total": len(QUESTIONS), "progress": round(len(rows)/len(QUESTIONS), 3), "next_question": self.current(session["work_id"], session["session_id"]), "answers": rows}

    def history(self, question_id: str, work_id: str | None = None, session_id: str | None = None) -> list[dict]:
        session = self._session(work_id, session_id)
        if question_id not in QUESTION_MAP: raise KoboError("未知の質問IDです")
        with self.orchestrator.connection() as db: return [dict(row) for row in db.execute("SELECT * FROM urs_answer_history WHERE session_id=? AND question_id=? ORDER BY id", (session["session_id"],question_id))]

    def _render(self, session, version: int, final: bool) -> str:
        with self.orchestrator.connection() as db:
            answers = {row["question_id"]: dict(row) for row in db.execute("SELECT * FROM urs_answers WHERE session_id=?", (session["session_id"],))}
        groups: dict[str,list[str]] = {}
        for question in QUESTIONS:
            row = answers.get(question.question_id)
            if row:
                value = "今は決めない" if row["status"] == "deferred" else row["answer"]
                line = f"- **{question.text}** {value}  \n  状態: `{row['status']}` / 根拠: `{row['evidence']}`"
            else: line = f"- **{question.text}** 未回答  \n  状態: `unanswered` / 根拠: `なし`"
            groups.setdefault(question.section, []).append(line)
        order = ("作品概要","目的と想定読者","ジャンル、舞台、テーマ","登場人物と関係","語り方、文体、雰囲気、読後感","構成と分量","必須条件・禁止事項","確定事項・仮決定","根拠と確度")
        sections = "\n\n".join(f"## {title}\n\n" + "\n".join(groups.get(title,["- 該当回答なし"])) for title in order)
        confirmed = [q.text for q in QUESTIONS if answers.get(q.question_id,{}).get("status")=="confirmed"]
        provisional = [q.text for q in QUESTIONS if answers.get(q.question_id,{}).get("status")=="provisional"]
        unresolved = [q.text for q in QUESTIONS if q.question_id not in answers or answers[q.question_id]["status"]=="deferred"]
        return f"# 基本要求仕様書（URS）\n\n- 版: {version}\n- 状態: {'確定' if final else 'プレビュー'}\n- work_id: `{session['work_id']}`\n- session_id: `{session['session_id']}`\n\n{sections}\n\n## 確定事項\n\n" + "\n".join(f"- {x}" for x in confirmed or ["なし"]) + "\n\n## 仮決定\n\n" + "\n".join(f"- {x}" for x in provisional or ["なし"]) + "\n\n## 未決事項\n\n" + "\n".join(f"- {x}" for x in unresolved or ["なし"]) + "\n\n## 次工程へ渡す入力\n\n企画担当は確定事項を必須条件、仮決定を変更可能な仮説、未決事項を企画比較で確認する論点として扱うこと。ユーザー回答を創作・補完しないこと。\n"

    def preview(self, work_id: str | None = None, session_id: str | None = None) -> dict:
        session = self._session(work_id, session_id); directory = self.orchestrator.config.store / "works" / session["work_id"] / "urs"; path = directory / "URS.preview.md"
        atomic_write(path, self._render(session, 0, False)); return {"path": str(path), "characters": len(path.read_text(encoding="utf-8")), "status": "preview"}

    def finalize(self, work_id: str | None = None, session_id: str | None = None) -> dict:
        session = self._session(work_id, session_id)
        with self.orchestrator.connection() as db:
            version = db.execute("SELECT COALESCE(MAX(d.version),0)+1 FROM urs_documents d JOIN urs_sessions s ON s.session_id=d.session_id WHERE s.work_id=?", (session["work_id"],)).fetchone()[0]
        path = self.orchestrator.config.store / "works" / session["work_id"] / "urs" / f"URS.v{version:03d}.md"
        if path.exists(): raise KoboError("URS確定版の上書きを拒否しました")
        atomic_write(path, self._render(session, version, True)); timestamp=now()
        mail_id = self.orchestrator.mail.send("urs-maker", "planner", f"URS確定 work_id={session['work_id']} urs_path={path}", parent_message_id=session["source_mail_id"])
        with self.orchestrator.connection() as db:
            db.execute("INSERT INTO urs_documents(session_id,version,status,path,created_at) VALUES(?,?,?,?,?)",(session["session_id"],version,"final",str(path),timestamp))
            db.execute("UPDATE urs_sessions SET status='completed',urs_status='final',next_question_id=NULL,updated_at=? WHERE session_id=?",(timestamp,session["session_id"]))
            db.execute("UPDATE works SET next_agent='planner',status='pending',updated_at=? WHERE work_id=?",(timestamp,session["work_id"]))
        return {"path":str(path),"version":version,"mail_id":mail_id,"next_agent":"planner"}
