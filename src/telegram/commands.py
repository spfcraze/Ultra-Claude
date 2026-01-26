"""
Command definitions and message formatting helpers for the Telegram bot.
"""
from dataclasses import dataclass
from typing import List, Optional
import html


@dataclass
class BotCommand:
    """Telegram bot command definition."""
    command: str
    description: str


COMMANDS: List[BotCommand] = [
    BotCommand("start", "Welcome message and setup"),
    BotCommand("help", "List all commands"),
    BotCommand("status", "System status overview"),
    BotCommand("sessions", "List active sessions"),
    BotCommand("session", "Session details - /session <id>"),
    BotCommand("send", "Send input to session - /send <id> <text>"),
    BotCommand("create", "Create session - /create <name>"),
    BotCommand("stop", "Stop session - /stop <id>"),
    BotCommand("output", "Recent session output - /output <id>"),
    BotCommand("projects", "List projects"),
    BotCommand("issues", "Project issues - /issues <project_id>"),
    BotCommand("startissue", "Start issue - /startissue <issue_session_id>"),
    BotCommand("subscribe", "Subscribe to project events"),
    BotCommand("unsubscribe", "Unsubscribe from project events"),
]


def format_help_text() -> str:
    """Format the help message listing all commands."""
    lines = ["<b>UltraClaude Bot Commands</b>\n"]
    for cmd in COMMANDS:
        lines.append(f"/{cmd.command} - {html.escape(cmd.description)}")
    return "\n".join(lines)


def format_session_status(session) -> str:
    """Format session info as Telegram HTML message."""
    status_icons = {
        "starting": "...",
        "running": "[RUN]",
        "needs_attention": "[!]",
        "stopped": "[X]",
        "error": "[ERR]",
        "queued": "[Q]",
        "completed": "[OK]",
    }
    status_val = session.get("status", "unknown") if isinstance(session, dict) else getattr(session, "status", "unknown")
    if hasattr(status_val, "value"):
        status_val = status_val.value

    icon = status_icons.get(status_val, "?")
    name = session.get("name", "?") if isinstance(session, dict) else getattr(session, "name", "?")
    sid = session.get("id", "?") if isinstance(session, dict) else getattr(session, "id", "?")
    working_dir = session.get("working_dir", "") if isinstance(session, dict) else getattr(session, "working_dir", "")
    created_at = session.get("created_at", "") if isinstance(session, dict) else getattr(session, "created_at", "")
    last_output = session.get("last_output", "") if isinstance(session, dict) else getattr(session, "last_output", "")

    lines = [
        f"{icon} <b>Session #{sid}: {html.escape(str(name))}</b>",
        f"  Status: {status_val}",
        f"  Dir: <code>{html.escape(str(working_dir))}</code>",
    ]
    if created_at:
        lines.append(f"  Created: {html.escape(str(created_at)[:19])}")
    if last_output:
        preview = str(last_output)[-200:].strip()
        if preview:
            lines.append(f"  Output: <code>{html.escape(preview[:150])}</code>")
    return "\n".join(lines)


def format_session_list(sessions) -> str:
    """Format a list of sessions as Telegram HTML."""
    if not sessions:
        return "No active sessions."

    lines = [f"<b>Sessions ({len(sessions)})</b>\n"]
    for s in sessions:
        sd = s.to_dict() if hasattr(s, "to_dict") else s
        status_val = sd.get("status", "unknown")
        status_icons = {
            "starting": "...",
            "running": "[RUN]",
            "needs_attention": "[!]",
            "stopped": "[X]",
            "error": "[ERR]",
            "queued": "[Q]",
            "completed": "[OK]",
        }
        icon = status_icons.get(status_val, "?")
        lines.append(f"{icon} #{sd.get('id', '?')} {html.escape(str(sd.get('name', '?')))} - {status_val}")
    return "\n".join(lines)


def format_project_list(projects) -> str:
    """Format projects as Telegram HTML message."""
    if not projects:
        return "No projects configured."

    lines = [f"<b>Projects ({len(projects)})</b>\n"]
    for p in projects:
        pd = p if isinstance(p, dict) else (p.to_dict() if hasattr(p, "to_dict") else {"id": "?", "name": "?"})
        name = pd.get("name", "?")
        pid = pd.get("id", "?")
        repo = pd.get("github_repo", "")
        status = pd.get("status", "")
        line = f"#{pid} <b>{html.escape(str(name))}</b>"
        if repo:
            line += f" ({html.escape(str(repo))})"
        if status:
            line += f" [{status}]"
        lines.append(line)
    return "\n".join(lines)


def format_issue_list(issues) -> str:
    """Format issues as Telegram HTML message."""
    if not issues:
        return "No issues found."

    lines = [f"<b>Issues ({len(issues)})</b>\n"]
    for issue in issues:
        isd = issue if isinstance(issue, dict) else (issue.to_dict() if hasattr(issue, "to_dict") else {})
        issue_num = isd.get("github_issue_number", "?")
        title = isd.get("github_issue_title", "?")
        status = isd.get("status", "?")
        sid = isd.get("id", "?")
        status_icons = {
            "pending": "[P]",
            "in_progress": "[RUN]",
            "completed": "[OK]",
            "failed": "[ERR]",
            "verifying": "[V]",
            "pr_created": "[PR]",
        }
        icon = status_icons.get(status, "[?]")
        lines.append(f"{icon} #{sid} Issue #{issue_num}: {html.escape(str(title)[:60])} - {status}")
    return "\n".join(lines)


def format_system_status(sessions, projects, automation_status=None) -> str:
    """Format system overview as Telegram HTML message."""
    running = sum(1 for s in sessions if (s.get("status") if isinstance(s, dict) else getattr(s, "status", None)) in ("running", "needs_attention", SessionStatusValue.RUNNING, SessionStatusValue.NEEDS_ATTENTION))
    total_sessions = len(sessions)

    lines = [
        "<b>UltraClaude Status</b>\n",
        f"Sessions: {running} running / {total_sessions} total",
        f"Projects: {len(projects)}",
    ]

    if automation_status:
        active = sum(1 for v in automation_status.values() if v.get("running"))
        lines.append(f"Automation: {active} active")

    # List sessions needing attention
    attention = [s for s in sessions if (s.get("status") if isinstance(s, dict) else getattr(s, "status", None)) in ("needs_attention", SessionStatusValue.NEEDS_ATTENTION)]
    if attention:
        lines.append(f"\n[!] {len(attention)} session(s) need attention")

    return "\n".join(lines)


class SessionStatusValue:
    """String constants matching SessionStatus enum values."""
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"


def truncate_output(text: str, max_lines: int = 20) -> str:
    """Truncate long output for Telegram display."""
    if not text:
        return "(no output)"
    lines = text.strip().split("\n")
    if len(lines) <= max_lines:
        return text.strip()
    # Show last max_lines
    truncated = lines[-max_lines:]
    return f"... ({len(lines) - max_lines} lines omitted)\n" + "\n".join(truncated)
