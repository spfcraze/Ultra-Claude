"""
Antigravity Provider

Workflow provider that routes requests through Google's Cloud Code Assist (Antigravity) API.
Supports Claude (Sonnet 4.5, Opus 4.5), Gemini 3, and Gemini 2.5 models without requiring
a Google Cloud API key.

Requests are wrapped in the Antigravity envelope format:
  {project, model, request, requestType: "agent"}

and sent to the v1internal endpoints.
"""
import json
import logging
from typing import AsyncIterator
from uuid import uuid4

from .base import WorkflowLLMProvider, GenerationResult, ModelInfo, ProviderStatus
from ..models import ProviderConfig
from ..oauth.manager import OAuthManager, oauth_manager
from ..oauth.flows.antigravity import (
    unpack_refresh_token,
    ANTIGRAVITY_HEADERS,
    ANTIGRAVITY_ENDPOINTS,
    DEFAULT_PROJECT_ID,
)

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

logger = logging.getLogger("autowrkers.antigravity.provider")

# Default API endpoint (daily sandbox, same as CLIProxy/Vibeproxy)
DEFAULT_ENDPOINT = "https://daily-cloudcode-pa.sandbox.googleapis.com"

ANTIGRAVITY_MODELS = {
    # Claude models (via Antigravity proxy) - $0 cost
    "claude-sonnet-4-5": {
        "context": 200000, "input": 0.0, "output": 0.0,
        "family": "claude", "name": "Claude Sonnet 4.5",
    },
    "claude-sonnet-4-5-thinking": {
        "context": 200000, "input": 0.0, "output": 0.0,
        "family": "claude", "name": "Claude Sonnet 4.5 (Thinking)",
    },
    "claude-opus-4-5-thinking": {
        "context": 200000, "input": 0.0, "output": 0.0,
        "family": "claude", "name": "Claude Opus 4.5 (Thinking)",
    },
    # Gemini 3 models
    "gemini-3-pro": {
        "context": 1048576, "input": 0.0, "output": 0.0,
        "family": "gemini", "name": "Gemini 3 Pro",
    },
    "gemini-3-flash": {
        "context": 1048576, "input": 0.0, "output": 0.0,
        "family": "gemini", "name": "Gemini 3 Flash",
    },
    # Gemini 2.5 models
    "gemini-2.5-pro": {
        "context": 1048576, "input": 0.0, "output": 0.0,
        "family": "gemini", "name": "Gemini 2.5 Pro",
    },
    "gemini-2.5-flash": {
        "context": 1048576, "input": 0.0, "output": 0.0,
        "family": "gemini", "name": "Gemini 2.5 Flash",
    },
}


class AntigravityError(Exception):
    pass


