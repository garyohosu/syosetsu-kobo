from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .orchestrator import Config, DummyAdapter, KoboError, Orchestrator, load_agents
from .gemini import GeminiAdapter, GeminiError
from .agy import AgyAdapter, AgyError
from .urs import UrsManager
from .concept import ConceptManager
from .story_design import StoryDesignManager
from .manuscript import ManuscriptManager
from .canon import CanonManager
from .visual_publish import VisualPublisher
from .agy_image import AgyImageError
from .devloop import DevLoop, DevLoopConfig


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="小説工房オーケストレーター")
    root.add_argument("--config", type=Path)
    root.add_argument("--dummy", action="store_true", help="外部AIを起動せず全アダプターをダミー化")
    sub = root.add_subparsers(dest="command", required=True)
    create = sub.add_parser("work-create"); create.add_argument("work_id"); create.add_argument("title"); create.add_argument("--first-agent")
    sub.add_parser("work-list"); sub.add_parser("active"); sub.add_parser("agents")
    for name in ("run-step", "run", "continue", "history", "dry-run"):
        command = sub.add_parser(name); command.add_argument("--work")
    retry = sub.add_parser("retry"); retry.add_argument("run_id")
    sub.add_parser("stop")
    sub.add_parser("gemini-doctor")
    smoke = sub.add_parser("gemini-smoke"); smoke.add_argument("--model")
    sub.add_parser("agy-doctor")
    agy_smoke = sub.add_parser("agy-smoke"); agy_smoke.add_argument("--model")
    start = sub.add_parser("urs-start"); start.add_argument("--work"); start.add_argument("--known-json", type=Path)
    for name in ("urs-question", "urs-status", "urs-preview", "urs-finalize", "urs-interactive"):
        command = sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    answer = sub.add_parser("urs-answer"); answer.add_argument("question_id"); answer.add_argument("answer"); answer.add_argument("--work"); answer.add_argument("--session"); answer.add_argument("--status", choices=("confirmed","provisional"), default="confirmed"); answer.add_argument("--evidence", choices=("user","known","ai_inference","source"), default="user"); answer.add_argument("--revise", action="store_true")
    defer = sub.add_parser("urs-defer"); defer.add_argument("question_id"); defer.add_argument("--work"); defer.add_argument("--session")
    history = sub.add_parser("urs-answer-history"); history.add_argument("question_id"); history.add_argument("--work"); history.add_argument("--session")
    concept_start=sub.add_parser("concept-start"); concept_start.add_argument("--work"); concept_start.add_argument("--count",type=int,default=5)
    for name in ("concept-status","concept-list","concept-compare","concept-history","concept-preview","concept-finalize","concept-resume"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    publish=sub.add_parser("concept-publish"); publish.add_argument("--work"); publish.add_argument("--session")
    sub.add_parser("concept-board").add_argument("--work");
    # concept-board also accepts --session; add it to the parser created above.
    # argparse returns the same parser object, so configure it explicitly.
    board_parser = sub.choices["concept-board"]; board_parser.add_argument("--session")
    detail=sub.add_parser("concept-show"); detail.add_argument("candidate_id"); detail.add_argument("--work"); detail.add_argument("--session")
    select=sub.add_parser("concept-select"); select.add_argument("candidate_id"); select.add_argument("--work"); select.add_argument("--session")
    for name in ("concept-hold","concept-reject-all","concept-regenerate"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    revise=sub.add_parser("concept-revise"); revise.add_argument("candidate_id"); revise.add_argument("--instructions",type=Path,required=True); revise.add_argument("--work"); revise.add_argument("--session")
    for name in ("story-start", "story-resume", "story-status"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    show=sub.add_parser("story-show"); show.add_argument("kind", choices=("bible_draft","bible_audit","plot_draft","plot_audit","bible","plot")); show.add_argument("--work"); show.add_argument("--session")
    for name in ("story-approve-bible", "story-finalize-bible", "story-start-plot", "story-approve-plot", "story-finalize-plot"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    manuscript_start=sub.add_parser("manuscript-start"); manuscript_start.add_argument("chapter",type=int); manuscript_start.add_argument("--title"); manuscript_start.add_argument("--work")
    for name in ("manuscript-resume","manuscript-status","manuscript-approve","manuscript-finalize"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    manuscript_show=sub.add_parser("manuscript-show"); manuscript_show.add_argument("kind",choices=("chapter_design","scene_design","draft","audit","revision","reaudit","final")); manuscript_show.add_argument("--work"); manuscript_show.add_argument("--session")
    canon_start=sub.add_parser("canon-start"); canon_start.add_argument("chapter",type=int); canon_start.add_argument("--work")
    for name in ("canon-resume","canon-status","canon-approve","canon-finalize"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    canon_show=sub.add_parser("canon-show"); canon_show.add_argument("kind",choices=("canon","character_ledger","timeline","resource_ledger","foreshadowing_ledger","audit","draft","revision")); canon_show.add_argument("--work"); canon_show.add_argument("--session")
    canon_reject=sub.add_parser("canon-reject"); canon_reject.add_argument("--reason",required=True); canon_reject.add_argument("--instructions",type=Path); canon_reject.add_argument("--work"); canon_reject.add_argument("--session")
    visual_start=sub.add_parser("visual-start"); visual_start.add_argument("chapter",type=int); visual_start.add_argument("--work",required=True)
    for name in ("visual-resume","visual-status","visual-approve","visual-finalize"):
        command=sub.add_parser(name); command.add_argument("--work",required=True); command.add_argument("--session")
    visual_show=sub.add_parser("visual-show"); visual_show.add_argument("kind",choices=("plan","html")); visual_show.add_argument("--work",required=True); visual_show.add_argument("--session")
    visual_regenerate=sub.add_parser("visual-regenerate"); visual_regenerate.add_argument("image_id"); visual_regenerate.add_argument("--work",required=True); visual_regenerate.add_argument("--session")
    dev_status=sub.add_parser("devloop-status"); dev_status.add_argument("--dev-config",type=Path,default=Path("devloop.json"))
    dev_once=sub.add_parser("devloop-once"); dev_once.add_argument("--dev-config",type=Path,default=Path("devloop.json")); dev_once.add_argument("--execute",action="store_true"); dev_once.add_argument("--publish",action="store_true")
    dev_run=sub.add_parser("devloop-run"); dev_run.add_argument("--dev-config",type=Path,default=Path("devloop.json")); dev_run.add_argument("--execute",action="store_true"); dev_run.add_argument("--publish",action="store_true"); dev_run.add_argument("--max-cycles",type=int)
    return root


def gemini_adapter(config: Config) -> GeminiAdapter:
    template = config.commands.get("gemini", ["gemini"])
    return GeminiAdapter(template[0], template[1:])


def agy_adapter(config: Config) -> AgyAdapter:
    template = config.commands.get("agy", ["agy"])
    return AgyAdapter(template[0])


def interactive_urs(manager: UrsManager, work: str | None, session: str | None) -> dict:
    while True:
        question = manager.current(work, session)
        if not question:
            return manager.status(work, session)
        print(f"\n質問: {question['text']}")
        for index, choice in enumerate(question["choices"], 1):
            print(f"{index}. {choice['value']} — {choice['impact']}")
        print("0. 今は決めない\n自由記入も可能です。")
        value = input("> ").strip()
        if value == "0": manager.answer(question["question_id"], None, status="deferred", work_id=work, session_id=session)
        else:
            if value.isdigit() and 1 <= int(value) <= len(question["choices"]): value = question["choices"][int(value)-1]["value"]
            manager.answer(question["question_id"], value, work_id=work, session_id=session)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = Config.load(args.config)
        adapters = {name: DummyAdapter() for name in config.commands} if args.dummy else None
        orchestrator = Orchestrator(config, adapters=adapters)
        if args.command == "work-create": result = orchestrator.create_work(args.work_id, args.title, first_agent=args.first_agent)
        elif args.command == "work-list": result = orchestrator.list_works()
        elif args.command == "active": result = orchestrator.get_work()
        elif args.command == "agents": result = [{"agent_id": a.agent_id, "display_name": a.display_name, "adapter": a.adapter, "model": a.model} for a in load_agents(config.agents_dir).values()]
        elif args.command == "run-step": result = orchestrator.run_step(args.work)
        elif args.command in {"run", "continue"}: result = orchestrator.continue_work(args.work)
        elif args.command == "history": result = orchestrator.history(args.work)
        elif args.command == "dry-run": result = orchestrator.dry_run(args.work)
        elif args.command == "retry": result = orchestrator.retry(args.run_id)
        elif args.command == "stop": orchestrator.stop(); result = {"stopped": True}
        elif args.command == "gemini-doctor": result = gemini_adapter(config).doctor(config.default_timeout)
        elif args.command == "gemini-smoke":
            adapter = gemini_adapter(config); agent = orchestrator.agents["writer"]
            if args.model: agent = type(agent)(**{**agent.__dict__, "model": args.model})
            run_id = orchestrator._new_run_id(); run_dir = config.store / "diagnostics" / run_id; run_dir.mkdir(parents=True)
            task = run_dir / "task.md"; output = run_dir / "result.md"
            task.write_text("# 接続確認\n\nこれは機密情報や小説本文を含まない接続テストです。日本語で「接続確認成功」とだけ回答してください。\n", encoding="utf-8")
            refs = {"task_path":str(task),"output_path":str(output),"model":args.model or config.models.get("gemini",agent.model),"run_id":run_id,"run_dir":str(run_dir),"agent_path":str(agent.path),"mail_db":str(config.mail_db),"mail_id":"diagnostic"}
            result = adapter.smoke(agent, refs, output)
        elif args.command == "agy-doctor":
            result = agy_adapter(config).doctor(config.default_timeout)
        elif args.command == "agy-smoke":
            adapter = agy_adapter(config); agent = orchestrator.agents["writer"]
            run_id = orchestrator._new_run_id(); run_dir = config.store / "diagnostics" / run_id; run_dir.mkdir(parents=True)
            output = run_dir / "result.md"
            refs = {"model": args.model or "", "run_id": run_id, "run_dir": str(run_dir), "mail_id": "diagnostic"}
            result = adapter.smoke(agent, refs, output)
        elif args.command.startswith("devloop-"):
            loop=DevLoop(DevLoopConfig.load(args.dev_config))
            if args.command=="devloop-status": result=loop.status()
            elif args.command=="devloop-run": result=loop.run(args.execute,args.publish,args.max_cycles)
            else: result=loop.once(args.execute,args.publish)
        elif args.command.startswith("manuscript-"):
            manager=ManuscriptManager(orchestrator,dummy=args.dummy)
            if args.command=="manuscript-start": result=manager.start(args.chapter,args.title,args.work)
            elif args.command=="manuscript-resume": result=manager.resume(args.work,args.session)
            elif args.command=="manuscript-status": result=manager.status(args.work,args.session)
            elif args.command=="manuscript-show": result=manager.show(args.kind,args.work,args.session)
            elif args.command=="manuscript-approve": result=manager.approve(args.work,args.session)
            else: result=manager.finalize(args.work,args.session)
        elif args.command.startswith("visual-"):
            manager=VisualPublisher(orchestrator,dummy=args.dummy)
            if args.command=="visual-start": result=manager.start(args.chapter,args.work)
            elif args.command=="visual-resume": result=manager.resume(args.work,args.session)
            elif args.command=="visual-status": result=manager.status(args.work,args.session)
            elif args.command=="visual-show": result=manager.show(args.kind,args.work,args.session)
            elif args.command=="visual-regenerate": result=manager.regenerate(args.image_id,args.work,args.session)
            elif args.command=="visual-approve": result=manager.approve(args.work,args.session)
            else: result=manager.finalize(args.work,args.session)
        elif args.command.startswith("canon-"):
            manager=CanonManager(orchestrator,dummy=args.dummy)
            if args.command=="canon-start": result=manager.start(args.chapter,args.work)
            elif args.command=="canon-resume": result=manager.resume(args.work,args.session)
            elif args.command=="canon-status": result=manager.status(args.work,args.session)
            elif args.command=="canon-show": result=manager.show(args.kind,args.work,args.session)
            elif args.command=="canon-reject": result=manager.reject(args.reason,args.instructions,args.work,args.session)
            elif args.command=="canon-approve": result=manager.approve(args.work,args.session)
            else: result=manager.finalize(args.work,args.session)
        elif args.command.startswith("story-"):
            manager=StoryDesignManager(orchestrator,dummy=args.dummy)
            if args.command=="story-start": result=manager.start(args.work)
            elif args.command=="story-resume": result=manager.resume(args.work,args.session)
            elif args.command=="story-status": result=manager.status(args.work,args.session)
            elif args.command=="story-show": result=manager.show(args.kind,args.work,args.session)
            elif args.command=="story-approve-bible": result=manager.approve("bible",args.work,args.session)
            elif args.command=="story-finalize-bible": result=manager.finalize_bible(args.work,args.session)
            elif args.command=="story-start-plot": result=manager.start_plot(args.work,args.session)
            elif args.command=="story-approve-plot": result=manager.approve("plot",args.work,args.session)
            else: result=manager.finalize_plot(args.work,args.session)
        elif args.command.startswith("concept-"):
            manager=ConceptManager(orchestrator,dummy=args.dummy)
            if args.command=="concept-start": result=manager.start(args.work,args.count)
            elif args.command=="concept-status": result=manager.status(args.work,args.session)
            elif args.command=="concept-list": result=manager.candidates(args.work,args.session)
            elif args.command=="concept-show": result=manager.candidate(args.candidate_id,args.work,args.session)
            elif args.command=="concept-compare": result=manager.comparisons(args.work,args.session)
            elif args.command=="concept-board": result=manager.board(args.work,args.session)
            elif args.command=="concept-publish": result=manager.publish(args.work,args.session)
            elif args.command=="concept-select": result=manager.action("select",args.candidate_id,work_id=args.work,session_id=args.session)
            elif args.command=="concept-hold": result=manager.action("hold",work_id=args.work,session_id=args.session)
            elif args.command=="concept-reject-all": result=manager.action("reject_all",work_id=args.work,session_id=args.session)
            elif args.command=="concept-regenerate": result=manager.action("regenerate",work_id=args.work,session_id=args.session)
            elif args.command=="concept-revise": result=manager.action("revise",args.candidate_id,instruction_path=args.instructions,work_id=args.work,session_id=args.session)
            elif args.command=="concept-history": result=manager.history(args.work,args.session)
            elif args.command=="concept-preview": result=manager.preview(args.work,args.session)
            elif args.command=="concept-finalize": result=manager.finalize(args.work,args.session)
            else: result=manager.resume(args.work,args.session)
        else:
            manager = UrsManager(orchestrator)
            if args.command == "urs-start":
                known = json.loads(args.known_json.read_text(encoding="utf-8")) if args.known_json else None; result = manager.start(args.work, known)
            elif args.command == "urs-question": result = manager.current(args.work, args.session)
            elif args.command == "urs-status": result = manager.status(args.work, args.session)
            elif args.command == "urs-answer": result = manager.answer(args.question_id,args.answer,status=args.status,evidence=args.evidence,work_id=args.work,session_id=args.session,revise=args.revise)
            elif args.command == "urs-defer": result = manager.answer(args.question_id,None,status="deferred",work_id=args.work,session_id=args.session)
            elif args.command == "urs-answer-history": result = manager.history(args.question_id,args.work,args.session)
            elif args.command == "urs-preview": result = manager.preview(args.work,args.session)
            elif args.command == "urs-finalize": result = manager.finalize(args.work,args.session)
            else: result = interactive_urs(manager,args.work,args.session)
    except (KoboError, GeminiError, AgyError, AgyImageError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)); return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
