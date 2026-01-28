import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    WorkflowStatus,
    PhaseStatus,
    TriggerMode,
    WorkflowPhase,
    PhaseExecution,
)
from .engine import workflow_orchestrator, WorkflowOrchestrator
from .template_manager import template_manager
from .artifact_manager import artifact_manager
from .budget_tracker import budget_manager
from .providers.registry import model_registry


class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if not attr.startswith('_') and attr != 'disable':
                setattr(cls, attr, "")


class OutputFormatter:
    
    def __init__(self, use_colors: bool = True, verbose: bool = False):
        self.use_colors = use_colors
        self.verbose = verbose
        if not use_colors:
            Colors.disable()

    def header(self, text: str):
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.CYAN}{'═' * 60}{Colors.RESET}\n")

    def section(self, text: str):
        print(f"\n{Colors.BOLD}{Colors.WHITE}▶ {text}{Colors.RESET}")
        print(f"{Colors.DIM}{'─' * 40}{Colors.RESET}")

    def phase_start(self, phase_name: str, provider: str, model: str):
        print(f"\n{Colors.BOLD}{Colors.BLUE}┌─ Phase: {phase_name}{Colors.RESET}")
        print(f"{Colors.DIM}│  Provider: {provider} | Model: {model}{Colors.RESET}")
        print(f"{Colors.DIM}└{'─' * 50}{Colors.RESET}")

    def phase_output(self, content: str, stream: bool = False):
        if stream:
            sys.stdout.write(content)
            sys.stdout.flush()
        else:
            for line in content.split('\n'):
                print(f"{Colors.DIM}│{Colors.RESET} {line}")

    def phase_complete(self, phase_name: str, status: PhaseStatus, duration: float, cost: float):
        status_color = {
            PhaseStatus.COMPLETED: Colors.GREEN,
            PhaseStatus.FAILED: Colors.RED,
            PhaseStatus.SKIPPED: Colors.YELLOW,
        }.get(status, Colors.WHITE)
        
        status_icon = {
            PhaseStatus.COMPLETED: "✓",
            PhaseStatus.FAILED: "✗",
            PhaseStatus.SKIPPED: "⊘",
        }.get(status, "?")
        
        print(f"\n{status_color}{Colors.BOLD}{status_icon} {phase_name}: {status.value.upper()}{Colors.RESET}")
        print(f"{Colors.DIM}  Duration: {duration:.1f}s | Cost: ${cost:.4f}{Colors.RESET}")

    def workflow_status(self, status: WorkflowStatus, execution_id: str):
        status_color = {
            WorkflowStatus.RUNNING: Colors.BLUE,
            WorkflowStatus.COMPLETED: Colors.GREEN,
            WorkflowStatus.FAILED: Colors.RED,
            WorkflowStatus.PAUSED: Colors.YELLOW,
            WorkflowStatus.CANCELLED: Colors.YELLOW,
            WorkflowStatus.BUDGET_EXCEEDED: Colors.RED,
        }.get(status, Colors.WHITE)
        
        print(f"\n{status_color}{Colors.BOLD}Workflow Status: {status.value.upper()}{Colors.RESET}")
        print(f"{Colors.DIM}Execution ID: {execution_id}{Colors.RESET}")

    def budget_summary(self, summary: dict[str, Any]):
        print(f"\n{Colors.BOLD}Budget Summary{Colors.RESET}")
        print(f"  Total spent: {Colors.CYAN}${summary['total_spent']:.4f}{Colors.RESET}")
        if summary.get('budget_limit'):
            remaining = summary.get('remaining', 0)
            color = Colors.GREEN if remaining > 0 else Colors.RED
            print(f"  Remaining: {color}${remaining:.4f}{Colors.RESET}")
        print(f"  Tokens: {summary['total_tokens']:,} (in: {summary['tokens_input']:,}, out: {summary['tokens_output']:,})")

    def artifact_list(self, artifacts: list[dict[str, Any]]):
        if not artifacts:
            print(f"{Colors.DIM}No artifacts generated{Colors.RESET}")
            return
        
        print(f"\n{Colors.BOLD}Artifacts{Colors.RESET}")
        for a in artifacts:
            edited = f" {Colors.YELLOW}(edited){Colors.RESET}" if a.get('is_edited') else ""
            print(f"  • {a['name']} [{a['artifact_type']}]{edited}")
            if self.verbose and a.get('file_path'):
                print(f"    {Colors.DIM}{a['file_path']}{Colors.RESET}")

    def error(self, message: str):
        print(f"\n{Colors.RED}{Colors.BOLD}Error: {message}{Colors.RESET}")

    def success(self, message: str):
        print(f"\n{Colors.GREEN}{Colors.BOLD}✓ {message}{Colors.RESET}")

    def warning(self, message: str):
        print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ {message}{Colors.RESET}")

    def info(self, message: str):
        print(f"{Colors.CYAN}ℹ {message}{Colors.RESET}")

    def progress(self, current: int, total: int, phase_name: str):
        bar_width = 30
        filled = int(bar_width * current / total)
        bar = "█" * filled + "░" * (bar_width - filled)
        print(f"\r{Colors.BLUE}[{bar}]{Colors.RESET} {current}/{total} - {phase_name}", end="", flush=True)


