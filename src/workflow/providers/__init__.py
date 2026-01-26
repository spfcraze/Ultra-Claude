"""LLM Provider implementations for the workflow pipeline."""

from .base import WorkflowLLMProvider, GenerationResult, ModelInfo, ProviderStatus
from .gemini import GeminiSDKProvider, GeminiOpenRouterProvider
from .gemini_oauth import GeminiOAuthProvider
from .openai import OpenAIProvider, OpenRouterProvider
from .ollama import OllamaProvider, detect_ollama
from .lm_studio import LMStudioProvider, detect_lm_studio
from .sdk_provider import ClaudeSDKProvider, SDKGenerationResult
from .registry import ModelRegistry

__all__ = [
    # Base
    "WorkflowLLMProvider",
    "GenerationResult",
    "ModelInfo",
    "ProviderStatus",
    # Providers
    "GeminiSDKProvider",
    "GeminiOpenRouterProvider",
    "GeminiOAuthProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "OllamaProvider",
    "LMStudioProvider",
    "ClaudeSDKProvider",
    "SDKGenerationResult",
    # Detection
    "detect_ollama",
    "detect_lm_studio",
    # Registry
    "ModelRegistry",
]
