import asyncio
import threading
from datetime import datetime
from typing import Any
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .models import (
    WorkflowStatus,
    PhaseStatus,
    TriggerMode,
    IterationBehavior,
    WorkflowPhase,
    PhaseExecution,
)
from .engine import workflow_orchestrator, WorkflowOrchestrator
from .template_manager import template_manager
from .artifact_manager import artifact_manager
from .budget_tracker import budget_manager
from .providers.registry import model_registry
from .oauth.manager import oauth_manager, AuthStatus
from .oauth.storage import OAuthClientConfig
from .todo_sync import todo_sync_manager
from .sdk_models import TodoStatus
from ..database import db


router = APIRouter(prefix="/api/workflow", tags=["workflow"])


class ApprovalManager:
    
    DEFAULT_TIMEOUT_SECONDS = 300
    
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._messages: dict[str, str] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        self._timeout_values: dict[str, float] = {}
        self._created_at: dict[str, datetime] = {}
    
    def create_request(
        self, 
        execution_id: str, 
        message: str, 
        timeout_seconds: float | None = None,
        default_on_timeout: bool = False
    ) -> asyncio.Future[bool]:
        """Create a new approval request and return a Future to await."""
        if execution_id in self._pending:
            self._pending[execution_id].cancel()
        if execution_id in self._timeout_tasks:
            self._timeout_tasks[execution_id].cancel()
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[execution_id] = future
        self._messages[execution_id] = message
        self._created_at[execution_id] = datetime.now()
        
        effective_timeout = timeout_seconds if timeout_seconds is not None else self.DEFAULT_TIMEOUT_SECONDS
        self._timeout_values[execution_id] = effective_timeout
        
        if effective_timeout > 0:
            async def timeout_handler():
                await asyncio.sleep(effective_timeout)
                if execution_id in self._pending and not self._pending[execution_id].done():
                    self._record_approval(execution_id, "timeout", was_timeout=True)
                    self.resolve(execution_id, default_on_timeout, source="timeout")
            
            try:
                self._timeout_tasks[execution_id] = asyncio.create_task(timeout_handler())
            except RuntimeError:
                pass
        
        return future
    
    def resolve(self, execution_id: str, approved: bool, source: str = "web") -> bool:
        with self._lock:
            if execution_id not in self._pending:
                return False
            
            future = self._pending.get(execution_id)
            if future is None or future.done():
                return False
            
            if source != "timeout":
                self._record_approval(execution_id, "approved" if approved else "rejected", source=source)
            
            if execution_id in self._timeout_tasks:
                self._timeout_tasks[execution_id].cancel()
                del self._timeout_tasks[execution_id]
            
            self._pending.pop(execution_id, None)
            self._messages.pop(execution_id, None)
            self._timeout_values.pop(execution_id, None)
            self._created_at.pop(execution_id, None)
            
            future.set_result(approved)
            return True
    
    def _record_approval(self, execution_id: str, action: str, source: str = "web", was_timeout: bool = False):
        message = self._messages.get(execution_id, "")
        timeout_val = self._timeout_values.get(execution_id)
        try:
            db.create_approval_record({
                "execution_id": execution_id,
                "message": message,
                "action": action,
                "source": source,
                "responded_at": datetime.now().isoformat(),
                "timeout_seconds": timeout_val,
                "was_timeout": was_timeout,
            })
        except Exception:
            pass
    
    def get_pending_message(self, execution_id: str) -> str | None:
        """Get the message for a pending approval."""
        return self._messages.get(execution_id)
    
    def get_pending_info(self, execution_id: str) -> dict[str, Any] | None:
        if execution_id not in self._pending:
            return None
        
        created = self._created_at.get(execution_id)
        timeout = self._timeout_values.get(execution_id, self.DEFAULT_TIMEOUT_SECONDS)
        elapsed = (datetime.now() - created).total_seconds() if created else 0
        remaining = max(0, timeout - elapsed) if timeout else None
        
        return {
            "message": self._messages.get(execution_id),
            "timeout_seconds": timeout,
            "remaining_seconds": remaining,
            "created_at": created.isoformat() if created else None,
        }
    
    def has_pending(self, execution_id: str) -> bool:
        """Check if there's a pending approval for this execution."""
        return execution_id in self._pending
    
    def cancel(self, execution_id: str):
        """Cancel a pending approval request."""
        if execution_id in self._timeout_tasks:
            self._timeout_tasks[execution_id].cancel()
            del self._timeout_tasks[execution_id]
        
        if execution_id in self._pending:
            future = self._pending.pop(execution_id)
            self._messages.pop(execution_id, None)
            self._timeout_values.pop(execution_id, None)
            self._created_at.pop(execution_id, None)
            if not future.done():
                future.cancel()