class WorkflowCLI:
    
    def __init__(self, use_colors: bool = True, verbose: bool = False):
        self.formatter = OutputFormatter(use_colors=use_colors, verbose=verbose)
        self._current_phase: str = ""
        self._phase_start_time: datetime | None = None

    async def run_workflow(
        self,
        task_description: str,
        project_path: str | None = None,
        template_id: str | None = None,
        budget_limit: float | None = None,
        interactive: bool = False,
    ) -> bool:
        self.formatter.header("Multi-LLM Workflow Pipeline")
        
        project_path = project_path or str(Path.cwd())
        self.formatter.info(f"Project: {project_path}")
        self.formatter.info(f"Task: {task_description[:100]}...")
        
        orchestrator = WorkflowOrchestrator(
            on_phase_start=self._on_phase_start,
            on_phase_complete=self._on_phase_complete,
            on_phase_output=self._on_phase_output,
            on_workflow_status=self._on_workflow_status,
            on_approval_needed=self._on_approval_needed if interactive else None,
        )
        
        try:
            execution = orchestrator.create_execution(
                template_id=template_id,
                trigger_mode=TriggerMode.MANUAL_TASK,
                project_path=project_path,
                task_description=task_description,
                budget_limit=budget_limit,
                interactive_mode=interactive,
            )
            
            self.formatter.info(f"Execution ID: {execution.id}")
            self.formatter.info(f"Template: {execution.template_name}")
            
            if budget_limit:
                self.formatter.info(f"Budget limit: ${budget_limit:.2f}")
            
            self.formatter.section("Starting Workflow")
            
            result = await orchestrator.run(execution.id)
            
            self.formatter.section("Results")
            
            budget = orchestrator.get_budget_summary(execution.id)
            self.formatter.budget_summary(budget)
            
            artifacts = orchestrator.get_artifacts(execution.id)
            self.formatter.artifact_list([{
                'name': a.name,
                'artifact_type': a.artifact_type.value,
                'is_edited': a.is_edited,
                'file_path': a.file_path,
            } for a in artifacts])
            
            if result.status == WorkflowStatus.COMPLETED:
                self.formatter.success("Workflow completed successfully!")
                return True
            else:
                self.formatter.error(f"Workflow ended with status: {result.status.value}")
                return False
                
        except Exception as e:
            self.formatter.error(str(e))
            return False

    async def _on_phase_start(self, execution_id: str, phase: WorkflowPhase):
        self._current_phase = phase.name
        self._phase_start_time = datetime.now()
        self.formatter.phase_start(
            phase.name,
            phase.provider_config.provider_type.value,
            phase.provider_config.model_name or "default",
        )

    async def _on_phase_complete(self, execution_id: str, phase_exec: PhaseExecution):
        duration = 0.0
        if self._phase_start_time:
            duration = (datetime.now() - self._phase_start_time).total_seconds()
        
        self.formatter.phase_complete(
            phase_exec.phase_name,
            phase_exec.status,
            duration,
            phase_exec.cost_usd,
        )

    async def _on_phase_output(self, execution_id: str, phase_id: str, content: str):
        self.formatter.phase_output(content)

    async def _on_workflow_status(self, execution_id: str, status: WorkflowStatus):
        self.formatter.workflow_status(status, execution_id)

    async def _on_approval_needed(self, execution_id: str, message: str) -> bool:
        self.formatter.warning(message)
        response = input(f"{Colors.YELLOW}Continue? [y/N]: {Colors.RESET}").strip().lower()
        return response in ('y', 'yes')

    def list_templates(self, project_id: int | None = None):
        self.formatter.header("Workflow Templates")
        
        templates = template_manager.get_all(project_id)
        
        if not templates:
            self.formatter.info("No templates found")
            return
        
        for t in templates:
            default_marker = f" {Colors.GREEN}(default){Colors.RESET}" if t.is_default else ""
            scope = "global" if t.is_global else f"project:{t.project_id}"
            
            print(f"\n{Colors.BOLD}{t.name}{Colors.RESET}{default_marker}")
            print(f"  {Colors.DIM}ID: {t.id} | Scope: {scope}{Colors.RESET}")
            print(f"  {t.description or 'No description'}")
            print(f"  Phases: {len(t.phases)}")
            
            for p in t.phases:
                print(f"    {p.order+1}. {p.name} ({p.provider_config.provider_type.value})")

    def list_executions(self, project_id: int | None = None, limit: int = 10):
        self.formatter.header("Recent Workflow Executions")
        
        executions = workflow_orchestrator.get_executions(project_id=project_id, limit=limit)
        
        if not executions:
            self.formatter.info("No executions found")
            return
        
        for e in executions:
            status_color = {
                WorkflowStatus.COMPLETED: Colors.GREEN,
                WorkflowStatus.FAILED: Colors.RED,
                WorkflowStatus.RUNNING: Colors.BLUE,
            }.get(e.status, Colors.WHITE)
            
            print(f"\n{Colors.BOLD}{e.id}{Colors.RESET} - {status_color}{e.status.value}{Colors.RESET}")
            print(f"  Template: {e.template_name}")
            print(f"  Created: {e.created_at}")
            print(f"  Cost: ${e.total_cost_usd:.4f} | Tokens: {e.total_tokens_input + e.total_tokens_output:,}")

    def show_execution(self, execution_id: str):
        self.formatter.header(f"Execution: {execution_id}")
        
        execution = workflow_orchestrator.get_execution(execution_id)
        if not execution:
            self.formatter.error("Execution not found")
            return
        
        status_color = {
            WorkflowStatus.COMPLETED: Colors.GREEN,
            WorkflowStatus.FAILED: Colors.RED,
        }.get(execution.status, Colors.WHITE)
        
        print(f"Status: {status_color}{execution.status.value}{Colors.RESET}")
        print(f"Template: {execution.template_name}")
        print(f"Trigger: {execution.trigger_mode.value}")
        print(f"Created: {execution.created_at}")
        if execution.completed_at:
            print(f"Completed: {execution.completed_at}")
        
        self.formatter.section("Phases")
        for pe in execution.phase_executions:
            status_icon = "✓" if pe.status == PhaseStatus.COMPLETED else "✗"
            print(f"  {status_icon} {pe.phase_name}: {pe.status.value}")
            print(f"    Provider: {pe.provider_used} | Model: {pe.model_used}")
            print(f"    Tokens: {pe.tokens_input + pe.tokens_output:,} | Cost: ${pe.cost_usd:.4f}")
            if pe.error_message:
                print(f"    {Colors.RED}Error: {pe.error_message}{Colors.RESET}")
        
        budget = workflow_orchestrator.get_budget_summary(execution_id)
        self.formatter.budget_summary(budget)
        
        artifacts = workflow_orchestrator.get_artifacts(execution_id)
        self.formatter.artifact_list([{
            'name': a.name,
            'artifact_type': a.artifact_type.value,
            'is_edited': a.is_edited,
            'file_path': a.file_path,
        } for a in artifacts])

    async def list_providers(self):
        self.formatter.header("LLM Providers")
        
        status = model_registry.get_provider_status()
        local = await model_registry.detect_local_providers()
        
        for name, info in status.items():
            configured = info.get('configured', False)
            ptype = info.get('type', 'unknown')
            
            status_str = f"{Colors.GREEN}configured{Colors.RESET}" if configured else f"{Colors.RED}not configured{Colors.RESET}"
            
            print(f"\n{Colors.BOLD}{name}{Colors.RESET} [{ptype}] - {status_str}")
            
            if name == 'ollama':
                available, models = local.get('ollama', (False, []))
                if available:
                    print(f"  {Colors.GREEN}Online{Colors.RESET} at {info.get('url', 'localhost')}")
                    print(f"  Models: {', '.join(models[:5])}{'...' if len(models) > 5 else ''}")
                else:
                    print(f"  {Colors.RED}Offline{Colors.RESET}")
            
            elif name == 'lm_studio':
                available, models = local.get('lm_studio', (False, []))
                if available:
                    print(f"  {Colors.GREEN}Online{Colors.RESET} at {info.get('url', 'localhost')}")
                    print(f"  Models: {', '.join(models[:5])}{'...' if len(models) > 5 else ''}")
                else:
                    print(f"  {Colors.RED}Offline{Colors.RESET}")

    def show_artifact(self, artifact_id: str):
        artifact = artifact_manager.get(artifact_id)
        if not artifact:
            self.formatter.error("Artifact not found")
            return
        
        self.formatter.header(f"Artifact: {artifact.name}")
        print(f"Type: {artifact.artifact_type.value}")
        print(f"Created: {artifact.created_at}")
        if artifact.is_edited:
            print(f"{Colors.YELLOW}(edited){Colors.RESET}")
        
        self.formatter.section("Content")
        print(artifact.content)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Autowrkers Workflow CLI")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    run_parser = subparsers.add_parser("run", help="Run a workflow")
    run_parser.add_argument("task", help="Task description")
    run_parser.add_argument("-p", "--project", help="Project path")
    run_parser.add_argument("-t", "--template", help="Template ID")
    run_parser.add_argument("-b", "--budget", type=float, help="Budget limit in USD")
    run_parser.add_argument("-i", "--interactive", action="store_true", help="Interactive mode")
    
    subparsers.add_parser("templates", help="List workflow templates")
    
    list_parser = subparsers.add_parser("list", help="List workflow executions")
    list_parser.add_argument("-n", "--limit", type=int, default=10, help="Number of results")
    
    show_parser = subparsers.add_parser("show", help="Show execution details")
    show_parser.add_argument("execution_id", help="Execution ID")
    
    subparsers.add_parser("providers", help="List LLM providers")
    
    artifact_parser = subparsers.add_parser("artifact", help="Show artifact content")
    artifact_parser.add_argument("artifact_id", help="Artifact ID")
    
    args = parser.parse_args()
    
    cli = WorkflowCLI(use_colors=not args.no_color, verbose=args.verbose)
    
    if args.command == "run":
        success = asyncio.run(cli.run_workflow(
            task_description=args.task,
            project_path=args.project,
            template_id=args.template,
            budget_limit=args.budget,
            interactive=args.interactive,
        ))
        sys.exit(0 if success else 1)
    
    elif args.command == "templates":
        cli.list_templates()
    
    elif args.command == "list":
        cli.list_executions(limit=args.limit)
    
    elif args.command == "show":
        cli.show_execution(args.execution_id)
    
    elif args.command == "providers":
        asyncio.run(cli.list_providers())
    
    elif args.command == "artifact":
        cli.show_artifact(args.artifact_id)
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
