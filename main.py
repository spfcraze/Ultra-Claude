#!/usr/bin/env python3
"""
Autowrkers - Multi-session Claude Code Manager

A hybrid terminal + web dashboard tool for managing multiple Claude Code sessions in parallel.
"""
import asyncio
import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """Autowrkers - Multi-session Claude Code Manager"""
    pass


@cli.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to (use 0.0.0.0 for external access, requires auth)")
@click.option("--port", default=8420, help="Port to bind to")
@click.option("--sessions", "-n", default=0, help="Number of sessions to start immediately")
@click.option("--working-dir", "-d", default=None, help="Working directory for new sessions")
def start(host: str, port: int, sessions: int, working_dir: str):
    """Start the Autowrkers server and web dashboard"""
    from src.server import app, run_server
    from src.session_manager import manager
    from src.notifier import notify_session_needs_attention

    console.print(Panel.fit(
        "[bold cyan]Autowrkers[/bold cyan] - Multi-session Claude Code Manager\n"
        f"[dim]Starting server on http://{host}:{port}[/dim]",
        border_style="cyan"
    ))

    # Register notification callback
    async def on_attention(session_id, status):
        if status.value == "needs_attention":
            session = manager.get_session(session_id)
            if session:
                notify_session_needs_attention(session.name, session.id)

    manager.add_status_callback(on_attention)

    # Start initial sessions if requested
    if sessions > 0:
        async def start_initial_sessions():
            for i in range(sessions):
                session = manager.create_session(
                    name=f"Claude {i + 1}",
                    working_dir=working_dir
                )
                await manager.start_session(session)
                console.print(f"  [green]✓[/green] Started session: {session.name}")

        asyncio.get_event_loop().run_until_complete(start_initial_sessions())

    console.print(f"\n[bold green]Dashboard ready:[/bold green] http://localhost:{port}")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    run_server(host=host, port=port)


@cli.command()
@click.argument("count", type=int, default=1)
@click.option("--working-dir", "-d", default=None, help="Working directory")
@click.option("--name", "-n", default=None, help="Session name")
def spawn(count: int, working_dir: str, name: str):
    """Spawn new Claude Code session(s)"""
    import requests

    for i in range(count):
        session_name = f"{name} {i + 1}" if name and count > 1 else name
        try:
            resp = requests.post(
                "http://localhost:8420/api/sessions",
                params={"name": session_name, "working_dir": working_dir}
            )
            data = resp.json()
            if data.get("success"):
                session = data["session"]
                console.print(f"[green]✓[/green] Created session: {session['name']} (#{session['id']})")
            else:
                console.print(f"[red]✗[/red] Failed to create session")
        except requests.ConnectionError:
            console.print("[red]Error:[/red] Server not running. Start with: autowrkers start")
            return


@cli.command()
def status():
    """Show status of all sessions"""
    import requests

    try:
        resp = requests.get("http://localhost:8420/api/sessions")
        data = resp.json()
        sessions = data.get("sessions", [])

        if not sessions:
            console.print("[dim]No active sessions[/dim]")
            return

        table = Table(title="Autowrkers Sessions")
        table.add_column("ID", style="cyan", justify="right")
        table.add_column("Name", style="white")
        table.add_column("Status", justify="center")
        table.add_column("Working Dir", style="dim")

        status_styles = {
            "running": "[green]Running[/green]",
            "needs_attention": "[yellow]⚠ Needs Attention[/yellow]",
            "stopped": "[dim]Stopped[/dim]",
            "error": "[red]Error[/red]",
            "starting": "[blue]Starting...[/blue]"
        }

        for session in sessions:
            table.add_row(
                str(session["id"]),
                session["name"],
                status_styles.get(session["status"], session["status"]),
                session["working_dir"][:40] + "..." if len(session["working_dir"]) > 40 else session["working_dir"]
            )

        console.print(table)

    except requests.ConnectionError:
        console.print("[red]Error:[/red] Server not running. Start with: autowrkers start")


@cli.command()
@click.argument("session_id", type=int)
def attach(session_id: int):
    """Attach to a session (opens in browser)"""
    import webbrowser
    url = f"http://localhost:8420/?session={session_id}"
    console.print(f"Opening session #{session_id} in browser...")
    webbrowser.open(url)


@cli.command()
@click.argument("session_id", type=int)
def kill(session_id: int):
    """Stop a session"""
    import requests

    try:
        resp = requests.post(f"http://localhost:8420/api/sessions/{session_id}/stop")
        data = resp.json()
        if data.get("success"):
            console.print(f"[green]✓[/green] Session #{session_id} stopped")
        else:
            console.print(f"[red]✗[/red] Failed to stop session")
    except requests.ConnectionError:
        console.print("[red]Error:[/red] Server not running")


@cli.command()
@click.argument("session_id", type=int)
@click.argument("text")
def send(session_id: int, text: str):
    """Send input to a session"""
    import requests

    try:
        resp = requests.post(
            f"http://localhost:8420/api/sessions/{session_id}/input",
            params={"data": text + "\n"}
        )
        data = resp.json()
        if data.get("success"):
            console.print(f"[green]✓[/green] Sent to session #{session_id}")
        else:
            console.print(f"[red]✗[/red] Failed to send")
    except requests.ConnectionError:
        console.print("[red]Error:[/red] Server not running")


@cli.command()
def dashboard():
    """Open the web dashboard in browser"""
    import webbrowser
    url = "http://localhost:8420"
    console.print(f"Opening dashboard: {url}")
    webbrowser.open(url)


@cli.command()
def migrate():
    """Migrate data from JSON files to SQLite database"""
    from pathlib import Path
    from src.database import db, DATA_DIR
    
    projects_file = DATA_DIR / "projects.json"
    issue_sessions_file = DATA_DIR / "issue_sessions.json"
    
    if not projects_file.exists() and not issue_sessions_file.exists():
        console.print("[yellow]No JSON files found to migrate[/yellow]")
        return
    
    console.print("[cyan]Migrating data from JSON to SQLite...[/cyan]")
    db.migrate_from_json(projects_file, issue_sessions_file)
    
    console.print("[green]✓[/green] Migration complete!")
    console.print(f"  Database: {DATA_DIR / 'autowrkers.db'}")
    console.print("\n[dim]You can now delete the old JSON files if desired:[/dim]")
    if projects_file.exists():
        console.print(f"  rm {projects_file}")
    if issue_sessions_file.exists():
        console.print(f"  rm {issue_sessions_file}")


if __name__ == "__main__":
    cli()
