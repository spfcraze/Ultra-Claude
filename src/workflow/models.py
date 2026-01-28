"""
Workflow Data Models for Multi-LLM Pipeline

This module defines the data structures for:
- Workflow templates (reusable pipeline definitions)
- Workflow executions (running instances)
- Phases (individual steps in a workflow)
- Artifacts (outputs from phases)
- Budget tracking
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
import uuid


class TriggerMode(Enum):
    """How a workflow execution was triggered"""
    GITHUB_ISSUE = "github_issue"
    MANUAL_TASK = "manual_task"
    DIRECTORY_SCAN = "directory_scan"


class PhaseRole(Enum):
    """Role/purpose of a workflow phase"""
    ANALYZER = "analyzer"
    PLANNER = "planner"
    IMPLEMENTER = "implementer"
    REVIEWER_FUNC = "reviewer_functional"
    REVIEWER_STYLE = "reviewer_style"
    REVIEWER_SECURITY = "reviewer_security"
    REVIEWER_CUSTOM = "reviewer_custom"
    VERIFIER = "verifier"
    BROWSER_VERIFIER = "browser_verifier"


class PhaseStatus(Enum):
    """Status of a phase execution"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStatus(Enum):
    """Status of a workflow execution"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"


class ArtifactType(Enum):
    """Type of artifact produced by a phase"""
    TASK_LIST = "task_list"
    CODEBASE_DOCS = "codebase_docs"
    IMPLEMENTATION_PLAN = "implementation_plan"
    CODE_DIFF = "code_diff"
    REVIEW_REPORT = "review_report"
    VERIFICATION_REPORT = "verification_report"
    BROWSER_VERIFICATION_REPORT = "browser_verification_report"
    CUSTOM = "custom"


class IterationBehavior(Enum):
    """How to handle iteration when review fails"""
    AUTO_ITERATE = "auto_iterate"
    PAUSE_FOR_APPROVAL = "pause_for_approval"


class FailureBehavior(Enum):
    """How to handle provider failures"""
    PAUSE_NOTIFY = "pause_notify"
    FALLBACK_PROVIDER = "fallback_provider"
    SKIP_PHASE = "skip_phase"


class ProviderType(Enum):
    """LLM Provider types"""
    CLAUDE_CODE = "claude_code"
    CLAUDE_SDK = "claude_sdk"  # Claude Agent SDK for todo-aware workflows
    GEMINI_SDK = "gemini_sdk"
    GEMINI_OPENROUTER = "gemini_openrouter"
    GEMINI_OAUTH = "gemini_oauth"
    ANTIGRAVITY = "antigravity"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    NONE = "none"


def generate_id() -> str:
    """Generate a unique ID for workflow entities"""
    return str(uuid.uuid4())[:8]


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider"""
    provider_type: ProviderType
    model_name: str = ""
    api_url: Optional[str] = None
    temperature: float = 0.1
    context_length: int = 8192
    extra_params: Dict[str, Any] = field(default_factory=dict)
    fallback_provider: Optional["ProviderConfig"] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "provider_type": self.provider_type.value,
            "model_name": self.model_name,
            "api_url": self.api_url,
            "temperature": self.temperature,
            "context_length": self.context_length,
            "extra_params": self.extra_params,
        }
        if self.fallback_provider:
            result["fallback_provider"] = self.fallback_provider.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderConfig":
        fallback = None
        if data.get("fallback_provider"):
            fallback = cls.from_dict(data["fallback_provider"])
        return cls(
            provider_type=ProviderType(data["provider_type"]),
            model_name=data.get("model_name", ""),
            api_url=data.get("api_url"),
            temperature=data.get("temperature", 0.1),
            context_length=data.get("context_length", 8192),
            extra_params=data.get("extra_params", {}),
            fallback_provider=fallback,
        )


