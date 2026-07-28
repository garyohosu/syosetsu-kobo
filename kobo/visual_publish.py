from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import sqlite3
import uuid
from pathlib import Path

from .agy_image import AgyImageAdapter
from .orchestrator import KoboError, now, safe_path


class VisualPublishError(KoboError): pass


DEFAULTS = {"enabled": True, "target_chars_per_image": 1200, "min_chars_between_images": 700, "max_chars_without_image": 1800, "min_body_images": 3, "max_body_images": 8, "cover_enabled": True, "aspect_ratio": "4:3", "image_format": "png"}


class VisualPublisher:
    def __init__(self, orchestrator, *, dummy: bool = False):
        self.orchestrator, self.dummy = orchestrator, dummy
        self.settings = {**DEFAULTS, **getattr(orchestrator.config, "visual_publish", {})}
        self.initialize()

    def initialize(self):
        with self.orchestrator.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS visual_sessions(
              session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, manuscript_document_id INTEGER,
              chapter_number INTEGER NOT NULL, source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL,
              status TEXT NOT NULL, target_chars_per_image INTEGER NOT NULL, min_chars_between_images INTEGER NOT NULL,
              max_chars_without_image INTEGER NOT NULL, cover_enabled INTEGER NOT NULL, image_adapter TEXT NOT NULL,
              latest_mail_id INTEGER, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS visual_images(
              image_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES visual_sessions(session_id), ordinal INTEGER NOT NULL,
              kind TEXT NOT NULL, anchor_start INTEGER NOT NULL, anchor_end INTEGER NOT NULL, insert_after_paragraph INTEGER NOT NULL,
              scene_summary TEXT NOT NULL, prompt_path TEXT NOT NULL, output_path TEXT NOT NULL, alt_text TEXT NOT NULL,
              caption TEXT, status TEXT NOT NULL, attempt INTEGER NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              UNIQUE(session_id, ordinal));
            CREATE TABLE IF NOT EXISTS visual_documents(
              id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL REFERENCES visual_sessions(session_id), version INTEGER NOT NULL,
              path TEXT NOT NULL UNIQUE, manifest_path TEXT NOT NULL UNIQUE, source_sha256 TEXT NOT NULL, image_count INTEGER NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(session_id));
            """)

    def _root(self): return self.orchestrator.config.root.resolve()
    def _work(self, work_id):
        try: return self.orchestrator.get_work(work_id)
        except KoboError:
            source = self._novel_source(work_id, 1)
            if source:
                work_dir = self.orchestrator.config.store / "works" / work_id
                if work_dir.exists():
                    return {"work_id": work_id, "title": work_id}
                return self.orchestrator.create_work(work_id, f"{work_id} 第一試作", activate=False, first_agent="planner")
            raise

    def _novel_source(self, work_id, chapter):
        path = self._root() / "novels" / work_id / f"CHAPTER-{chapter:03d}.v001.md"
        return path if path.is_file() else None

    def _source(self, work_id, chapter):
        direct = self._novel_source(work_id, chapter)
        if direct: return direct, None
        with self.orchestrator.connection() as db:
            row = db.execute("SELECT id,path FROM manuscript_documents d JOIN manuscript_sessions s ON s.session_id=d.session_id WHERE s.work_id=? AND s.chapter_number=? ORDER BY d.version DESC LIMIT 1", (work_id, chapter)).fetchone()
        if not row: raise VisualPublishError("対象章の確定本文が見つかりません")
        return safe_path(self._root(), row["path"], must_exist=True), row["id"]

    def _session(self, work_id=None, session_id=None):
        with self.orchestrator.connection() as db:
            row = db.execute("SELECT * FROM visual_sessions WHERE session_id=? AND work_id=?", (session_id, work_id)).fetchone() if session_id else db.execute("SELECT * FROM visual_sessions WHERE work_id=? ORDER BY chapter_number DESC LIMIT 1", (work_id,)).fetchone()
        if not row: raise VisualPublishError("挿絵出版セッションが見つかりません")
        return row

    @staticmethod
    def _blocks(text):
        blocks=[]
        for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
            value=block.strip()
            if value: blocks.append(value)
        return blocks

    @staticmethod
    def _reader_blocks(text):
        blocks=[]
        for block in VisualPublisher._blocks(text):
            if block.startswith("## 未解決事項"): break
            blocks.append(block)
        return blocks

    def _plan_items(self, blocks):
        full="\n\n".join(blocks); positions=[]
        scenes=[("工房", ("灰炉", "工房"), "灰炉工房でリオ、ミナ、コゲが吹雪の朝に六台の給湯器停止を受ける場面", "灰炉工房、リオ、ミナ、黒猫コゲ、凍った工具", "吹雪の朝の工房と仕事の危機"),
                ("調査", ("食堂", "診療所"), "食堂または診療所で給湯器を測定し、暮らしへの影響を知る場面", "セラの食堂、または診療所、リオとミナ、測定器", "冷えた食堂または患者を守る診療所"),
                ("帳面", ("帳面", "異音"), "ミナの帳面に残る夜間の吸気音から原因の手掛かりを見つける場面", "ミナの帳面、リオの指、測定記録、工房の作業台", "記録が価値を持つ瞬間"),
                ("調整槽", ("調整槽", "吹雪"), "坂上の圧力調整槽で吹雪の中、三人が漏れを測り応急修理する場面", "圧力調整槽、リオ、ミナ、ガルド、雪と配管", "吹雪の高所での危険な修理"),
                ("スープ", ("スープ", "任命"), "復旧した食堂で温かい根菜と塩漬け肉のスープを囲み、ミナが任命される場面", "木のお椀、根菜と塩漬け肉のスープ、リオ、ミナ、ガルド、コゲ", "復旧後の安心と新しい役割"),
                ("刻印", ("刻印", "使用禁止"), "夜の工房で継手を拭い、王都で使用禁止となった刻印を発見する章末場面", "ランプ、古い真鍮の継手、刻印、リオの手", "静かな工房に過去の謎が現れる")]
        used=[]; cumulative=0
        for label, keys, summary, subject, alt in scenes:
            found=None
            for i,b in enumerate(blocks):
                if i in used: continue
                if any(k in b for k in keys): found=i; break
            if found is None: continue
            before="\n\n".join(blocks[:found+1]); cumulative=len(before)
            positions.append((found,cumulative,label,summary,subject,alt)); used.append(found)
        if len(positions) < int(self.settings["min_body_images"]):
            candidates=[max(0,min(len(blocks)-1,round(len(blocks)*x))) for x in (0.2,0.4,0.6,0.8)]
            for p in candidates:
                if p not in used:
                    positions.append((p, len("\n\n".join(blocks[:p+1])), "scene", blocks[p][:80], "人物と場面の生活描写", "本文の場面"))
                    used.append(p)
        positions.sort(key=lambda x:x[0])
        # Keyword anchors are preferred, but long unillustrated stretches must
        # be filled before the plan is accepted.
        max_gap = int(self.settings["max_chars_without_image"])
        while len(positions) < int(self.settings["max_body_images"]):
            anchors = [x[1] for x in positions]
            boundaries = [0] + anchors + [len(full)]
            gaps = [(boundaries[i + 1] - boundaries[i], boundaries[i], boundaries[i + 1])
                    for i in range(len(boundaries) - 1)]
            gap, start, end = max(gaps)
            if gap <= max_gap:
                break
            target = start + max_gap
            candidates = []
            for p, block in enumerate(blocks):
                if p in used:
                    continue
                cumulative = len("\n\n".join(blocks[:p + 1]))
                if start < cumulative < end:
                    candidates.append((abs(cumulative - target), p, cumulative, block))
            if not candidates:
                break
            _, p, cumulative, block = min(candidates)
            positions.append((p, cumulative, "interval", block[:80], "人物と場面の生活描写", "本文の場面"))
            used.append(p)
            positions.sort(key=lambda x: x[0])
        return positions[:int(self.settings["max_body_images"])]

    def _visual_bible(self, work_id):
        return f"""# CHARACTER_VISUAL_BIBLE v001

## 人物
- リオ・カーデン: 43歳の細身の成人男性。短い灰褐色の髪、落ち着いた目、濃紺の防寒外套、革手袋、測定器と拡大鏡。表情は観察と控えめな疲労。
- ミナ・フェル: 19歳の見習い職人。栗色の髪を簡素に束ね、厚手の作業着と革の工具鞄、ぼろぼろの羊皮紙の帳面。真剣で少し緊張した表情。
- ガルド親方: 腕の太い中年職人。煤のついた作業着と革前掛け、頑固だが不器用に誠実。
- コゲ: 灰炉工房の黒猫。黄色い目、炉や温かい配管のそばを好む。

## 舞台と画風
- 辺境町ベルク、灰炉工房、木漏れ日亭の食堂、診療所、坂上の石造り圧力調整槽。
- 冬の淡い青灰色と炉火の橙色、手描きの児童文学風ファンタジー挿絵。柔らかな水彩と細いインク線、4:3構図。
- 魔導具は真鍮、鉄、ガラス、青白い魔石。生活の道具として描き、過度なSFや派手な戦闘にしない。

## 禁止
- 本文にない人物・出来事・衣装・ロゴ・読めない看板文字・透かし・画像内テキストを追加しない。
"""

    def start(self, chapter_number, work_id):
        work=self._work(work_id); source,document_id=self._source(work_id, chapter_number); raw=source.read_text(encoding="utf-8"); digest=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        with self.orchestrator.connection() as db:
            existing=db.execute("SELECT * FROM visual_sessions WHERE work_id=? AND chapter_number=?",(work_id,chapter_number)).fetchone()
            if existing:
                if existing["status"] != "planned":
                    return self.status(work_id, existing["session_id"])
                old_sid = existing["session_id"]
                db.execute("DELETE FROM visual_images WHERE session_id=?", (old_sid,))
                db.execute("DELETE FROM visual_sessions WHERE session_id=?", (old_sid,))
                old_base = self.orchestrator.config.store / "works" / work_id / "visual" / old_sid
                if old_base.exists():
                    shutil.rmtree(old_base)
            sid=f"visual-{uuid.uuid4().hex}"; base=self.orchestrator.config.store/"works"/work_id/"visual"/sid; base.mkdir(parents=True,exist_ok=True)
            (base/"source.md").write_text(raw,encoding="utf-8"); (base/"CHARACTER_VISUAL_BIBLE.v001.md").write_text(self._visual_bible(work_id),encoding="utf-8")
            blocks=self._reader_blocks(raw); positions=self._plan_items(blocks); items=[]
            if self.settings["cover_enabled"]: items.append({"image_id":f"{sid}-cover","kind":"cover","ordinal":0,"anchor_start":0,"anchor_end":0,"insert_after_paragraph":-1,"scene_summary":"作品の表紙。雪の辺境町と灰炉工房を描く","subject":"灰炉工房、雪のベルク、遠景の炉火","alt_text":"雪の辺境町ベルクと灰炉工房","caption":""})
            for ordinal,(paragraph,cumulative,label,summary,subject,alt) in enumerate(positions,1): items.append({"image_id":f"{sid}-{ordinal:03d}","kind":"body","ordinal":ordinal,"anchor_start":max(0,cumulative-len(blocks[paragraph])),"anchor_end":cumulative,"insert_after_paragraph":paragraph,"scene_summary":summary,"subject":subject,"alt_text":alt,"caption":""})
            plan={"session_id":sid,"source_sha256":digest,"chapter_number":chapter_number,"items":items}
            (base/"ILLUSTRATION_PLAN.v001.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
            (base/"ILLUSTRATION_PLAN.v001.md").write_text("# 挿絵計画 v001\n\n"+"\n".join(f"- {x['image_id']}: {x['scene_summary']}（段落{x['insert_after_paragraph']}、{x['alt_text']}）" for x in items),encoding="utf-8")
            timestamp=now(); db.execute("INSERT INTO visual_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(sid,work_id,document_id,chapter_number,str(source.relative_to(self._root())),digest,"planned",self.settings["target_chars_per_image"],self.settings["min_chars_between_images"],self.settings["max_chars_without_image"],int(self.settings["cover_enabled"]),"agy-image",None,None,timestamp,timestamp))
            for item in items:
                out=base/"images"/("cover.png" if item["kind"]=="cover" else f"illustration-{item['ordinal']:03d}.png"); prompt_path=base/"tasks"/(item["image_id"]+".md"); prompt_path.parent.mkdir(parents=True,exist_ok=True); prompt_path.write_text(item["scene_summary"],encoding="utf-8")
                db.execute("INSERT INTO visual_images VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(item["image_id"],sid,item["ordinal"],item["kind"],item["anchor_start"],item["anchor_end"],item["insert_after_paragraph"],item["scene_summary"],str(prompt_path.relative_to(self._root())),str(out.relative_to(self._root())),item["alt_text"],item["caption"],"pending",0,None,timestamp,timestamp))
        return self.status(work_id,sid)

    def _image_prompt(self, session, image, bible):
        return f"""Create one original raster illustration for an offline Japanese fantasy novel reading edition. Actually use the image generation tool and save exactly to {self._root()/image['output_path']} as PNG. Do not merely describe it. Use a consistent hand-painted watercolor storybook style, soft ink lines, winter blue-gray shadows and warm orange forge light, 4:3 composition. No text, logo, watermark, readable signs, border, collage, or extra characters.\n\nVisual bible:\n{bible}\n\nScene: {image['scene_summary']}\nShow: {image['alt_text']}\nRequired visual subjects: {image['scene_summary']}"""

    def resume(self, work_id, session_id):
        session=self._session(work_id,session_id); base=self.orchestrator.config.store/"works"/work_id/"visual"/session_id; bible=(base/"CHARACTER_VISUAL_BIBLE.v001.md").read_text(encoding="utf-8")
        with self.orchestrator.connection() as db: images=[dict(x) for x in db.execute("SELECT * FROM visual_images WHERE session_id=? ORDER BY ordinal",(session_id,))]
        if self.dummy: raise VisualPublishError("dummyでは画像生成を偽装しません")
        adapter=AgyImageAdapter(self.orchestrator.config.commands.get("agy",["agy"])[0])
        try:
            for image in images:
                if image["status"]=="completed" and Path(image["output_path"]).is_file(): continue
                output=self._root()/image["output_path"]; output.parent.mkdir(parents=True,exist_ok=True); prompt=self._image_prompt(session,image,bible)
                with self.orchestrator.connection() as db: db.execute("UPDATE visual_images SET status='generating',attempt=attempt+1,error=NULL,updated_at=? WHERE image_id=?",(now(),image["image_id"]))
                adapter.generate(prompt,output)
                with self.orchestrator.connection() as db: db.execute("UPDATE visual_images SET status='completed',updated_at=? WHERE image_id=?",(now(),image["image_id"]))
            with self.orchestrator.connection() as db: images=[dict(x) for x in db.execute("SELECT * FROM visual_images WHERE session_id=? ORDER BY ordinal",(session_id,))]
            html_path,manifest=self._render_preview(session,images)
            with self.orchestrator.connection() as db: db.execute("UPDATE visual_sessions SET status='preview',error=NULL,updated_at=? WHERE session_id=?",(now(),session_id))
            return {"status":"preview","html_path":str(html_path),"manifest_path":str(manifest),"image_count":len(images),"characters":self._source_chars(session)}
        except Exception as error:
            with self.orchestrator.connection() as db: db.execute("UPDATE visual_sessions SET status='failed',error=?,updated_at=? WHERE session_id=?",(str(error),now(),session_id))
            raise

    def _source_chars(self, session): return len(self._normalized_source((self._root()/session["source_path"]).read_text(encoding="utf-8")))

    def _render_html(self, source, images, asset_prefix="assets"):
        blocks=self._reader_blocks(source); by_para={x["insert_after_paragraph"]:x for x in images if x["kind"]=="body" and x["status"]=="completed"}; out=[]
        covers=[x for x in images if x["kind"]=="cover" and x["status"]=="completed"]
        if covers:
            cover=covers[0]; out.append(f'<figure><img src="{html.escape(asset_prefix+"/"+Path(cover["output_path"]).name)}" alt="{html.escape(cover["alt_text"])}"></figure>')
        for i,block in enumerate(blocks):
            lines=[]
            for line in block.splitlines():
                if line.startswith("### "): lines.append(f"<h3>{html.escape(line[4:])}</h3>")
                elif line.startswith("## "): lines.append(f"<h2>{html.escape(line[3:])}</h2>")
                elif line.startswith("# "): lines.append(f"<h1>{html.escape(line[2:])}</h1>")
                else: lines.append(f"<p>{html.escape(line)}</p>")
            out.extend(lines)
            if i in by_para:
                image=by_para[i]; filename=Path(image["output_path"]).name; out.append(f'<figure><img src="{html.escape(asset_prefix+"/"+filename)}" alt="{html.escape(image["alt_text"])}" loading="lazy"></figure>')
        return "\n".join(out)

    @staticmethod
    def _normalized_source(source):
        blocks=VisualPublisher._reader_blocks(source); return re.sub(r"\s+"," "," ".join(re.sub(r"^#{1,6}\s*", "", b, flags=re.MULTILINE) for b in blocks)).strip()

    @staticmethod
    def _normalized_html(article):
        article=re.sub(r"<figure>.*?</figure>","",article,flags=re.DOTALL); text=re.sub(r"<[^>]+>"," ",article); return re.sub(r"\s+"," ",html.unescape(text)).strip()

    def _render_preview(self, session, images):
        source=(self._root()/session["source_path"]).read_text(encoding="utf-8"); base=self.orchestrator.config.store/"works"/session["work_id"] / "visual" / session["session_id"]/"preview"; assets=base/"assets"; assets.mkdir(parents=True,exist_ok=True)
        for image in images: shutil.copy2(self._root()/image["output_path"],assets/Path(image["output_path"]).name)
        article=self._render_html(source,images); 
        if self._normalized_source(source)!=self._normalized_html(article): raise VisualPublishError("HTML本文完全性検査に失敗しました")
        title="挿絵付き読書版"; document=f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>:root{{color-scheme:light dark}}body{{margin:0;background:#f7f3ea;color:#29241f;font-family:serif;line-height:1.9;font-size:19px}}main{{max-width:760px;margin:auto;padding:32px 20px 80px;background:#fffdf8}}h1,h2,h3{{line-height:1.35}}p{{margin:0 0 1.1em}}figure{{margin:2.2em 0;text-align:center}}img{{display:block;width:100%;height:auto;aspect-ratio:4/3;object-fit:cover;border-radius:8px}}@media(prefers-color-scheme:dark){{body{{background:#1e1c19;color:#eee7dc}}main{{background:#282521}}}}</style></head><body><main>{article}</main></body></html>'''
        index=base/"index.html"; index.write_text(document,encoding="utf-8"); manifest=base/"manifest.json"; manifest.write_text(json.dumps({"source_sha256":session["source_sha256"],"characters":len(self._normalized_source(source)),"image_count":len(images),"images":[{"image_id":x["image_id"],"path":Path(x["output_path"]).name,"paragraph":x["insert_after_paragraph"]} for x in images]},ensure_ascii=False,indent=2),encoding="utf-8"); return index,manifest

    def status(self, work_id, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: images=[dict(x) for x in db.execute("SELECT * FROM visual_images WHERE session_id=? ORDER BY ordinal",(session["session_id"],))]
        return {**dict(session),"images":images}

    def show(self, kind, work_id, session_id=None):
        session=self._session(work_id,session_id); base=self.orchestrator.config.store/"works"/work_id/"visual"/session["session_id"]
        if kind=="plan": return {"path":str(base/"ILLUSTRATION_PLAN.v001.md"),"image_count":len(self.status(work_id,session_id)["images"])}
        if kind=="html": return {"path":str(base/"preview/index.html"),"status":session["status"],"image_count":len(self.status(work_id,session_id)["images"]),"characters":self._source_chars(session)}
        raise VisualPublishError("表示対象が不正です")

    def regenerate(self, image_id, work_id, session_id=None):
        session=self._session(work_id,session_id)
        with self.orchestrator.connection() as db: image=db.execute("SELECT * FROM visual_images WHERE image_id=? AND session_id=?",(image_id,session["session_id"])).fetchone()
        if not image: raise VisualPublishError("画像が見つかりません")
        with self.orchestrator.connection() as db: db.execute("UPDATE visual_images SET status='pending',updated_at=? WHERE image_id=?",(now(),image_id))
        return self.resume(work_id,session["session_id"])

    def approve(self, work_id, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="preview": raise VisualPublishError("プレビュー状態でのみ承認できます")
        with self.orchestrator.connection() as db: db.execute("UPDATE visual_sessions SET status='approved',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return self.status(work_id,session["session_id"])

    def finalize(self, work_id, session_id=None):
        session=self._session(work_id,session_id)
        if session["status"]!="approved": raise VisualPublishError("利用者承認後にだけ確定できます")
        with self.orchestrator.connection() as db: version=db.execute("SELECT COALESCE(MAX(version),0)+1 FROM visual_documents WHERE session_id=?",(session["session_id"],)).fetchone()[0]
        target=self._root()/"novels"/work_id/f"illustrated-html-v{version:03d}"; 
        if target.exists(): raise VisualPublishError("確定済みHTMLの上書きを拒否しました")
        preview=self.orchestrator.config.store/"works"/work_id/"visual"/session["session_id"]/"preview"; target.mkdir(parents=True); shutil.copy2(preview/"index.html",target/"index.html"); shutil.copy2(preview/"manifest.json",target/"manifest.json"); shutil.copy2(preview.parent/"CHARACTER_VISUAL_BIBLE.v001.md",target/"CHARACTER_VISUAL_BIBLE.v001.md"); shutil.copy2(preview.parent/"ILLUSTRATION_PLAN.v001.md",target/"ILLUSTRATION_PLAN.v001.md"); shutil.copytree(preview/"assets",target/"assets")
        with self.orchestrator.connection() as db: db.execute("INSERT INTO visual_documents(session_id,version,path,manifest_path,source_sha256,image_count,created_at) VALUES(?,?,?,?,?,?,?)",(session["session_id"],version,str(target),str(target/"manifest.json"),session["source_sha256"],len(self.status(work_id,session_id)["images"]),now())); db.execute("UPDATE visual_sessions SET status='completed',updated_at=? WHERE session_id=?",(now(),session["session_id"]))
        return {"status":"completed","version":version,"path":str(target),"image_count":len(self.status(work_id,session_id)["images"]),"characters":self._source_chars(session)}
