from datetime import datetime
from typing import Callable, Awaitable

from .sdk_models import SDKTodo, TodoStatus, TodoSyncState
from ..database import db


class TodoSyncManager:
    
    def __init__(
        self,
        on_todo_update: Callable[[str, list[SDKTodo]], Awaitable[None]] | None = None,
    ):
        self._on_todo_update = on_todo_update
        self._sync_states: dict[str, TodoSyncState] = {}

    def set_update_callback(
        self, callback: Callable[[str, list[SDKTodo]], Awaitable[None]]
    ):
        self._on_todo_update = callback

    async def sync_todos(
        self,
        workflow_execution_id: str,
        todos: list[SDKTodo],
        phase_execution_id: str | None = None,
    ) -> TodoSyncState:
        if workflow_execution_id not in self._sync_states:
            self._sync_states[workflow_execution_id] = TodoSyncState(
                workflow_execution_id=workflow_execution_id
            )

        sync_state = self._sync_states[workflow_execution_id]
        
        for todo in todos:
            todo.workflow_execution_id = workflow_execution_id
            if phase_execution_id:
                todo.phase_execution_id = phase_execution_id
            todo.updated_at = datetime.now().isoformat()

        sync_state.todos = todos
        sync_state.last_sync = datetime.now().isoformat()

        for todo in todos:
            db.upsert_sdk_todo(todo.to_dict())

        if self._on_todo_update:
            await self._on_todo_update(workflow_execution_id, todos)

        return sync_state

    async def update_todo_status(
        self,
        workflow_execution_id: str,
        todo_id: str,
        status: TodoStatus,
    ) -> bool:
        if workflow_execution_id in self._sync_states:
            sync_state = self._sync_states[workflow_execution_id]
            if sync_state.update_todo(todo_id, status):
                for todo in sync_state.todos:
                    if todo.id == todo_id:
                        db.update_sdk_todo(todo_id, {
                            "status": status.value,
                            "updated_at": datetime.now().isoformat(),
                        })
                        if self._on_todo_update:
                            await self._on_todo_update(workflow_execution_id, sync_state.todos)
                        return True
        return False

    def get_sync_state(self, workflow_execution_id: str) -> TodoSyncState | None:
        return self._sync_states.get(workflow_execution_id)

    def get_todos(self, workflow_execution_id: str) -> list[SDKTodo]:
        if workflow_execution_id in self._sync_states:
            return self._sync_states[workflow_execution_id].todos
        
        db_todos = db.get_sdk_todos(workflow_execution_id)
        return [SDKTodo.from_dict(t) for t in db_todos]

    def get_progress(self, workflow_execution_id: str) -> dict[str, int]:
        todos = self.get_todos(workflow_execution_id)
        completed = sum(1 for t in todos if t.status == TodoStatus.COMPLETED)
        in_progress = sum(1 for t in todos if t.status == TodoStatus.IN_PROGRESS)
        pending = sum(1 for t in todos if t.status == TodoStatus.PENDING)
        cancelled = sum(1 for t in todos if t.status == TodoStatus.CANCELLED)
        total = len(todos)
        
        return {
            "completed": completed,
            "in_progress": in_progress,
            "pending": pending,
            "cancelled": cancelled,
            "total": total,
            "percent": round(completed / total * 100) if total > 0 else 0,
        }

    def clear_workflow(self, workflow_execution_id: str):
        if workflow_execution_id in self._sync_states:
            del self._sync_states[workflow_execution_id]
        db.delete_sdk_todos_by_workflow(workflow_execution_id)

    def load_from_db(self, workflow_execution_id: str) -> TodoSyncState:
        if workflow_execution_id in self._sync_states:
            return self._sync_states[workflow_execution_id]
        
        db_todos = db.get_sdk_todos(workflow_execution_id)
        todos = [SDKTodo.from_dict(t) for t in db_todos]
        
        sync_state = TodoSyncState(
            workflow_execution_id=workflow_execution_id,
            todos=todos,
            last_sync=datetime.now().isoformat() if todos else None,
        )
        self._sync_states[workflow_execution_id] = sync_state
        return sync_state


todo_sync_manager = TodoSyncManager()
