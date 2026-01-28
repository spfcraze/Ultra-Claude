"""
Claude Agent SDK Bridge - Integrates SDK with Autowrkers workflow system.

This module provides the connection layer between the Claude Agent SDK's
streaming query interface and Autowrkers's workflow execution system.
"""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable, Optional, Any, Awaitable

from .sdk_models import SDKTodo, TodoStatus, TodoSyncState


@dataclass
class SDKConfig:
    max_turns: int = 20
    timeout_seconds: int = 300
    enable_todo_sync: bool = True


class SDKBridge:
    """
    Bridge between Claude Agent SDK and Autowrkers.
    
    Handles SDK query execution, todo extraction from tool_use blocks,
    and synchronization with PhaseExecution status.
    """
    
    def __init__(
        self,
        config: Optional[SDKConfig] = None,
        on_todo_update: Optional[Callable[[str, list[SDKTodo]], Awaitable[None]]] = None,
        on_message: Optional[Callable[[str, str], Awaitable[None]]] = None,
    ):
        self.config = config or SDKConfig()
        self._on_todo_update = on_todo_update
        self._on_message = on_message
        self._active_sessions: dict[str, TodoSyncState] = {}
        self._sdk_available: Optional[bool] = None
    
    def is_sdk_available(self) -> bool:
        if self._sdk_available is None:
            try:
                from claude_agent_sdk import query
                self._sdk_available = True
            except ImportError:
                self._sdk_available = False
        return self._sdk_available
    
    async def query(
        self,
        prompt: str,
        workflow_execution_id: str,
    ) -> AsyncIterator[tuple[str, Optional[list[SDKTodo]]]]:
        """
        Execute SDK query and stream results with todo updates.
        
        Yields tuples of (content_chunk, todos_if_updated).
        """
        if not self.is_sdk_available():
            raise RuntimeError(
                "claude-agent-sdk not installed. Run: pip install claude-agent-sdk"
            )
        
        from claude_agent_sdk import query as sdk_query
        
        sync_state = TodoSyncState(workflow_execution_id=workflow_execution_id)
        self._active_sessions[workflow_execution_id] = sync_state
        
        try:
            async for message in sdk_query(
                prompt=prompt,
                options={"max_turns": self.config.max_turns}
            ):
                content, todos_updated = await self._process_message(
                    message, workflow_execution_id, sync_state
                )
                
                if content or todos_updated:
                    yield content, todos_updated
                    
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"SDK query timed out after {self.config.timeout_seconds}s"
            )
        finally:
            if workflow_execution_id in self._active_sessions:
                del self._active_sessions[workflow_execution_id]
    
    async def _process_message(
        self,
        message: Any,
        workflow_execution_id: str,
        sync_state: TodoSyncState,
    ) -> tuple[str, Optional[list[SDKTodo]]]:
        content = ""
        todos_updated = None
        
        if not hasattr(message, 'message') or not hasattr(message.message, 'content'):
            return content, todos_updated
        
        for block in message.message.content:
            if hasattr(block, 'text'):
                content += block.text
            
            if hasattr(block, 'type') and block.type == 'tool_use':
                if hasattr(block, 'name') and block.name == 'TodoWrite':
                    todos = self._parse_todos(
                        block.input.get('todos', []),
                        workflow_execution_id
                    )
                    sync_state.todos = todos
                    sync_state.last_sync = datetime.now().isoformat()
                    todos_updated = todos
                    
                    if self._on_todo_update:
                        await self._on_todo_update(workflow_execution_id, todos)
        
        return content, todos_updated
    
    def _parse_todos(
        self,
        sdk_todos: list[dict],
        workflow_id: str,
    ) -> list[SDKTodo]:
        return [
            SDKTodo.from_sdk_todo(t, workflow_id)
            for t in sdk_todos
        ]
    
    def get_todos(self, workflow_execution_id: str) -> list[SDKTodo]:
        if workflow_execution_id in self._active_sessions:
            return self._active_sessions[workflow_execution_id].todos
        return []
    
    def get_progress(self, workflow_execution_id: str) -> tuple[int, int]:
        if workflow_execution_id in self._active_sessions:
            return self._active_sessions[workflow_execution_id].get_progress()
        return 0, 0
    
    def get_sync_state(self, workflow_execution_id: str) -> Optional[TodoSyncState]:
        return self._active_sessions.get(workflow_execution_id)
    
    def clear_session(self, workflow_execution_id: str) -> bool:
        if workflow_execution_id in self._active_sessions:
            del self._active_sessions[workflow_execution_id]
            return True
        return False


sdk_bridge = SDKBridge()
