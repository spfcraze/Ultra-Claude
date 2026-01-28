import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table

from src import __version__

console = Console()


@click.group()
@click.version_option(version=__version__, prog_name="autowrkers")
def main():
    """Autowrkers - Multi-session Claude Code manager."""
    pass


@main.command()
def version():
    """Show version information."""
    console.print(f"[bold cyan]Autowrkers[/bold cyan] v{__version__}")
    console.print(f"Repository: https://github.com/spfcraze/Ultra-Claude")


@main.command()
@click.option("--check-only", is_flag=True, help="Only check for updates, don't install")
@click.option("--force", is_flag=True, help="Force update even with uncommitted changes")
def update(check_only: bool, force: bool):
    """Check for and install updates."""
    asyncio.run(_update(check_only, force))


async def _update(check_only: bool, force: bool):
    from src.updater import updater

    console.print("[bold]Checking for updates...[/bold]")
    
    update_info = await updater.check_for_updates()
    git_status = await updater.get_local_git_status()

    table = Table(show_header=False, box=None)
    table.add_column("Label", style="dim")
    table.add_column("Value")
    
    table.add_row("Current version", f"v{update_info.current_version}")
    
    if update_info.error:
        console.print(f"\n[red]Error:[/red] {update_info.error}")
        return
    
    if update_info.latest_version:
        table.add_row("Latest version", f"v{update_info.latest_version}")
    
    if git_status.get("is_git"):
        table.add_row("Branch", git_status.get("branch", "unknown"))
        table.add_row("Commit", git_status.get("local_commit", "unknown"))
        if git_status.get("has_uncommitted_changes"):
            table.add_row("Changes", "[yellow]Uncommitted changes present[/yellow]")
    else:
        table.add_row("Git", "[yellow]Not a git repository[/yellow]")
    
    console.print(table)
    console.print()

    if update_info.update_available:
        console.print(f"[green]Update available![/green] v{update_info.current_version} → v{update_info.latest_version}")
        
        if update_info.release_notes:
            console.print("\n[bold]Release notes:[/bold]")
            console.print(update_info.release_notes[:500])
        
        if check_only:
            console.print("\nRun [cyan]autowrkers update[/cyan] to install the update.")
            return
        
        if not git_status.get("is_git"):
            console.print("\n[yellow]Cannot auto-update:[/yellow] Not a git repository.")
            console.print(f"Download manually from: {update_info.release_url or 'https://github.com/spfcraze/Ultra-Claude'}")
            return
        
        console.print("\n[bold]Installing update...[/bold]")
        result = await updater.update(force=force)
        
        if result.get("success"):
            if result.get("already_up_to_date"):
                console.print("[green]Already up to date.[/green]")
            else:
                console.print("[green]Update installed successfully![/green]")
                if result.get("restart_required"):
                    console.print("[yellow]Please restart the server to apply changes.[/yellow]")
        else:
            console.print(f"[red]Update failed:[/red] {result.get('error')}")
    else:
        console.print("[green]You're up to date![/green]")


@main.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to (use 0.0.0.0 for external access, requires auth)")
@click.option("--port", default=8420, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--ssl-certfile", default=None, help="Path to SSL certificate (PEM). Also: AUTOWRKERS_SSL_CERTFILE env var")
@click.option("--ssl-keyfile", default=None, help="Path to SSL private key (PEM). Also: AUTOWRKERS_SSL_KEYFILE env var")
def serve(host: str, port: int, reload: bool, ssl_certfile: str, ssl_keyfile: str):
    """Start the Autowrkers server."""
    import os
    import uvicorn

    # Resolve SSL from args or env vars
    certfile = ssl_certfile or os.environ.get("AUTOWRKERS_SSL_CERTFILE")
    keyfile = ssl_keyfile or os.environ.get("AUTOWRKERS_SSL_KEYFILE")

    scheme = "https" if (certfile and keyfile) else "http"
    console.print(f"[bold cyan]Starting Autowrkers server...[/bold cyan]")
    console.print(f"URL: {scheme}://{host if host != '0.0.0.0' else 'localhost'}:{port}")

    if certfile and keyfile:
        console.print(f"[green]HTTPS enabled[/green] cert={certfile}")

    kwargs = {
        "host": host,
        "port": port,
        "reload": reload,
    }
    if certfile and keyfile:
        kwargs["ssl_certfile"] = certfile
        kwargs["ssl_keyfile"] = keyfile

    uvicorn.run("src.server:app", **kwargs)


if __name__ == "__main__":
    main()