@dataclass
class WorkflowPhase:
    """Definition of a single workflow phase"""
    id: str
    name: str
    role: PhaseRole
    provider_config: ProviderConfig
    prompt_template: str
    output_artifact_type: ArtifactType
    success_pattern: str = "/complete"
    can_skip: bool = True
    can_iterate: bool = False
    max_retries: int = 2
    timeout_seconds: int = 3600
    parallel_with: Optional[str] = None
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role.value,
            "provider_config": self.provider_config.to_dict(),
            "prompt_template": self.prompt_template,
            "output_artifact_type": self.output_artifact_type.value,
            "success_pattern": self.success_pattern,
            "can_skip": self.can_skip,
            "can_iterate": self.can_iterate,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "parallel_with": self.parallel_with,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowPhase":
        return cls(
            id=data["id"],
            name=data["name"],
            role=PhaseRole(data["role"]),
            provider_config=ProviderConfig.from_dict(data["provider_config"]),
            prompt_template=data["prompt_template"],
            output_artifact_type=ArtifactType(data["output_artifact_type"]),
            success_pattern=data.get("success_pattern", "/complete"),
            can_skip=data.get("can_skip", True),
            can_iterate=data.get("can_iterate", False),
            max_retries=data.get("max_retries", 2),
            timeout_seconds=data.get("timeout_seconds", 3600),
            parallel_with=data.get("parallel_with"),
            order=data.get("order", 0),
        )