approval_manager = ApprovalManager()


class WorkflowCreateRequest(BaseModel):
    task_description: str = Field(..., min_length=1, max_length=50000)
    project_path: str = Field("", max_length=1000)
    template_id: str | None = Field(None, max_length=100)
    project_id: int | None = None
    issue_session_id: int | None = None
    budget_limit: float | None = Field(None, ge=0, le=10000)
    interactive_mode: bool = False


class TemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    phases: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    max_iterations: int = Field(3, ge=1, le=50)
    iteration_behavior: str = Field("auto_iterate", max_length=50)
    failure_behavior: str = Field("pause_notify", max_length=50)
    budget_limit: float | None = Field(None, ge=0, le=10000)
    is_global: bool = True
    project_id: int | None = None


class ProviderKeysRequest(BaseModel):
    gemini_api_key: str = Field("", max_length=500)
    openai_api_key: str = Field("", max_length=500)
    openrouter_api_key: str = Field("", max_length=500)
    ollama_url: str = Field("http://localhost:11434", max_length=500)
    lm_studio_url: str = Field("http://localhost:1234/v1", max_length=500)


class OAuthClientConfigRequest(BaseModel):
    client_config: dict[str, Any]  # Validated by OAuth handler


@router.get("/templates")
async def list_templates(project_id: int | None = None):
    templates = template_manager.get_all(project_id)
    return {
        "templates": [t.to_dict() for t in templates],
        "count": len(templates),
    }


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    template = template_manager.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": template.to_dict()}


@router.post("/templates")
async def create_template(request: TemplateCreateRequest):
    from .models import (
        WorkflowTemplate,
        WorkflowPhase,
        ProviderConfig,
        ProviderType,
        PhaseRole,
        ArtifactType,
        IterationBehavior,
        FailureBehavior,
        generate_id,
    )
    
    phases = []
    for i, p in enumerate(request.phases):
        provider_data = p.get("provider_config", p.get("provider", {}))
        provider_config = ProviderConfig(
            provider_type=ProviderType(provider_data.get("provider_type", provider_data.get("type", "claude_code"))),
            model_name=provider_data.get("model_name", provider_data.get("model", "")),
            temperature=provider_data.get("temperature", 0.1),
            context_length=provider_data.get("context_length", 8192),
        )
        
        phase = WorkflowPhase(
            id=generate_id(),
            name=p.get("name", f"Phase {i+1}"),
            role=PhaseRole(p.get("role", "analyzer")),
            provider_config=provider_config,
            prompt_template=p.get("prompt_template", ""),
            output_artifact_type=ArtifactType(p.get("output_artifact_type", p.get("output_type", "custom"))),
            success_pattern=p.get("success_pattern", "/complete"),
            can_skip=p.get("can_skip", True),
            can_iterate=p.get("can_iterate", False),
            max_retries=p.get("max_retries", 2),
            timeout_seconds=p.get("timeout_seconds", 3600),
            parallel_with=p.get("parallel_with"),
            order=p.get("order", i),
        )
        phases.append(phase)
    
    template = WorkflowTemplate(
        id=generate_id(),
        name=request.name,
        description=request.description,
        phases=phases,
        max_iterations=request.max_iterations,
        iteration_behavior=IterationBehavior(request.iteration_behavior),
        failure_behavior=FailureBehavior(request.failure_behavior),
        budget_limit=request.budget_limit,
        is_global=request.is_global,
        project_id=request.project_id,
    )
    
    template_id = template_manager.create(template)
    return {"success": True, "template_id": template_id, "template": template.to_dict()}


