"""
LLM Provider Abstraction for Autowrkers

Provides a unified interface for different LLM backends:
- Claude Code (default, uses tmux + claude CLI)
- Ollama (local LLM)
- LM Studio (local LLM with OpenAI-compatible API)
- OpenRouter (cloud API for various models)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Dict, Any, Awaitable


class LLMProviderType(Enum):
    """Supported LLM provider types"""
    CLAUDE_CODE = "claude_code"    # Default: uses claude CLI via tmux
    OLLAMA = "ollama"              # Local Ollama instance
    LM_STUDIO = "lm_studio"        # LM Studio (OpenAI-compatible API)
    OPENROUTER = "openrouter"      # OpenRouter cloud API


# Default API URLs for each provider
DEFAULT_API_URLS = {
    LLMProviderType.OLLAMA: "http://localhost:11434",
    LLMProviderType.LM_STUDIO: "http://localhost:1234/v1",
    LLMProviderType.OPENROUTER: "https://openrouter.ai/api/v1",
}


@dataclass
class LLMProviderConfig:
    """Configuration for an LLM provider"""
    provider_type: LLMProviderType = LLMProviderType.CLAUDE_CODE
    model_name: str = ""           # e.g., "llama3.2:latest", "anthropic/claude-3.5-sonnet"
    api_url: str = ""              # API endpoint URL
    api_key: str = ""              # API key (required for OpenRouter)
    context_length: int = 8192     # Max context window
    temperature: float = 0.1       # Lower for coding tasks
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def get_api_url(self) -> str:
        """Get API URL, using default if not specified"""
        if self.api_url:
            return self.api_url
        return DEFAULT_API_URLS.get(self.provider_type, "")

    def is_local(self) -> bool:
        """Check if this is a local LLM provider"""
        return self.provider_type in (LLMProviderType.OLLAMA, LLMProviderType.LM_STUDIO)

    def requires_api_key(self) -> bool:
        """Check if this provider requires an API key"""
        return self.provider_type == LLMProviderType.OPENROUTER

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization"""
        return {
            "provider_type": self.provider_type.value,
            "model_name": self.model_name,
            "api_url": self.api_url,
            "api_key": self.api_key,  # Note: should be encrypted before storage
            "context_length": self.context_length,
            "temperature": self.temperature,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMProviderConfig":
        """Create from dictionary"""
        provider_type = data.get("provider_type", "claude_code")
        if isinstance(provider_type, str):
            provider_type = LLMProviderType(provider_type)

        return cls(
            provider_type=provider_type,
            model_name=data.get("model_name", ""),
            api_url=data.get("api_url", ""),
            api_key=data.get("api_key", ""),
            context_length=data.get("context_length", 8192),
            temperature=data.get("temperature", 0.1),
            extra_params=data.get("extra_params", {}),
        )


class LLMProviderStatus(Enum):
    """Status of an LLM provider session"""
    STARTING = "starting"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"   # Waiting for user input
    PROCESSING = "processing"          # Processing a request
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    Defines the interface that both Claude Code (tmux-based) and
    local LLM providers (API-based) must implement.
    """

    def __init__(self, config: LLMProviderConfig, working_dir: str):
        self.config = config
        self.working_dir = working_dir
        self._status = LLMProviderStatus.STOPPED
        self._output_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._status_callback: Optional[Callable[[LLMProviderStatus], Awaitable[None]]] = None
        self._last_output: str = ""

    @abstractmethod
    async def start(self, initial_prompt: Optional[str] = None) -> bool:
        """
        Start a new LLM session.

        Args:
            initial_prompt: Optional initial prompt to send after starting

        Returns:
            True if session started successfully
        """
        pass

    @abstractmethod
    async def send_input(self, message: str) -> bool:
        """
        Send input/message to the LLM.

        Args:
            message: The message or input to send

        Returns:
            True if input was sent successfully
        """
        pass

    @abstractmethod
    async def stop(self) -> bool:
        """
        Stop the session.

        Returns:
            True if session was stopped successfully
        """
        pass

    @property
    def status(self) -> LLMProviderStatus:
        """Get current status"""
        return self._status

    @property
    def last_output(self) -> str:
        """Get most recent output"""
        return self._last_output

    def is_running(self) -> bool:
        """Check if session is running"""
        return self._status in (
            LLMProviderStatus.RUNNING,
            LLMProviderStatus.WAITING_INPUT,
            LLMProviderStatus.PROCESSING
        )

    def needs_input(self) -> bool:
        """Check if session is waiting for input"""
        return self._status == LLMProviderStatus.WAITING_INPUT

    def set_output_callback(self, callback: Callable[[str], Awaitable[None]]):
        """
        Set callback for output streaming.

        The callback is called whenever new output is available from the LLM.
        """
        self._output_callback = callback

    def set_status_callback(self, callback: Callable[[LLMProviderStatus], Awaitable[None]]):
        """
        Set callback for status changes.

        The callback is called whenever the session status changes.
        """
        self._status_callback = callback

    async def _emit_output(self, output: str):
        """Emit output to callback"""
        self._last_output = output
        if self._output_callback:
            await self._output_callback(output)

    async def _set_status(self, status: LLMProviderStatus):
        """Set status and notify callback"""
        self._status = status
        if self._status_callback:
            await self._status_callback(status)


class ClaudeCodeProvider(LLMProvider):
    """
    Provider that wraps the existing Claude Code CLI functionality.

    This provider delegates to the existing tmux-based session management
    in session_manager.py, maintaining full backward compatibility.

    Note: This is a thin wrapper - the actual tmux management remains
    in session_manager.py to avoid duplicating that complex logic.
    """

    def __init__(self, config: LLMProviderConfig, working_dir: str):
        super().__init__(config, working_dir)
        self.session_id: Optional[int] = None
        self.tmux_session: Optional[str] = None

    async def start(self, initial_prompt: Optional[str] = None) -> bool:
        """
        Start is handled by session_manager.py for Claude Code.
        This method is called after the session is created.
        """
        await self._set_status(LLMProviderStatus.RUNNING)
        return True

    async def send_input(self, message: str) -> bool:
        """
        Input sending is handled by session_manager.py for Claude Code.
        This is a pass-through that just updates status.
        """
        await self._set_status(LLMProviderStatus.RUNNING)
        return True

    async def stop(self) -> bool:
        """
        Stopping is handled by session_manager.py for Claude Code.
        """
        await self._set_status(LLMProviderStatus.STOPPED)
        return True

    def set_session_info(self, session_id: int, tmux_session: str):
        """Set session info from session_manager"""
        self.session_id = session_id
        self.tmux_session = tmux_session


# Import LocalLLMProvider from agentic_runner to avoid circular imports
# The actual implementation is in agentic_runner.py
def get_provider(config: LLMProviderConfig, working_dir: str) -> LLMProvider:
    """
    Factory function to get the appropriate provider for a configuration.

    Args:
        config: LLM provider configuration
        working_dir: Working directory for the session

    Returns:
        An LLMProvider instance
    """
    if config.provider_type == LLMProviderType.CLAUDE_CODE:
        return ClaudeCodeProvider(config, working_dir)
    else:
        # Import here to avoid circular imports
        from .agentic_runner import LocalLLMProvider
        return LocalLLMProvider(config, working_dir)