@dataclass
class WorkflowTemplate:
    """Reusable workflow template"""
    id: str
    name: str
    description: str = ""
    phases: List[WorkflowPhase] = field(default_factory=list)
    max_iterations: int = 3
    iteration_behavior: IterationBehavior = IterationBehavior.AUTO_ITERATE
    failure_behavior: FailureBehavior = FailureBehavior.PAUSE_NOTIFY
    budget_limit: Optional[float] = None
    budget_scope: str = "execution"
    is_default: bool = False
    is_global: bool = True
    project_id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "phases": [p.to_dict() for p in self.phases],
            "max_iterations": self.max_iterations,
            "iteration_behavior": self.iteration_behavior.value,
            "failure_behavior": self.failure_behavior.value,
            "budget_limit": self.budget_limit,
            "budget_scope": self.budget_scope,
            "is_default": self.is_default,
            "is_global": self.is_global,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowTemplate":
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            phases=[WorkflowPhase.from_dict(p) for p in data.get("phases", [])],
            max_iterations=data.get("max_iterations", 3),
            iteration_behavior=IterationBehavior(data.get("iteration_behavior", "auto_iterate")),
            failure_behavior=FailureBehavior(data.get("failure_behavior", "pause_notify")),
            budget_limit=data.get("budget_limit"),
            budget_scope=data.get("budget_scope", "execution"),
            is_default=data.get("is_default", False),
            is_global=data.get("is_global", True),
            project_id=data.get("project_id"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


@dataclass
class Artifact:
    """Output artifact from a phase"""
    id: str
    workflow_execution_id: str
    phase_execution_id: str
    artifact_type: ArtifactType
    name: str
    content: str
    file_path: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_edited: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workflow_execution_id": self.workflow_execution_id,
            "phase_execution_id": self.phase_execution_id,
            "artifact_type": self.artifact_type.value,
            "name": self.name,
            "content": self.content,
            "file_path": self.file_path,
            "metadata": self.metadata,
            "is_edited": self.is_edited,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        return cls(
            id=data["id"],
            workflow_execution_id=data["workflow_execution_id"],
            phase_execution_id=data["phase_execution_id"],
            artifact_type=ArtifactType(data["artifact_type"]),
            name=data["name"],
            content=data["content"],
            file_path=data["file_path"],
            metadata=data.get("metadata", {}),
            is_edited=data.get("is_edited", False),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
        )


@dataclass
class PhaseExecution:
    """Execution record for a single phase"""
    id: str
    workflow_execution_id: str
    phase_id: str
    phase_name: str
    phase_role: PhaseRole
    session_id: Optional[int] = None
    provider_used: str = ""
    model_used: str = ""
    status: PhaseStatus = PhaseStatus.PENDING
    iteration: int = 1
    input_artifact_ids: List[str] = field(default_factory=list)
    output_artifact_id: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "workflow_execution_id": self.workflow_execution_id,
            "phase_id": self.phase_id,
            "phase_name": self.phase_name,
            "phase_role": self.phase_role.value,
            "session_id": self.session_id,
            "provider_used": self.provider_used,
            "model_used": self.model_used,
            "status": self.status.value,
            "iteration": self.iteration,
            "input_artifact_ids": self.input_artifact_ids,
            "output_artifact_id": self.output_artifact_id,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": self.cost_usd,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhaseExecution":
        return cls(
            id=data["id"],
            workflow_execution_id=data["workflow_execution_id"],
            phase_id=data["phase_id"],
            phase_name=data["phase_name"],
            phase_role=PhaseRole(data["phase_role"]),
            session_id=data.get("session_id"),
            provider_used=data.get("provider_used", ""),
            model_used=data.get("model_used", ""),
            status=PhaseStatus(data.get("status", "pending")),
            iteration=data.get("iteration", 1),
            input_artifact_ids=data.get("input_artifact_ids", []),
            output_artifact_id=data.get("output_artifact_id"),
            tokens_input=data.get("tokens_input", 0),
            tokens_output=data.get("tokens_output", 0),
            cost_usd=data.get("cost_usd", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message", ""),
        )


@dataclass
class WorkflowExecution:
    """A running or completed workflow instance"""
    id: str
    template_id: str
    template_name: str
    trigger_mode: TriggerMode
    project_id: Optional[int] = None
    project_path: str = ""
    issue_session_id: Optional[int] = None
    task_description: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    current_phase_id: Optional[str] = None
    iteration: int = 1
    phase_executions: List[PhaseExecution] = field(default_factory=list)
    artifact_ids: List[str] = field(default_factory=list)
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    total_cost_usd: float = 0.0
    budget_limit: Optional[float] = None
    iteration_behavior: IterationBehavior = IterationBehavior.AUTO_ITERATE
    interactive_mode: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "template_name": self.template_name,
            "trigger_mode": self.trigger_mode.value,
            "project_id": self.project_id,
            "project_path": self.project_path,
            "issue_session_id": self.issue_session_id,
            "task_description": self.task_description,
            "status": self.status.value,
            "current_phase_id": self.current_phase_id,
            "iteration": self.iteration,
            "phase_executions": [p.to_dict() for p in self.phase_executions],
            "artifact_ids": self.artifact_ids,
            "total_tokens_input": self.total_tokens_input,
            "total_tokens_output": self.total_tokens_output,
            "total_cost_usd": self.total_cost_usd,
            "budget_limit": self.budget_limit,
            "iteration_behavior": self.iteration_behavior.value,
            "interactive_mode": self.interactive_mode,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowExecution":
        return cls(
            id=data["id"],
            template_id=data["template_id"],
            template_name=data["template_name"],
            trigger_mode=TriggerMode(data["trigger_mode"]),
            project_id=data.get("project_id"),
            project_path=data.get("project_path", ""),
            issue_session_id=data.get("issue_session_id"),
            task_description=data.get("task_description", ""),
            status=WorkflowStatus(data.get("status", "pending")),
            current_phase_id=data.get("current_phase_id"),
            iteration=data.get("iteration", 1),
            phase_executions=[PhaseExecution.from_dict(p) for p in data.get("phase_executions", [])],
            artifact_ids=data.get("artifact_ids", []),
            total_tokens_input=data.get("total_tokens_input", 0),
            total_tokens_output=data.get("total_tokens_output", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            budget_limit=data.get("budget_limit"),
            iteration_behavior=IterationBehavior(data.get("iteration_behavior", "auto_iterate")),
            interactive_mode=data.get("interactive_mode", False),
            created_at=data.get("created_at", datetime.now().isoformat()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class BudgetTracker:
    """Track spending across executions, projects, and globally"""
    id: str
    scope: str  # "execution", "project", "global"
    scope_id: str
    period_start: str
    budget_limit: Optional[float] = None
    total_spent: float = 0.0
    token_count_input: int = 0
    token_count_output: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "period_start": self.period_start,
            "budget_limit": self.budget_limit,
            "total_spent": self.total_spent,
            "token_count_input": self.token_count_input,
            "token_count_output": self.token_count_output,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BudgetTracker":
        return cls(
            id=data["id"],
            scope=data["scope"],
            scope_id=data["scope_id"],
            period_start=data["period_start"],
            budget_limit=data.get("budget_limit"),
            total_spent=data.get("total_spent", 0.0),
            token_count_input=data.get("token_count_input", 0),
            token_count_output=data.get("token_count_output", 0),
        )

    def check_budget(self, additional_cost: float = 0.0) -> tuple[bool, float]:
        """Check if budget would be exceeded. Returns (is_ok, remaining)"""
        if self.budget_limit is None:
            return True, float('inf')
        remaining = self.budget_limit - self.total_spent - additional_cost
        return remaining >= 0, max(0, remaining)


@dataclass
class ProviderKeys:
    """Encrypted storage for all provider API keys"""
    gemini_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_url: str = "http://localhost:11434"
    lm_studio_url: str = "http://localhost:1234/v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gemini_api_key": self.gemini_api_key,
            "openai_api_key": self.openai_api_key,
            "openrouter_api_key": self.openrouter_api_key,
            "ollama_url": self.ollama_url,
            "lm_studio_url": self.lm_studio_url,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProviderKeys":
        return cls(
            gemini_api_key=data.get("gemini_api_key", ""),
            openai_api_key=data.get("openai_api_key", ""),
            openrouter_api_key=data.get("openrouter_api_key", ""),
            ollama_url=data.get("ollama_url", "http://localhost:11434"),
            lm_studio_url=data.get("lm_studio_url", "http://localhost:1234/v1"),
        )

    def has_key(self, provider: ProviderType) -> bool:
        """Check if API key is configured for a provider"""
        if provider == ProviderType.GEMINI_SDK:
            return bool(self.gemini_api_key)
        elif provider == ProviderType.OPENAI:
            return bool(self.openai_api_key)
        elif provider in (ProviderType.OPENROUTER, ProviderType.GEMINI_OPENROUTER):
            return bool(self.openrouter_api_key)
        elif provider in (ProviderType.OLLAMA, ProviderType.LM_STUDIO, ProviderType.CLAUDE_CODE, ProviderType.CLAUDE_SDK, ProviderType.ANTIGRAVITY, ProviderType.NONE):
            return True
        return False

    def get_key(self, provider: ProviderType) -> str:
        """Get API key for a provider"""
        if provider == ProviderType.GEMINI_SDK:
            return self.gemini_api_key
        elif provider == ProviderType.OPENAI:
            return self.openai_api_key
        elif provider in (ProviderType.OPENROUTER, ProviderType.GEMINI_OPENROUTER):
            return self.openrouter_api_key
        return ""

    def get_url(self, provider: ProviderType) -> str:
        """Get API URL for a provider"""
        if provider == ProviderType.OLLAMA:
            return self.ollama_url
        elif provider == ProviderType.LM_STUDIO:
            return self.lm_studio_url
        elif provider == ProviderType.OPENROUTER:
            return "https://openrouter.ai/api/v1"
        elif provider == ProviderType.OPENAI:
            return "https://api.openai.com/v1"
        elif provider == ProviderType.GEMINI_SDK:
            return "https://generativelanguage.googleapis.com"
        return ""


# Token cost estimates per 1K tokens (USD)
TOKEN_COSTS: Dict[str, Dict[str, float]] = {
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    "gemini-2.0-flash": {"input": 0.0001, "output": 0.0004},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    # Antigravity models (free via Cloud Code Assist)
    "claude-sonnet-4-5": {"input": 0.0, "output": 0.0},
    "claude-sonnet-4-5-thinking": {"input": 0.0, "output": 0.0},
    "claude-opus-4-5-thinking": {"input": 0.0, "output": 0.0},
    "gemini-3-pro": {"input": 0.0, "output": 0.0},
    "gemini-3-flash": {"input": 0.0, "output": 0.0},
    "gemini-2.5-pro": {"input": 0.0, "output": 0.0},
    "gemini-2.5-flash": {"input": 0.0, "output": 0.0},
}


def estimate_cost(model: str, tokens_input: int, tokens_output: int) -> float:
    """Estimate cost for token usage"""
    costs = TOKEN_COSTS.get(model, {"input": 0.001, "output": 0.002})
    return (tokens_input / 1000 * costs["input"]) + (tokens_output / 1000 * costs["output"])