class AntigravityProvider(WorkflowLLMProvider):
    """Provider that routes requests through Antigravity (Cloud Code Assist) API."""

    def __init__(
        self,
        config: ProviderConfig,
        oauth_mgr: OAuthManager | None = None,
        user_id: str = "default",
    ):
        super().__init__(config, api_key="")
        self._oauth_manager = oauth_mgr or oauth_manager
        self._user_id = user_id
        self._client: "httpx.AsyncClient | None" = None
        self._endpoint = DEFAULT_ENDPOINT
        self._project_id: str | None = None

    async def _ensure_client(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx package not installed. Run: pip install httpx")
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=120.0)

    async def _get_access_token(self) -> str:
        token = await self._oauth_manager.get_valid_access_token("antigravity", self._user_id)
        if not token:
            raise AntigravityError(
                "Antigravity not authenticated. Connect via the Workflows page."
            )
        return token

    def _get_project_id(self) -> str:
        """Get project ID from stored token's packed refresh_token."""
        if self._project_id:
            return self._project_id

        from ..oauth.storage import oauth_storage
        token = oauth_storage.load_token("antigravity", self._user_id)
        if token and token.refresh_token:
            _, project_id = unpack_refresh_token(token.refresh_token)
            self._project_id = project_id
            return project_id

        return DEFAULT_PROJECT_ID

    async def _get_headers(self) -> dict[str, str]:
        access_token = await self._get_access_token()
        return {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            **ANTIGRAVITY_HEADERS,
        }

    def _build_url(self, action: str, streaming: bool = False) -> str:
        url = f"{self._endpoint}/v1internal:{action}"
        if streaming:
            url += "?alt=sse"
        return url

    def _build_contents(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> list[dict]:
        """Build Gemini-format contents array."""
        contents = []
        if system_prompt:
            contents.append({
                "role": "user",
                "parts": [{"text": f"System: {system_prompt}"}],
            })
            contents.append({
                "role": "model",
                "parts": [{"text": "Understood. I will follow those instructions."}],
            })
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}],
        })
        return contents

    def _build_request_payload(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Build the inner request payload (standard Generative Language API format)."""
        payload: dict = {
            "contents": self._build_contents(prompt, system_prompt),
            "generationConfig": {
                "temperature": temperature or self.config.temperature,
                "maxOutputTokens": max_tokens or 8192,
            },
        }
        return payload

    def _wrap_antigravity(self, model: str, request_payload: dict) -> dict:
        """Wrap a request payload in the Antigravity envelope."""
        project_id = self._get_project_id()
        return {
            "project": project_id,
            "model": model,
            "request": request_payload,
            "requestType": "agent",
            "userAgent": "antigravity",
            "requestId": f"agent-{uuid4()}",
        }

    def _get_model(self) -> str:
        """Get the model name for the API request."""
        return self.config.model_name or "gemini-2.5-flash"

    def _extract_response(self, data: dict) -> dict:
        """Extract the actual response from potentially wrapped Antigravity envelope."""
        # Antigravity may wrap in {response: {...}} or return directly
        if "response" in data and isinstance(data["response"], dict):
            return data["response"]
        return data

    def _extract_content(self, response_data: dict) -> tuple[str, int, int, str]:
        """Extract content, token counts, and finish reason from response."""
        candidates = response_data.get("candidates", [])
        if not candidates:
            return "", 0, 0, "UNKNOWN"

        content_parts = candidates[0].get("content", {}).get("parts", [])
        content = "".join(part.get("text", "") for part in content_parts)

        usage = response_data.get("usageMetadata", {})
        tokens_in = usage.get("promptTokenCount", 0)
        tokens_out = usage.get("candidatesTokenCount", 0)
        finish_reason = candidates[0].get("finishReason", "UNKNOWN")

        return content, tokens_in, tokens_out, finish_reason

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        await self._ensure_client()
        await self._set_status(ProviderStatus.GENERATING)

        model = self._get_model()

        try:
            headers = await self._get_headers()
            request_payload = self._build_request_payload(
                prompt, system_prompt, temperature, max_tokens
            )
            wrapped = self._wrap_antigravity(model, request_payload)
            url = self._build_url("generateContent")

            assert self._client is not None
            response = await self._client.post(url, json=wrapped, headers=headers)

            if response.status_code == 401:
                raise AntigravityError("Antigravity token expired or invalid. Please re-authenticate.")

            if response.status_code == 429:
                raise AntigravityError("Antigravity rate limited. Try again later.")

            if response.status_code != 200:
                error_text = response.text[:500]
                # Try fallback endpoints
                for fallback in ANTIGRAVITY_ENDPOINTS:
                    if fallback == self._endpoint:
                        continue
                    try:
                        fallback_url = f"{fallback}/v1internal:generateContent"
                        response = await self._client.post(
                            fallback_url, json=wrapped, headers=headers
                        )
                        if response.status_code == 200:
                            self._endpoint = fallback
                            break
                    except Exception:
                        continue
                else:
                    raise AntigravityError(
                        f"Antigravity API error ({response.status_code}): {error_text}"
                    )

            data = response.json()
            response_data = self._extract_response(data)
            content, tokens_in, tokens_out, finish_reason = self._extract_content(response_data)

            await self._set_status(ProviderStatus.READY)
            return GenerationResult(
                content=content,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                model_used=model,
                finish_reason=finish_reason,
                raw_response=data,
            )
        except AntigravityError:
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
        await self._ensure_client()
        await self._set_status(ProviderStatus.GENERATING)

        model = self._get_model()

        try:
            headers = await self._get_headers()
            request_payload = self._build_request_payload(
                prompt, system_prompt, temperature, max_tokens
            )
            wrapped = self._wrap_antigravity(model, request_payload)
            url = self._build_url("streamGenerateContent", streaming=True)

            assert self._client is not None
            async with self._client.stream(
                "POST", url, json=wrapped, headers=headers
            ) as response:
                if response.status_code == 401:
                    raise AntigravityError(
                        "Antigravity token expired or invalid. Please re-authenticate."
                    )

                if response.status_code == 429:
                    raise AntigravityError("Antigravity rate limited. Try again later.")

                if response.status_code != 200:
                    body = await response.aread()
                    raise AntigravityError(
                        f"Antigravity streaming error ({response.status_code}): {body[:500]}"
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        data = json.loads(data_str)
                        response_data = self._extract_response(data)
                        candidates = response_data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for part in parts:
                                text = part.get("text", "")
                                if text:
                                    yield text
                    except json.JSONDecodeError:
                        continue

            await self._set_status(ProviderStatus.READY)
        except AntigravityError:
            await self._set_status(ProviderStatus.ERROR)
            raise
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
        """Check health by calling loadCodeAssist endpoint."""
        try:
            await self._ensure_client()
            headers = await self._get_headers()
            assert self._client is not None

            body = json.dumps({
                "metadata": {
                    "ideType": "IDE_UNSPECIFIED",
                    "platform": "PLATFORM_UNSPECIFIED",
                    "pluginType": "GEMINI",
                }
            })

            for endpoint in ANTIGRAVITY_ENDPOINTS:
                try:
                    url = f"{endpoint}/v1internal:loadCodeAssist"
                    response = await self._client.post(
                        url, content=body, headers=headers
                    )
                    if response.status_code == 200:
                        self._endpoint = endpoint
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            self._last_error = str(e)
            return False

    async def validate_config(self) -> tuple[bool, str]:
        if not self._oauth_manager.is_authenticated("antigravity", self._user_id):
            return False, "Antigravity not configured. Please authenticate first."

        try:
            healthy = await self.check_health()
            if healthy:
                return True, "Antigravity authentication valid"
            return False, "Antigravity API unreachable"
        except Exception as e:
            return False, str(e)

    async def list_models(self) -> list[ModelInfo]:
        models = []
        for model_id, info in ANTIGRAVITY_MODELS.items():
            models.append(ModelInfo(
                model_id=model_id,
                model_name=info.get("name", model_id),
                provider="antigravity",
                context_length=int(info["context"]),
                supports_tools=True,
                supports_vision=info.get("family") == "gemini",
                supports_streaming=True,
                cost_input_per_1k=info["input"],
                cost_output_per_1k=info["output"],
                metadata={"family": info.get("family", "unknown")},
            ))
        return models

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
