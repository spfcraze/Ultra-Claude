from typing import AsyncIterator, Optional, Callable, Awaitable, TypedDict
from dataclasses import dataclass, field

from .base import WorkflowLLMProvider, GenerationResult, ModelInfo, ProviderStatus
from ..models import ProviderConfig
from ..sdk_bridge import sdk_bridge, SDKConfig
from ..sdk_models import SDKTodo


class _ModelSpec(TypedDict):
    context: int
    input: float
    output: float


CLAUDE_SDK_MODELS: dict[str, _ModelSpec] = {
    "claude-sonnet-4-20250514": {"context": 200000, "input": 0.003, "output": 0.015},
    "claude-3-5-sonnet-20241022": {"context": 200000, "input": 0.003, "output": 0.015},
    "claude-3-5-haiku-20241022": {"context": 200000, "input": 0.001, "output": 0.005},
}


@dataclass
class SDKGenerationResult(GenerationResult):
    todos: list[SDKTodo] = field(default_factory=list)
    workflow_execution_id: str = ""


class ClaudeSDKProvider(WorkflowLLMProvider):

    def __init__(
        self,
        config: ProviderConfig,
        api_key: str = "",
        sdk_config: Optional[SDKConfig] = None,
        on_todo_update: Optional[Callable[[str, list[SDKTodo]], Awaitable[None]]] = None,
    ):
        super().__init__(config, api_key)
        self._sdk_config = sdk_config or SDKConfig()
        self._on_todo_update = on_todo_update
        self._current_workflow_id: str = ""
        self._accumulated_todos: list[SDKTodo] = []

    def set_workflow_execution_id(self, workflow_id: str):
        self._current_workflow_id = workflow_id
        self._accumulated_todos = []

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> SDKGenerationResult:
        if not sdk_bridge.is_sdk_available():
            self._last_error = "claude-agent-sdk not installed"
            await self._set_status(ProviderStatus.ERROR)
            raise RuntimeError(self._last_error)

        await self._set_status(ProviderStatus.GENERATING)

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        workflow_id = self._current_workflow_id or "default"
        content_parts: list[str] = []
        self._accumulated_todos = []

        try:
            async for content_chunk, todos_updated in sdk_bridge.query(
                prompt=full_prompt,
                workflow_execution_id=workflow_id,
            ):
                if content_chunk:
                    content_parts.append(content_chunk)

                if todos_updated:
                    self._accumulated_todos = todos_updated
                    if self._on_todo_update:
                        await self._on_todo_update(workflow_id, todos_updated)

            await self._set_status(ProviderStatus.READY)

            full_content = "".join(content_parts)
            return SDKGenerationResult(
                content=full_content,
                tokens_input=0,
                tokens_output=0,
                model_used=self.config.model_name or "claude-sonnet-4-20250514",
                finish_reason="stop",
                raw_response={"todos_count": len(self._accumulated_todos)},
                todos=self._accumulated_todos,
                workflow_execution_id=workflow_id,
            )
        except TimeoutError as e:
            self._last_error = str(e)
            await self._set_status(ProviderStatus.ERROR)
            raise
        except Exception as e:
            self._last_error = str(e)
            await self._set_status(ProviderStatus.ERROR)
            raise

    async def _generate_stream_impl(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        if not sdk_bridge.is_sdk_available():
            self._last_error = "claude-agent-sdk not installed"
            await self._set_status(ProviderStatus.ERROR)
            raise RuntimeError(self._last_error)

        await self._set_status(ProviderStatus.GENERATING)

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        workflow_id = self._current_workflow_id or "default"
        self._accumulated_todos = []

        try:
            async for content_chunk, todos_updated in sdk_bridge.query(
                prompt=full_prompt,
                workflow_execution_id=workflow_id,
            ):
                if content_chunk:
                    yield content_chunk

                if todos_updated:
                    self._accumulated_todos = todos_updated
                    if self._on_todo_update:
                        await self._on_todo_update(workflow_id, todos_updated)

            await self._set_status(ProviderStatus.READY)
        except Exception as e:
            self._last_error = str(e)
            await self._set_status(ProviderStatus.ERROR)
            raise

    def generate_stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        return self._generate_stream_impl(prompt, system_prompt, temperature, max_tokens)

    async def check_health(self) -> bool:
        if not sdk_bridge.is_sdk_available():
            self._last_error = "claude-agent-sdk not installed"
            return False
        return True

    async def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for model_id, info in CLAUDE_SDK_MODELS.items():
            ctx_len: int = info["context"]
            cost_in: float = info["input"]
            cost_out: float = info["output"]
            models.append(ModelInfo(
                model_id=model_id,
                model_name=model_id,
                provider="claude_sdk",
                context_length=ctx_len,
                supports_tools=True,
                supports_vision=True,
                supports_streaming=True,
                cost_input_per_1k=cost_in,
                cost_output_per_1k=cost_out,
            ))
        return models

    def get_accumulated_todos(self) -> list[SDKTodo]:
        return self._accumulated_todos

    def get_todo_progress(self) -> tuple[int, int]:
        return sdk_bridge.get_progress(self._current_workflow_id)

    async def close(self):
        if self._current_workflow_id:
            sdk_bridge.clear_session(self._current_workflow_id)
