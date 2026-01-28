import asyncio
import logging
import re
from datetime import datetime
from typing import AsyncIterator, Callable, Awaitable, Any

logger = logging.getLogger("autowrkers.workflow")

from .models import (
    WorkflowPhase,
    PhaseExecution,
    PhaseStatus,
    Artifact,
    ArtifactType,
    generate_id,
)
from .artifact_manager import artifact_manager
from .budget_tracker import budget_manager
from .providers import WorkflowLLMProvider, GenerationResult
from .providers.registry import model_registry


class PhaseRunner:
    
    def __init__(
        self,
        workflow_execution_id: str,
        project_id: int | None = None,
        project_path: str = "",
        on_output: Callable[[str, str], Awaitable[None]] | None = None,
        on_status: Callable[[str, PhaseStatus], Awaitable[None]] | None = None,
    ):
        self.workflow_execution_id = workflow_execution_id
        self.project_id = project_id
        self.project_path = project_path
        self._on_output = on_output
        self._on_status = on_status
        self._providers: dict[str, WorkflowLLMProvider] = {}

    async def _emit_output(self, phase_id: str, content: str):
        if self._on_output:
            await self._on_output(phase_id, content)

    async def _emit_status(self, phase_id: str, status: PhaseStatus):
        if self._on_status:
            await self._on_status(phase_id, status)

    def _get_provider(self, phase: WorkflowPhase) -> WorkflowLLMProvider:
        key = f"{phase.provider_config.provider_type.value}:{phase.provider_config.model_name}"
        
        if key not in self._providers:
            self._providers[key] = model_registry.create_provider(phase.provider_config)
        
        return self._providers[key]

    def _build_prompt(
        self,
        phase: WorkflowPhase,
        task_description: str,
        artifacts: dict[str, Artifact],
    ) -> str:
        prompt = phase.prompt_template
        
        prompt = prompt.replace("{task_description}", task_description)
        prompt = prompt.replace("{project_path}", self.project_path)
        
        artifact_pattern = re.compile(r"\{artifact:(\w+)\}")
        
        def replace_artifact(match: re.Match[str]) -> str:
            artifact_name = match.group(1).lower()
            for name, artifact in artifacts.items():
                if artifact_name in name.lower():
                    return artifact.content
            return f"[Artifact '{artifact_name}' not found]"
        
        prompt = artifact_pattern.sub(replace_artifact, prompt)
        
        return prompt

    async def run_phase(
        self,
        phase: WorkflowPhase,
        task_description: str,
        input_artifacts: dict[str, Artifact],
        iteration: int = 1,
    ) -> PhaseExecution:
        phase_exec = PhaseExecution(
            id=generate_id(),
            workflow_execution_id=self.workflow_execution_id,
            phase_id=phase.id,
            phase_name=phase.name,
            phase_role=phase.role,
            provider_used=phase.provider_config.provider_type.value,
            model_used=phase.provider_config.model_name,
            status=PhaseStatus.RUNNING,
            iteration=iteration,
            input_artifact_ids=[a.id for a in input_artifacts.values()],
            started_at=datetime.now().isoformat(),
        )
        
        await self._emit_status(phase.id, PhaseStatus.RUNNING)
        
        try:
            provider = self._get_provider(phase)
            prompt = self._build_prompt(phase, task_description, input_artifacts)
            
            is_ok, remaining = budget_manager.check_budget(
                "execution", self.workflow_execution_id
            )
            if not is_ok:
                phase_exec.status = PhaseStatus.FAILED
                phase_exec.error_message = "Budget limit exceeded"
                phase_exec.completed_at = datetime.now().isoformat()
                await self._emit_status(phase.id, PhaseStatus.FAILED)
                return phase_exec
            
            result = await asyncio.wait_for(
                provider.generate(prompt),
                timeout=phase.timeout_seconds,
            )
            
            phase_exec.tokens_input = result.tokens_input
            phase_exec.tokens_output = result.tokens_output
            
            cost, budget_ok = budget_manager.record_execution_usage(
                self.workflow_execution_id,
                self.project_id,
                result.model_used or phase.provider_config.model_name,
                result.tokens_input,
                result.tokens_output,
            )
            phase_exec.cost_usd = cost
            
            await self._emit_output(phase.id, result.content)
            
            success = self._check_success(result.content, phase.success_pattern)
            
            if success:
                artifact = artifact_manager.create(
                    workflow_execution_id=self.workflow_execution_id,
                    phase_execution_id=phase_exec.id,
                    artifact_type=phase.output_artifact_type,
                    name=f"{phase.name}_output",
                    content=result.content,
                    metadata={
                        "model": result.model_used,
                        "tokens_input": result.tokens_input,
                        "tokens_output": result.tokens_output,
                        "cost_usd": cost,
                    },
                )
                phase_exec.output_artifact_id = artifact.id
                phase_exec.status = PhaseStatus.COMPLETED
            else:
                phase_exec.status = PhaseStatus.FAILED
                phase_exec.error_message = "Success pattern not found in output"
            
            if not budget_ok:
                phase_exec.error_message = (phase_exec.error_message or "") + " [Budget exceeded]"
            
        except asyncio.TimeoutError:
            phase_exec.status = PhaseStatus.FAILED
            phase_exec.error_message = f"Phase timed out after {phase.timeout_seconds}s"
        except Exception as e:
            phase_exec.status = PhaseStatus.FAILED
            phase_exec.error_message = str(e)
        
        phase_exec.completed_at = datetime.now().isoformat()
        await self._emit_status(phase.id, phase_exec.status)
        
        return phase_exec

    async def run_phase_streaming(
        self,
        phase: WorkflowPhase,
        task_description: str,
        input_artifacts: dict[str, Artifact],
        iteration: int = 1,
    ) -> AsyncIterator[tuple[str, PhaseExecution | None]]:
        phase_exec = PhaseExecution(
            id=generate_id(),
            workflow_execution_id=self.workflow_execution_id,
            phase_id=phase.id,
            phase_name=phase.name,
            phase_role=phase.role,
            provider_used=phase.provider_config.provider_type.value,
            model_used=phase.provider_config.model_name,
            status=PhaseStatus.RUNNING,
            iteration=iteration,
            input_artifact_ids=[a.id for a in input_artifacts.values()],
            started_at=datetime.now().isoformat(),
        )
        
        await self._emit_status(phase.id, PhaseStatus.RUNNING)
        
        try:
            provider = self._get_provider(phase)
            prompt = self._build_prompt(phase, task_description, input_artifacts)
            
            is_ok, _ = budget_manager.check_budget(
                "execution", self.workflow_execution_id
            )
            if not is_ok:
                phase_exec.status = PhaseStatus.FAILED
                phase_exec.error_message = "Budget limit exceeded"
                phase_exec.completed_at = datetime.now().isoformat()
                await self._emit_status(phase.id, PhaseStatus.FAILED)
                yield "", phase_exec
                return
            
            full_content = ""
            async for chunk in provider.generate_stream(prompt):
                full_content += chunk
                yield chunk, None
            
            success = self._check_success(full_content, phase.success_pattern)
            
            if success:
                artifact = artifact_manager.create(
                    workflow_execution_id=self.workflow_execution_id,
                    phase_execution_id=phase_exec.id,
                    artifact_type=phase.output_artifact_type,
                    name=f"{phase.name}_output",
                    content=full_content,
                )
                phase_exec.output_artifact_id = artifact.id
                phase_exec.status = PhaseStatus.COMPLETED
            else:
                phase_exec.status = PhaseStatus.FAILED
                phase_exec.error_message = "Success pattern not found in output"
            
        except Exception as e:
            phase_exec.status = PhaseStatus.FAILED
            phase_exec.error_message = str(e)
        
        phase_exec.completed_at = datetime.now().isoformat()
        await self._emit_status(phase.id, phase_exec.status)
        yield "", phase_exec

    def _check_success(self, content: str, pattern: str) -> bool:
        if not pattern:
            return True
        
        if pattern.startswith("/"):
            return pattern in content or pattern.lower() in content.lower()
        
        try:
            return bool(re.search(pattern, content, re.IGNORECASE))
        except re.error:
            return pattern in content

    async def cleanup(self):
        for provider in self._providers.values():
            close_method = getattr(provider, 'close', None)
            if close_method:
                try:
                    await close_method()
                except Exception as e:
                    logger.warning(f"Provider cleanup error: {e}")
        self._providers.clear()
