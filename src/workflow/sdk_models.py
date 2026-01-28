"""
SDK Todo Models - Bridge between Claude Agent SDK and Autowrkers

This module provides data models for integrating Claude Agent SDK's
todo tracking with Autowrkers's workflow system.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class TodoStatus(Enum):
    """SDK-compatible todo status matching Claude Agent SDK"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoPriority(Enum):
    """Todo priority levels"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SDKTodo:
    """
    Represents a todo item from Claude Agent SDK's TodoWrite tool.
    
    Maps to the SDK's todo format while adding Autowrkers-specific
    fields for workflow/phase linkage.
    """
    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    priority: TodoPriority = TodoPriority.MEDIUM
    phase_execution_id: Optional[str] = None
    workflow_execution_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status.value,
            "priority": self.priority.value,
            "phase_execution_id": self.phase_execution_id,
            "workflow_execution_id": self.workflow_execution_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SDKTodo":
        """Create from dictionary"""
        return cls(
            id=data["id"],
            content=data["content"],
            status=TodoStatus(data.get("status", "pending")),
            priority=TodoPriority(data.get("priority", "medium")),
            phase_execution_id=data.get("phase_execution_id"),
            workflow_execution_id=data.get("workflow_execution_id"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_sdk_todo(cls, sdk_todo: Dict[str, Any], workflow_id: str) -> "SDKTodo":
        """
        Create from Claude Agent SDK TodoWrite output.
        
        Args:
            sdk_todo: Todo dict from SDK's TodoWrite tool_use block
            workflow_id: Autowrkers workflow execution ID
        """
        return cls(
            id=sdk_todo.get("id", f"sdk-{datetime.now().timestamp()}"),
            content=sdk_todo.get("content", ""),
            status=TodoStatus(sdk_todo.get("status", "pending")),
            priority=TodoPriority(sdk_todo.get("priority", "medium")),
            workflow_execution_id=workflow_id,
            metadata=sdk_todo.get("metadata", {}),
        )


@dataclass
class TodoSyncState:
    """
    Tracks synchronization state between SDK todos and Autowrkers phases.
    
    Maintains the current list of todos for a workflow execution and
    provides progress calculations.
    """
    workflow_execution_id: str
    todos: List[SDKTodo] = field(default_factory=list)
    last_sync: Optional[str] = None
    
    def get_progress(self) -> tuple[int, int]:
        """
        Calculate progress as (completed, total).
        
        Returns:
            Tuple of (completed_count, total_count)
        """
        completed = sum(1 for t in self.todos if t.status == TodoStatus.COMPLETED)
        return completed, len(self.todos)
    
    def get_progress_percent(self) -> int:
        """Get completion percentage (0-100)"""
        completed, total = self.get_progress()
        if total == 0:
            return 0
        return round(completed / total * 100)
    
    def get_in_progress(self) -> List[SDKTodo]:
        """Get todos currently in progress"""
        return [t for t in self.todos if t.status == TodoStatus.IN_PROGRESS]
    
    def get_pending(self) -> List[SDKTodo]:
        """Get pending todos"""
        return [t for t in self.todos if t.status == TodoStatus.PENDING]
    
    def update_todo(self, todo_id: str, status: TodoStatus) -> bool:
        """
        Update a todo's status.
        
        Returns:
            True if todo was found and updated
        """
        for todo in self.todos:
            if todo.id == todo_id:
                todo.status = status
                todo.updated_at = datetime.now().isoformat()
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            "workflow_execution_id": self.workflow_execution_id,
            "todos": [t.to_dict() for t in self.todos],
            "last_sync": self.last_sync,
            "progress": {
                "completed": self.get_progress()[0],
                "total": self.get_progress()[1],
                "percent": self.get_progress_percent(),
                "in_progress": len(self.get_in_progress()),
            }
        }


@dataclass  
class SDKMessage:
    """
    Represents a message from the Claude Agent SDK stream.
    
    Used to capture both content and tool usage from SDK responses.
    """
    content: str = ""
    tool_uses: List[Dict[str, Any]] = field(default_factory=list)
    todos: Optional[List[SDKTodo]] = None
    
    def has_todos(self) -> bool:
        """Check if this message contains todo updates"""
        return self.todos is not None and len(self.todos) > 0
