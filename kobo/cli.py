from __future__ import annotations

import argparse
import json
from pathlib import Path

from .orchestrator import Config, DummyAdapter, KoboError, Orchestrator, load_agents


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
    return root


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
        else: orchestrator.stop(); result = {"stopped": True}
    except (KoboError, OSError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False)); return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
