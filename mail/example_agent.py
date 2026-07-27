"""Minimal startup hook example for an AI agent."""

from __future__ import annotations

from pathlib import Path

from mail.agent_mail import AgentMail


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE = PROJECT_ROOT / "mail" / "agent_mail.db"


def get_startup_work(agent_id: str):
    """Return unread counts and pending items in chronological order."""
    mailbox = AgentMail(DATABASE)
    mailbox.initialize()
    return mailbox.unread_count(agent_id), list(mailbox.iter_work(agent_id))


if __name__ == "__main__":
    counts, work = get_startup_work("writer")
    print(f"未読: {counts['total']}件")
    for item in work:
        print(item)
