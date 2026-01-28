from typing import AsyncIterator
import json

from .base import WorkflowLLMProvider, GenerationResult, ModelInfo, ProviderStatus
from ..models import ProviderConfig, ProviderType

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


OPENAI_MODELS = {
    "gpt-4o": {"context": 128000, "input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"context": 128000, "input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"context": 128000, "input": 0.01, "output": 0.03},
    "gpt-4": {"context": 8192, "input": 0.03, "output": 0.06},
    "gpt-3.5-turbo": {"context": 16385, "input": 0.0005, "output": 0.0015},
    "o1-preview": {"context": 128000, "input": 0.015, "output": 0.06},
    "o1-mini": {"context": 128000, "input": 0.003, "output": 0.012},
}


class OpenAIProvider(WorkflowLLMProvider):
    
    OPENAI_URL = "https://api.openai.com/v1"

    def __init__(self, config: ProviderConfig, api_key: str = ""):
        super().__init__(config, api_key)
        self._client: "httpx.AsyncClient | None" = None

    async def _ensure_client(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx package not installed. Run: pip install httpx")
        
        if self._client is None:
            base_url = self.config.api_url or self.OPENAI_URL
            self._client = httpx.AsyncClient(
                base_url=base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=120.0,
            )

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        await self._ensure_client()
        await self._set_status(ProviderStatus.GENERATING)
        
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            model = self.config.model_name or "gpt-4o"
            
            payload: dict = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens or 8192,
            }
            
            if not model.startswith("o1"):
                payload["temperature"] = temperature or self.config.temperature
            
            assert self._client is not None
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            
            usage = data.get("usage", {})
            choice = data.get("choices", [{}])[0]
            
            await self._set_status(ProviderStatus.READY)
            return GenerationResult(
                content=choice.get("message", {}).get("content", ""),
                tokens_input=usage.get("prompt_tokens", 0),
                tokens_output=usage.get("completion_tokens", 0),
                model_used=data.get("model", model),
                finish_reason=choice.get("finish_reason", "unknown"),
                raw_response=data,
            )
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
        
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            model = self.config.model_name or "gpt-4o"
            
            payload: dict = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens or 8192,
                "stream": True,
            }
            
            if not model.startswith("o1"):
                payload["temperature"] = temperature or self.config.temperature
            
            assert self._client is not None
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
            
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
        try:
            await self._ensure_client()
            assert self._client is not None
            response = await self._client.get("/models")
            return response.status_code == 200
        except Exception as e:
            self._last_error = str(e)
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            await self._ensure_client()
            assert self._client is not None
            response = await self._client.get("/models")
            
            if response.status_code != 200:
                return self._get_static_models()
            
            data = response.json()
            models = []
            
            for m in data.get("data", []):
                model_id = m.get("id", "")
                if model_id in OPENAI_MODELS:
                    info = OPENAI_MODELS[model_id]
                    models.append(ModelInfo(
                        model_id=model_id,
                        model_name=model_id,
                        provider="openai",
                        context_length=info["context"],
                        supports_tools=True,
                        supports_vision=model_id.startswith("gpt-4"),
                        supports_streaming=not model_id.startswith("o1"),
                        cost_input_per_1k=info["input"],
                        cost_output_per_1k=info["output"],
                    ))
            
            return models if models else self._get_static_models()
        except Exception:
            return self._get_static_models()

    def _get_static_models(self) -> list[ModelInfo]:
        models = []
        for model_id, info in OPENAI_MODELS.items():
            models.append(ModelInfo(
                model_id=model_id,
                model_name=model_id,
                provider="openai",
                context_length=info["context"],
                supports_tools=True,
                supports_vision=model_id.startswith("gpt-4"),
                supports_streaming=not model_id.startswith("o1"),
                cost_input_per_1k=info["input"],
                cost_output_per_1k=info["output"],
            ))
        return models

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class OpenRouterProvider(WorkflowLLMProvider):
    
    OPENROUTER_URL = "https://openrouter.ai/api/v1"

    def __init__(self, config: ProviderConfig, api_key: str = ""):
        super().__init__(config, api_key)
        self._client: "httpx.AsyncClient | None" = None
        self._models_cache: list[ModelInfo] = []

    async def _ensure_client(self):
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx package not installed. Run: pip install httpx")
        
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "HTTP-Referer": "https://autowrkers.local",
                    "X-Title": "Autowrkers Workflow",
                },
                timeout=120.0,
            )

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> GenerationResult:
        await self._ensure_client()
        await self._set_status(ProviderStatus.GENERATING)
        
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.config.model_name or "anthropic/claude-3.5-sonnet",
                "messages": messages,
                "temperature": temperature or self.config.temperature,
                "max_tokens": max_tokens or 8192,
            }
            
            assert self._client is not None
            response = await self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            
            usage = data.get("usage", {})
            choice = data.get("choices", [{}])[0]
            
            await self._set_status(ProviderStatus.READY)
            return GenerationResult(
                content=choice.get("message", {}).get("content", ""),
                tokens_input=usage.get("prompt_tokens", 0),
                tokens_output=usage.get("completion_tokens", 0),
                model_used=data.get("model", self.config.model_name),
                finish_reason=choice.get("finish_reason", "unknown"),
                raw_response=data,
            )
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
        
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.config.model_name or "anthropic/claude-3.5-sonnet",
                "messages": messages,
                "temperature": temperature or self.config.temperature,
                "max_tokens": max_tokens or 8192,
                "stream": True,
            }
            
            assert self._client is not None
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
            
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
        try:
            await self._ensure_client()
            assert self._client is not None
            response = await self._client.get("/models")
            return response.status_code == 200
        except Exception as e:
            self._last_error = str(e)
            return False

    async def list_models(self) -> list[ModelInfo]:
        if self._models_cache:
            return self._models_cache
        
        try:
            await self._ensure_client()
            assert self._client is not None
            response = await self._client.get("/models")
            
            if response.status_code != 200:
                return []
            
            data = response.json()
            models = []
            
            for m in data.get("data", []):
                pricing = m.get("pricing", {})
                models.append(ModelInfo(
                    model_id=m.get("id", ""),
                    model_name=m.get("name", m.get("id", "")),
                    provider="openrouter",
                    context_length=m.get("context_length", 8192),
                    supports_tools=True,
                    supports_vision="vision" in m.get("id", "").lower(),
                    supports_streaming=True,
                    cost_input_per_1k=float(pricing.get("prompt", 0)) * 1000,
                    cost_output_per_1k=float(pricing.get("completion", 0)) * 1000,
                    metadata={"description": m.get("description", "")},
                ))
            
            self._models_cache = models
            return models
        except Exception as e:
            self._last_error = str(e)
            return []

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


def create_openai_provider(config: ProviderConfig, api_key: str) -> WorkflowLLMProvider:
    if config.provider_type == ProviderType.OPENAI:
        return OpenAIProvider(config, api_key)
    elif config.provider_type == ProviderType.OPENROUTER:
        return OpenRouterProvider(config, api_key)
    else:
        raise ValueError(f"Invalid provider type for OpenAI/OpenRouter: {config.provider_type}")
