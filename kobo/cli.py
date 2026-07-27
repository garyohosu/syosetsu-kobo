from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .orchestrator import Config, DummyAdapter, KoboError, Orchestrator, load_agents
from .gemini import GeminiAdapter, GeminiError
from .urs import UrsManager
from .concept import ConceptManager


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
    start = sub.add_parser("urs-start"); start.add_argument("--work"); start.add_argument("--known-json", type=Path)
    for name in ("urs-question", "urs-status", "urs-preview", "urs-finalize", "urs-interactive"):
        command = sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    answer = sub.add_parser("urs-answer"); answer.add_argument("question_id"); answer.add_argument("answer"); answer.add_argument("--work"); answer.add_argument("--session"); answer.add_argument("--status", choices=("confirmed","provisional"), default="confirmed"); answer.add_argument("--evidence", choices=("user","known","ai_inference","source"), default="user"); answer.add_argument("--revise", action="store_true")
    defer = sub.add_parser("urs-defer"); defer.add_argument("question_id"); defer.add_argument("--work"); defer.add_argument("--session")
    history = sub.add_parser("urs-answer-history"); history.add_argument("question_id"); history.add_argument("--work"); history.add_argument("--session")
    concept_start=sub.add_parser("concept-start"); concept_start.add_argument("--work"); concept_start.add_argument("--count",type=int,default=3)
    for name in ("concept-status","concept-list","concept-compare","concept-history","concept-preview","concept-finalize","concept-resume"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    detail=sub.add_parser("concept-show"); detail.add_argument("candidate_id"); detail.add_argument("--work"); detail.add_argument("--session")
    select=sub.add_parser("concept-select"); select.add_argument("candidate_id"); select.add_argument("--work"); select.add_argument("--session")
    for name in ("concept-hold","concept-reject-all","concept-regenerate"):
        command=sub.add_parser(name); command.add_argument("--work"); command.add_argument("--session")
    revise=sub.add_parser("concept-revise"); revise.add_argument("candidate_id"); revise.add_argument("--instructions",type=Path,required=True); revise.add_argument("--work"); revise.add_argument("--session")
    return root


def gemini_adapter(config: Config) -> GeminiAdapter:
    template = config.commands.get("gemini", ["gemini"])
    return GeminiAdapter(template[0], template[1:])


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
        elif args.command.startswith("concept-"):
            manager=ConceptManager(orchestrator,dummy=args.dummy)
            if args.command=="concept-start": result=manager.start(args.work,args.count)
            elif args.command=="concept-status": result=manager.status(args.work,args.session)
            elif args.command=="concept-list": result=manager.candidates(args.work,args.session)
            elif args.command=="concept-show": result=manager.candidate(args.candidate_id,args.work,args.session)
            elif args.command=="concept-compare": result=manager.comparisons(args.work,args.session)
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
    except (KoboError, GeminiError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)); return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
