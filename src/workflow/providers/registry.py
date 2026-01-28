from datetime import datetime
from typing import Any

from .base import WorkflowLLMProvider, ModelInfo
from .claude_code import ClaudeCodeProvider, CLAUDE_CODE_MODELS
from .gemini import GeminiSDKProvider, GeminiOpenRouterProvider, GEMINI_MODELS
from .gemini_oauth import GeminiOAuthProvider
from .antigravity import AntigravityProvider, ANTIGRAVITY_MODELS
from .openai import OpenAIProvider, OpenRouterProvider, OPENAI_MODELS
from .ollama import OllamaProvider, detect_ollama
from .lm_studio import LMStudioProvider, detect_lm_studio
from .sdk_provider import ClaudeSDKProvider, CLAUDE_SDK_MODELS
from ..models import ProviderConfig, ProviderType, ProviderKeys
from ..oauth.manager import oauth_manager
from ...database import db


class ModelRegistry:
    
    def __init__(self):
        self._providers: dict[str, WorkflowLLMProvider] = {}
        self._keys: ProviderKeys | None = None

    def _load_keys(self) -> ProviderKeys:
        if self._keys is None:
            data = db.get_provider_keys()
            if data:
                self._keys = ProviderKeys(
                    gemini_api_key=data.get('gemini_api_key_encrypted', ''),
                    openai_api_key=data.get('openai_api_key_encrypted', ''),
                    openrouter_api_key=data.get('openrouter_api_key_encrypted', ''),
                    ollama_url=data.get('ollama_url', 'http://localhost:11434'),
                    lm_studio_url=data.get('lm_studio_url', 'http://localhost:1234/v1'),
                )
            else:
                self._keys = ProviderKeys()
        return self._keys

    def save_keys(self, keys: ProviderKeys):
        db.save_provider_keys({
            'gemini_api_key_encrypted': keys.gemini_api_key,
            'openai_api_key_encrypted': keys.openai_api_key,
            'openrouter_api_key_encrypted': keys.openrouter_api_key,
            'ollama_url': keys.ollama_url,
            'lm_studio_url': keys.lm_studio_url,
        })
        self._keys = keys

    def create_provider(self, config: ProviderConfig) -> WorkflowLLMProvider:
        keys = self._load_keys()
        api_key = keys.get_key(config.provider_type)
        
        if config.provider_type == ProviderType.GEMINI_SDK:
            return GeminiSDKProvider(config, api_key)
        elif config.provider_type == ProviderType.GEMINI_OPENROUTER:
            return GeminiOpenRouterProvider(config, api_key)
        elif config.provider_type == ProviderType.GEMINI_OAUTH:
            return GeminiOAuthProvider(config, oauth_manager)
        elif config.provider_type == ProviderType.ANTIGRAVITY:
            return AntigravityProvider(config, oauth_manager)
        elif config.provider_type == ProviderType.OPENAI:
            return OpenAIProvider(config, api_key)
        elif config.provider_type == ProviderType.OPENROUTER:
            return OpenRouterProvider(config, api_key)
        elif config.provider_type == ProviderType.OLLAMA:
            if not config.api_url:
                config.api_url = keys.ollama_url
            return OllamaProvider(config)
        elif config.provider_type == ProviderType.LM_STUDIO:
            if not config.api_url:
                config.api_url = keys.lm_studio_url
            return LMStudioProvider(config)
        elif config.provider_type == ProviderType.CLAUDE_SDK:
            return ClaudeSDKProvider(config)
        elif config.provider_type == ProviderType.CLAUDE_CODE:
            return ClaudeCodeProvider(config)
        elif config.provider_type == ProviderType.NONE:
            raise ValueError("Provider type 'none' cannot be used for generation")
        else:
            raise ValueError(f"Unsupported provider type: {config.provider_type}")

    async def refresh_models(self, provider_type: ProviderType) -> list[ModelInfo]:
        db.mark_models_unavailable(provider_type.value)
        
        keys = self._load_keys()
        models: list[ModelInfo] = []
        
        if provider_type == ProviderType.GEMINI_SDK:
            for model_id, info in GEMINI_MODELS.items():
                models.append(ModelInfo(
                    model_id=model_id,
                    model_name=model_id,
                    provider=provider_type.value,
                    context_length=int(info["context"]),
                    supports_tools=True,
                    supports_vision=True,
                    cost_input_per_1k=info["input"],
                    cost_output_per_1k=info["output"],
                ))
        
        elif provider_type == ProviderType.OPENAI:
            for model_id, info in OPENAI_MODELS.items():
                models.append(ModelInfo(
                    model_id=model_id,
                    model_name=model_id,
                    provider=provider_type.value,
                    context_length=int(info["context"]),
                    supports_tools=True,
                    supports_vision=model_id.startswith("gpt-4"),
                    cost_input_per_1k=info["input"],
                    cost_output_per_1k=info["output"],
                ))
        
        elif provider_type == ProviderType.OPENROUTER:
            if keys.openrouter_api_key:
                config = ProviderConfig(provider_type=ProviderType.OPENROUTER)
                provider = OpenRouterProvider(config, keys.openrouter_api_key)
                try:
                    models = await provider.list_models()
                finally:
                    await provider.close()
        
        elif provider_type == ProviderType.OLLAMA:
            config = ProviderConfig(
                provider_type=ProviderType.OLLAMA,
                api_url=keys.ollama_url,
            )
            provider = OllamaProvider(config)
            try:
                if await provider.check_health():
                    models = await provider.list_models()
            finally:
                await provider.close()
        
        elif provider_type == ProviderType.LM_STUDIO:
            config = ProviderConfig(
                provider_type=ProviderType.LM_STUDIO,
                api_url=keys.lm_studio_url,
            )
            provider = LMStudioProvider(config)
            try:
                if await provider.check_health():
                    models = await provider.list_models()
            finally:
                await provider.close()
        
        elif provider_type == ProviderType.CLAUDE_SDK:
            config = ProviderConfig(provider_type=ProviderType.CLAUDE_SDK)
            provider = ClaudeSDKProvider(config)
            models = await provider.list_models()

        elif provider_type == ProviderType.CLAUDE_CODE:
            config = ProviderConfig(provider_type=ProviderType.CLAUDE_CODE)
            provider = ClaudeCodeProvider(config)
            models = await provider.list_models()

        elif provider_type == ProviderType.ANTIGRAVITY:
            for model_id, info in ANTIGRAVITY_MODELS.items():
                models.append(ModelInfo(
                    model_id=model_id,
                    model_name=info.get("name", model_id),
                    provider=provider_type.value,
                    context_length=int(info["context"]),
                    supports_tools=True,
                    supports_vision=info.get("family") == "gemini",
                    cost_input_per_1k=info["input"],
                    cost_output_per_1k=info["output"],
                    metadata={"family": info.get("family", "unknown")},
                ))

        for model in models:
            db.upsert_model({
                'provider': provider_type.value,
                'model_id': model.model_id,
                'model_name': model.model_name,
                'context_length': model.context_length,
                'supports_tools': model.supports_tools,
                'supports_vision': model.supports_vision,
                'cost_input_per_1k': model.cost_input_per_1k,
                'cost_output_per_1k': model.cost_output_per_1k,
                'is_available': True,
                'last_checked': datetime.now().isoformat(),
                'metadata': model.metadata,
            })
        
        return models

    async def refresh_all_models(self) -> dict[str, list[ModelInfo]]:
        result: dict[str, list[ModelInfo]] = {}
        
        for ptype in [
            ProviderType.CLAUDE_CODE,
            ProviderType.CLAUDE_SDK,
            ProviderType.GEMINI_SDK,
            ProviderType.OPENAI,
            ProviderType.OPENROUTER,
            ProviderType.OLLAMA,
            ProviderType.LM_STUDIO,
            ProviderType.ANTIGRAVITY,
        ]:
            try:
                models = await self.refresh_models(ptype)
                result[ptype.value] = models
            except Exception:
                result[ptype.value] = []
        
        return result

    def get_cached_models(self, provider_type: ProviderType | None = None) -> list[ModelInfo]:
        if provider_type:
            data = db.get_models_by_provider(provider_type.value)
        else:
            data = db.get_all_available_models()
        
        return [
            ModelInfo(
                model_id=m['model_id'],
                model_name=m['model_name'],
                provider=m['provider'],
                context_length=m['context_length'],
                supports_tools=m['supports_tools'],
                supports_vision=m['supports_vision'],
                cost_input_per_1k=m['cost_input_per_1k'],
                cost_output_per_1k=m['cost_output_per_1k'],
                metadata=m.get('metadata', {}),
            )
            for m in data
        ]

    async def detect_local_providers(self) -> dict[str, tuple[bool, list[str]]]:
        keys = self._load_keys()
        
        ollama_available, ollama_models = await detect_ollama(keys.ollama_url)
        lm_studio_available, lm_studio_models = await detect_lm_studio(keys.lm_studio_url)
        
        return {
            'ollama': (ollama_available, ollama_models),
            'lm_studio': (lm_studio_available, lm_studio_models),
        }

    async def validate_provider(self, provider_type: ProviderType) -> tuple[bool, str]:
        keys = self._load_keys()
        
        if provider_type in (
            ProviderType.GEMINI_SDK,
            ProviderType.OPENAI,
            ProviderType.OPENROUTER,
            ProviderType.GEMINI_OPENROUTER,
        ):
            if not keys.has_key(provider_type):
                return False, f"API key not configured for {provider_type.value}"
        
        config = ProviderConfig(provider_type=provider_type)
        if provider_type == ProviderType.OLLAMA:
            config.api_url = keys.ollama_url
        elif provider_type == ProviderType.LM_STUDIO:
            config.api_url = keys.lm_studio_url
        
        provider = self.create_provider(config)
        
        try:
            return await provider.validate_config()
        finally:
            close_method = getattr(provider, 'close', None)
            if close_method:
                await close_method()

    def get_provider_status(self) -> dict[str, dict[str, Any]]:
        keys = self._load_keys()
        
        return {
            'gemini_sdk': {
                'configured': bool(keys.gemini_api_key),
                'type': 'cloud',
            },
            'openai': {
                'configured': bool(keys.openai_api_key),
                'type': 'cloud',
            },
            'openrouter': {
                'configured': bool(keys.openrouter_api_key),
                'type': 'cloud',
            },
            'ollama': {
                'configured': True,
                'url': keys.ollama_url,
                'type': 'local',
            },
            'lm_studio': {
                'configured': True,
                'url': keys.lm_studio_url,
                'type': 'local',
            },
            'claude_code': {
                'configured': True,
                'type': 'cli',
            },
            'claude_sdk': {
                'configured': True,
                'type': 'sdk',
            },
            'antigravity': {
                'configured': oauth_manager.is_authenticated("antigravity"),
                'type': 'oauth',
            },
        }


model_registry = ModelRegistry()