@router.put("/templates/{template_id}")
async def update_template(template_id: str, request: TemplateCreateRequest):
    from .models import (
        WorkflowPhase,
        ProviderConfig,
        ProviderType,
        PhaseRole,
        ArtifactType,
        IterationBehavior,
        FailureBehavior,
        generate_id,
    )
    
    existing = template_manager.get(template_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Template not found")
    
    existing_phase_ids = {p.name: p.id for p in existing.phases}
    
    phases = []
    for i, p in enumerate(request.phases):
        provider_data = p.get("provider_config", p.get("provider", {}))
        provider_config = ProviderConfig(
            provider_type=ProviderType(provider_data.get("provider_type", provider_data.get("type", "claude_code"))),
            model_name=provider_data.get("model_name", provider_data.get("model", "")),
            temperature=provider_data.get("temperature", 0.1),
            context_length=provider_data.get("context_length", 8192),
        )
        
        phase_name = p.get("name", f"Phase {i+1}")
        phase_id = existing_phase_ids.get(phase_name, generate_id())
        
        phase = WorkflowPhase(
            id=phase_id,
            name=phase_name,
            role=PhaseRole(p.get("role", "analyzer")),
            provider_config=provider_config,
            prompt_template=p.get("prompt_template", ""),
            output_artifact_type=ArtifactType(p.get("output_artifact_type", p.get("output_type", "custom"))),
            success_pattern=p.get("success_pattern", "/complete"),
            can_skip=p.get("can_skip", True),
            can_iterate=p.get("can_iterate", False),
            max_retries=p.get("max_retries", 2),
            timeout_seconds=p.get("timeout_seconds", 3600),
            parallel_with=p.get("parallel_with"),
            order=p.get("order", i),
        )
        phases.append(phase)
    
    updates = {
        'name': request.name,
        'description': request.description,
        'phases': phases,
        'max_iterations': request.max_iterations,
        'iteration_behavior': IterationBehavior(request.iteration_behavior).value,
        'failure_behavior': FailureBehavior(request.failure_behavior).value,
        'budget_limit': request.budget_limit,
        'is_global': request.is_global,
        'project_id': request.project_id,
    }
    
    if not template_manager.update(template_id, updates):
        raise HTTPException(status_code=500, detail="Failed to update template")
    
    updated = template_manager.get(template_id)
    return {"success": True, "template": updated.to_dict() if updated else None}


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    if not template_manager.delete(template_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True}


@router.post("/templates/{template_id}/default")
async def set_default_template(template_id: str, project_id: int | None = None):
    if not template_manager.set_default(template_id, project_id):
        raise HTTPException(status_code=404, detail="Template not found")
    return {"success": True}


@router.post("/templates/{template_id}/export")
async def export_template(template_id: str):
    from pathlib import Path
    import tempfile
    
    template = template_manager.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        file_path = Path(f.name)
    
    template_manager.export_yaml(template_id, file_path)
    content = file_path.read_text()
    file_path.unlink()
    
    return {"success": True, "yaml": content, "filename": f"{template.name}.yaml"}


@router.get("/executions")
async def list_executions(
    project_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
):
    ws = None
    if status:
        try:
            ws = WorkflowStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    executions = workflow_orchestrator.get_executions(
        project_id=project_id,
        status=ws,
        limit=limit,
    )
    
    return {
        "executions": [e.to_dict() for e in executions],
        "count": len(executions),
    }


@router.get("/executions/{execution_id}")
async def get_execution(execution_id: str):
    execution = workflow_orchestrator.get_execution(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    artifacts = workflow_orchestrator.get_artifacts(execution_id)
    budget = workflow_orchestrator.get_budget_summary(execution_id)
    
    template_phases = None
    if execution.template_id:
        template = template_manager.get(execution.template_id)
        if template:
            template_phases = [p.to_dict() for p in template.phases]
    
    return {
        "execution": execution.to_dict(),
        "artifacts": [a.to_dict() for a in artifacts],
        "budget": budget,
        "template_phases": template_phases,
    }


@router.post("/executions")
async def create_execution(request: WorkflowCreateRequest):
    try:
        execution = workflow_orchestrator.create_execution(
            template_id=request.template_id,
            trigger_mode=TriggerMode.MANUAL_TASK,
            project_id=request.project_id,
            project_path=request.project_path,
            issue_session_id=request.issue_session_id,
            task_description=request.task_description,
            budget_limit=request.budget_limit,
            interactive_mode=request.interactive_mode,
        )
        return {"success": True, "execution": execution.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/executions/{execution_id}/run")
async def run_execution(execution_id: str):
    execution = workflow_orchestrator.get_execution(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    
    if execution.status not in (WorkflowStatus.PENDING, WorkflowStatus.PAUSED):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot run execution in {execution.status.value} status"
        )
    
    asyncio.create_task(web_orchestrator.run(execution_id))
    
    return {"success": True, "message": "Workflow started", "execution_id": execution_id}


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(execution_id: str):
    if not await workflow_orchestrator.cancel(execution_id):
        raise HTTPException(status_code=400, detail="Cannot cancel execution")
    return {"success": True}


@router.post("/executions/{execution_id}/resume")
async def resume_execution(execution_id: str):
    result = await workflow_orchestrator.resume(execution_id)
    if not result:
        raise HTTPException(status_code=400, detail="Cannot resume execution")
    return {"success": True, "execution": result.to_dict()}


@router.post("/executions/{execution_id}/skip/{phase_id}")
async def skip_phase(execution_id: str, phase_id: str):
    if not workflow_orchestrator.skip_phase(execution_id, phase_id):
        raise HTTPException(status_code=400, detail="Cannot skip phase")
    return {"success": True}


@router.post("/executions/{execution_id}/approve")
async def approve_execution(execution_id: str):
    if not approval_manager.has_pending(execution_id):
        raise HTTPException(status_code=400, detail="No pending approval for this execution")
    
    if not approval_manager.resolve(execution_id, approved=True):
        raise HTTPException(status_code=400, detail="Failed to approve execution")
    
    return {"success": True, "action": "approved"}


@router.post("/executions/{execution_id}/reject")
async def reject_execution(execution_id: str):
    if not approval_manager.has_pending(execution_id):
        raise HTTPException(status_code=400, detail="No pending approval for this execution")
    
    if not approval_manager.resolve(execution_id, approved=False):
        raise HTTPException(status_code=400, detail="Failed to reject execution")
    
    return {"success": True, "action": "rejected"}


@router.get("/executions/{execution_id}/approval-status")
async def get_approval_status(execution_id: str):
    has_pending = approval_manager.has_pending(execution_id)
    message = approval_manager.get_pending_message(execution_id) if has_pending else None
    
    return {
        "has_pending_approval": has_pending,
        "message": message,
    }


@router.get("/executions/{execution_id}/artifacts")
async def get_execution_artifacts(execution_id: str):
    artifacts = workflow_orchestrator.get_artifacts(execution_id)
    return {
        "artifacts": [a.to_dict() for a in artifacts],
        "count": len(artifacts),
    }


@router.get("/executions/{execution_id}/budget")
async def get_execution_budget(execution_id: str):
    return workflow_orchestrator.get_budget_summary(execution_id)


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    artifact = artifact_manager.get(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"artifact": artifact.to_dict()}


@router.get("/artifacts/{artifact_id}/content")
async def get_artifact_content(artifact_id: str):
    content = artifact_manager.read_content(artifact_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"content": content}


@router.put("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: str, content: str):
    if not artifact_manager.update_content(artifact_id, content):
        raise HTTPException(status_code=404, detail="Artifact not found")
    return {"success": True}


@router.get("/providers")
async def list_providers():
    status = model_registry.get_provider_status()
    return {"providers": status}


@router.get("/providers/detect")
async def detect_local_providers():
    result = await model_registry.detect_local_providers()
    return {
        "ollama": {
            "available": result["ollama"][0],
            "models": result["ollama"][1],
        },
        "lm_studio": {
            "available": result["lm_studio"][0],
            "models": result["lm_studio"][1],
        },
    }


@router.post("/providers/validate/{provider_type}")
async def validate_provider(provider_type: str):
    from .models import ProviderType
    
    try:
        ptype = ProviderType(provider_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider type: {provider_type}")
    
    is_valid, error = await model_registry.validate_provider(ptype)
    return {"valid": is_valid, "error": error if not is_valid else None}


@router.get("/providers/{provider_type}/models")
async def get_provider_models(provider_type: str, refresh: bool = False):
    from .models import ProviderType
    
    try:
        ptype = ProviderType(provider_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid provider type: {provider_type}")
    
    if refresh:
        models = await model_registry.refresh_models(ptype)
    else:
        models = model_registry.get_cached_models(ptype)
    
    return {
        "provider": provider_type,
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.model_name,
                "context_length": m.context_length,
                "supports_tools": m.supports_tools,
                "supports_vision": m.supports_vision,
                "cost_input_per_1k": m.cost_input_per_1k,
                "cost_output_per_1k": m.cost_output_per_1k,
            }
            for m in models
        ],
        "count": len(models),
    }


@router.get("/providers/keys")
async def get_provider_keys():
    keys = model_registry._load_keys()
    return {
        "gemini_configured": bool(keys.gemini_api_key),
        "openai_configured": bool(keys.openai_api_key),
        "openrouter_configured": bool(keys.openrouter_api_key),
        "ollama_url": keys.ollama_url,
        "lm_studio_url": keys.lm_studio_url,
    }


@router.post("/providers/keys")
async def save_provider_keys(request: ProviderKeysRequest):
    from .models import ProviderKeys
    
    keys = ProviderKeys(
        gemini_api_key=request.gemini_api_key,
        openai_api_key=request.openai_api_key,
        openrouter_api_key=request.openrouter_api_key,
        ollama_url=request.ollama_url,
        lm_studio_url=request.lm_studio_url,
    )
    model_registry.save_keys(keys)
    return {"success": True}


@router.get("/oauth/status")
async def get_oauth_status():
    statuses = oauth_manager.get_all_statuses()
    return {
        "providers": {
            provider: status.to_dict()
            for provider, status in statuses.items()
        }
    }


@router.get("/oauth/{provider}/status")
async def get_oauth_provider_status(provider: str):
    if provider not in oauth_manager.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")
    
    status = oauth_manager.get_status(provider)
    return status.to_dict()


@router.post("/oauth/{provider}/client-config")
async def save_oauth_client_config(provider: str, request: OAuthClientConfigRequest):
    if provider not in oauth_manager.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")
    
    try:
        config = OAuthClientConfig(provider=provider, client_config=request.client_config)
        oauth_manager.save_client_config(config)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/oauth/{provider}/client-config")
async def delete_oauth_client_config(provider: str):
    if provider not in oauth_manager.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")
    
    oauth_manager.delete_client_config(provider)
    return {"success": True}


@router.post("/oauth/{provider}/start")
async def start_oauth_flow(provider: str, port: int = 0):
    if provider not in oauth_manager.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")
    
    if not oauth_manager.has_client_config(provider):
        raise HTTPException(
            status_code=400, 
            detail=f"OAuth client config not configured for {provider}. Upload client config first."
        )
    
    if provider == "google":
        from .oauth.flows.google import GoogleOAuthFlow, GoogleOAuthFlowError
        
        client_config = oauth_manager.get_client_config(provider)
        if not client_config:
            raise HTTPException(status_code=400, detail="OAuth client config not found")
        
        try:
            flow = GoogleOAuthFlow(client_config)
            token = await flow.run_local_server_flow(port=port, open_browser=True)
            oauth_manager.save_token(token)
            
            return {
                "success": True,
                "account_email": token.account_email,
                "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            }
        except GoogleOAuthFlowError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OAuth flow failed: {str(e)}")
    
    raise HTTPException(status_code=400, detail=f"OAuth flow not implemented for {provider}")


@router.delete("/oauth/{provider}/revoke")
async def revoke_oauth(provider: str):
    if provider not in oauth_manager.SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported OAuth provider: {provider}")
    
    oauth_manager.revoke(provider)
    return {"success": True}


@router.get("/budget/global")
async def get_global_budget():
    return budget_manager.get_global_summary()


@router.get("/budget/project/{project_id}")
async def get_project_budget(project_id: int):
    return budget_manager.get_project_summary(project_id)


@router.post("/budget/global/limit")
async def set_global_budget_limit(limit: float | None = None):
    budget_manager.set_limit("global", "global", limit)
    return {"success": True}


@router.post("/budget/project/{project_id}/limit")
async def set_project_budget_limit(project_id: int, limit: float | None = None):
    budget_manager.set_limit("project", str(project_id), limit)
    return {"success": True}


class TodoStatusUpdateRequest(BaseModel):
    status: str


@router.get("/executions/{execution_id}/todos")
async def get_execution_todos(execution_id: str):
    todos = todo_sync_manager.get_todos(execution_id)
    progress = todo_sync_manager.get_progress(execution_id)
    return {
        "todos": [t.to_dict() for t in todos],
        "progress": progress,
    }


@router.get("/executions/{execution_id}/todos/progress")
async def get_execution_todo_progress(execution_id: str):
    return todo_sync_manager.get_progress(execution_id)


@router.put("/executions/{execution_id}/todos/{todo_id}")
async def update_todo_status(
    execution_id: str,
    todo_id: str,
    request: TodoStatusUpdateRequest,
):
    try:
        status = TodoStatus(request.status)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid status: {request.status}")
    
    success = await todo_sync_manager.update_todo_status(execution_id, todo_id, status)
    if not success:
        raise HTTPException(status_code=404, detail="Todo not found")
    
    return {"success": True}


@router.delete("/executions/{execution_id}/todos")
async def clear_execution_todos(execution_id: str):
    todo_sync_manager.clear_workflow(execution_id)
    return {"success": True}


@router.delete("/executions/{execution_id}")
async def delete_execution(execution_id: str):
    """Delete a workflow execution and all associated data."""
    from ..database import db

    execution = db.get_workflow_execution(execution_id)
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    # Don't allow deleting running executions
    if execution.get("status") == "running":
        raise HTTPException(status_code=400, detail="Cannot delete a running execution. Cancel it first.")

    # Clear todos first
    todo_sync_manager.clear_workflow(execution_id)

    # Delete from database
    success = db.delete_workflow_execution(execution_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete execution")

    return {"success": True, "message": f"Execution {execution_id} deleted"}


class WorkflowWebSocketManager:
    
    def __init__(self):
        self.connections: dict[str, set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, execution_id: str):
        await websocket.accept()
        if execution_id not in self.connections:
            self.connections[execution_id] = set()
        self.connections[execution_id].add(websocket)

    def disconnect(self, websocket: WebSocket, execution_id: str):
        if execution_id in self.connections:
            self.connections[execution_id].discard(websocket)
            if not self.connections[execution_id]:
                del self.connections[execution_id]

    async def broadcast(self, execution_id: str, message: dict[str, Any]):
        if execution_id not in self.connections:
            return
        
        disconnected = set()
        for ws in self.connections[execution_id]:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)
        
        for ws in disconnected:
            self.connections[execution_id].discard(ws)
    
    def has_connections(self, execution_id: str) -> bool:
        return execution_id in self.connections and len(self.connections[execution_id]) > 0


ws_manager = WorkflowWebSocketManager()


@router.websocket("/ws/{execution_id}")
async def workflow_websocket(websocket: WebSocket, execution_id: str):
    await ws_manager.connect(websocket, execution_id)
    
    execution = workflow_orchestrator.get_execution(execution_id)
    if execution:
        init_data = {
            "type": "init",
            "execution": execution.to_dict(),
        }
        
        if execution.template_id:
            template = template_manager.get(execution.template_id)
            if template:
                init_data["template_phases"] = [p.to_dict() for p in template.phases]
        
        if approval_manager.has_pending(execution_id):
            approval_info = approval_manager.get_pending_info(execution_id)
            init_data["pending_approval"] = {
                "message": approval_info.get("message") if approval_info else "",
                "timeout_seconds": approval_info.get("remaining_seconds") if approval_info else 300,
            }
        
        todos = todo_sync_manager.get_todos(execution_id)
        if todos:
            init_data["todos"] = [t.to_dict() for t in todos]
            init_data["todo_progress"] = todo_sync_manager.get_progress(execution_id)
        
        await websocket.send_json(init_data)
    
    try:
        while True:
            data = await websocket.receive_json()
            
            if data.get("type") == "run":
                asyncio.create_task(web_orchestrator.run(execution_id))
            
            elif data.get("type") == "cancel":
                await workflow_orchestrator.cancel(execution_id)
            
            elif data.get("type") == "approve":
                approved = data.get("approved", True)
                if approval_manager.has_pending(execution_id):
                    approval_manager.resolve(execution_id, approved)
                    await ws_manager.broadcast(execution_id, {
                        "type": "approval_resolved",
                        "approved": approved,
                    })
            
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, execution_id)


async def broadcast_workflow_event(execution_id: str, event_type: str, data: dict[str, Any]):
    await ws_manager.broadcast(execution_id, {
        "type": event_type,
        **data,
    })


async def broadcast_todo_update(execution_id: str, todos: list):
    from .sdk_models import SDKTodo
    progress = todo_sync_manager.get_progress(execution_id)
    await ws_manager.broadcast(execution_id, {
        "type": "todo_update",
        "todos": [t.to_dict() if isinstance(t, SDKTodo) else t for t in todos],
        "progress": progress,
    })


async def web_approval_callback(execution_id: str, message: str) -> bool:
    has_clients = ws_manager.has_connections(execution_id)
    timeout = ApprovalManager.DEFAULT_TIMEOUT_SECONDS if has_clients else 30
    
    await ws_manager.broadcast(execution_id, {
        "type": "approval_needed",
        "execution_id": execution_id,
        "message": message,
        "timeout_seconds": timeout,
    })
    
    future = approval_manager.create_request(
        execution_id, 
        message, 
        timeout_seconds=timeout,
        default_on_timeout=False
    )
    
    try:
        result = await future
        await ws_manager.broadcast(execution_id, {
            "type": "approval_resolved",
            "approved": result,
            "source": "callback",
        })
        return result
    except asyncio.CancelledError:
        return False


def create_web_orchestrator() -> WorkflowOrchestrator:
    
    async def on_workflow_status(execution_id: str, status: WorkflowStatus):
        await broadcast_workflow_event(execution_id, "status_update", {
            "status": status.value,
        })
    
    async def on_phase_start(execution_id: str, phase: WorkflowPhase):
        await broadcast_workflow_event(execution_id, "phase_start", {
            "phase_id": phase.id,
            "phase_name": phase.name,
        })
    
    async def on_phase_complete(execution_id: str, phase_exec: PhaseExecution):
        await broadcast_workflow_event(execution_id, "phase_complete", {
            "phase_id": phase_exec.phase_id,
            "phase_name": phase_exec.phase_name,
            "status": phase_exec.status.value,
        })
    
    async def on_phase_output(execution_id: str, phase_id: str, content: str):
        await broadcast_workflow_event(execution_id, "phase_output", {
            "phase_id": phase_id,
            "content": content,
        })
    
    return WorkflowOrchestrator(
        on_phase_start=on_phase_start,
        on_phase_complete=on_phase_complete,
        on_phase_output=on_phase_output,
        on_workflow_status=on_workflow_status,
        on_approval_needed=web_approval_callback,
    )


web_orchestrator = create_web_orchestrator()

todo_sync_manager.set_update_callback(broadcast_todo_update)
