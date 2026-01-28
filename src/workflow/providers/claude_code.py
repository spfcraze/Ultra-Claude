"""Claude Code CLI provider for workflow phases.

Uses `claude -p` (print/non-interactive mode) to execute prompts via
the Claude Code CLI. This provider runs locally and requires no API key
since Claude Code handles its own authentication.
"""

import asyncio
import json
import logging
import shutil
from typing import AsyncIterator

from .base import WorkflowLLMProvider, GenerationResult, ModelInfo, ProviderStatus
from ..models import ProviderConfig

logger = logging.getLogger("autowrkers.workflow")

CLAUDE_CODE_MODELS: dict[str, dict] = {
    "claude-sonnet-4-20250514": {"context": 200000, "input": 0.003, "output": 0.015},
    "claude-opus-4-20250514": {"context": 200000, "input": 0.015, "output": 0.075},
    "claude-opus-4-5-20251101": {"context": 200000, "input": 0.015, "output": 0.075},
    "claude-3-5-haiku-20241022": {"context": 200000, "input": 0.001, "output": 0.005},
}

# Default model alias used when none specified
DEFAULT_MODEL = "sonnet"


def _claude_available() -> bool:
    """Check if claude CLI is on PATH."""
    return shutil.which("claude") is not None


class ClaudeCodeProvider(WorkflowLLMProvider):
    """Workflow provider that calls `claude -p` for each generation."""

    def __init__(self, config: ProviderConfig, api_key: str = ""):
        super().__init__(config, api_key)
        self._working_dir: str = config.extra_params.get("working_dir", "") if config.extra_params else ""

    # ── generation ───────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        if not _claude_available():
            self._last_error = "claude CLI not found on PATH"
            await self._set_status(ProviderStatus.ERROR)
            raise RuntimeError(self._last_error)

        await self._set_status(ProviderStatus.GENERATING)

        cmd = self._build_cmd(system_prompt, max_tokens, output_format="json")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir or None,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=self.config.timeout if hasattr(self.config, "timeout") and self.config.timeout else 600,
            )

            if proc.returncode != 0:
                err = stderr.decode().strip()
                self._last_error = f"claude exited {proc.returncode}: {err}"
                await self._set_status(ProviderStatus.ERROR)
                raise RuntimeError(self._last_error)

            raw = stdout.decode()
            content = self._parse_output(raw)

            await self._set_status(ProviderStatus.READY)
            return GenerationResult(
                content=content,
                tokens_input=0,
                tokens_output=0,
                model_used=self.config.model_name or DEFAULT_MODEL,
                finish_reason="stop",
                raw_response={"source": "claude_code_cli"},
            )

        except asyncio.TimeoutError:
            self._last_error = "claude CLI timed out"
            await self._set_status(ProviderStatus.ERROR)
            raise
        except Exception as e:
            if not self._last_error:
                self._last_error = str(e)
            await self._set_status(ProviderStatus.ERROR)
            raise

    # ── streaming ────────────────────────────────────────────────

    async def _generate_stream_impl(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        if not _claude_available():
            self._last_error = "claude CLI not found on PATH"
            await self._set_status(ProviderStatus.ERROR)
            raise RuntimeError(self._last_error)

        await self._set_status(ProviderStatus.GENERATING)
        cmd = self._build_cmd(system_prompt, max_tokens, output_format="stream-json")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._working_dir or None,
            )
            # Write prompt and close stdin
            proc.stdin.write(prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()

            # Read streaming JSON lines
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode().strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                    # stream-json emits {"type":"content_block_delta","delta":{"text":"..."}}
                    # or simpler {"type":"result","result":"..."}
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            yield delta
                    elif data.get("type") == "result":
                        result_text = data.get("result", "")
                        if result_text:
                            yield result_text
                    elif isinstance(data.get("content"), str):
                        yield data["content"]
                except json.JSONDecodeError:
                    # Plain text fallback
                    yield text

            await proc.wait()
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

    # ── health & models ──────────────────────────────────────────

    async def check_health(self) -> bool:
        if not _claude_available():
            self._last_error = "claude CLI not found on PATH"
            return False
        return True

    async def list_models(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        for model_id, info in CLAUDE_CODE_MODELS.items():
            models.append(ModelInfo(
                model_id=model_id,
                model_name=model_id,
                provider="claude_code",
                context_length=info["context"],
                supports_tools=True,
                supports_vision=True,
                supports_streaming=True,
                cost_input_per_1k=info["input"],
                cost_output_per_1k=info["output"],
            ))
        return models

    async def close(self):
        pass  # No persistent state

    # ── internals ────────────────────────────────────────────────

    def _build_cmd(
        self,
        system_prompt: str | None,
        max_tokens: int | None,
        output_format: str = "text",
    ) -> list[str]:
        cmd = ["claude", "-p"]

        if self.config.model_name:
            cmd.extend(["--model", self.config.model_name])

        if system_prompt:
            cmd.extend(["--system-prompt", system_prompt])

        if output_format != "text":
            cmd.extend(["--output-format", output_format])

        # Skip permission prompts for automated workflow execution
        cmd.append("--dangerously-skip-permissions")

        return cmd

    @staticmethod
    def _parse_output(raw: str) -> str:
        """Parse claude CLI output. Handles both plain text and JSON."""
        raw = raw.strip()
        if not raw:
            return ""

        # Try JSON output first (from --output-format json)
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                # {"type":"result","result":"..."} format
                if "result" in data:
                    return data["result"]
                if "content" in data:
                    return data["content"]
            return raw
        except (json.JSONDecodeError, TypeError):
            return raw
