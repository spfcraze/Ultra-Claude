import pytest
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.workflow.sdk_models import (
    TodoStatus,
    TodoPriority,
    SDKTodo,
    TodoSyncState,
    SDKMessage,
)
from src.workflow.sdk_bridge import SDKBridge, SDKConfig
from src.workflow.todo_sync import TodoSyncManager
from src.workflow.models import ProviderType


class TestTodoStatus:
    def test_status_values(self):
        assert TodoStatus.PENDING.value == "pending"
        assert TodoStatus.IN_PROGRESS.value == "in_progress"
        assert TodoStatus.COMPLETED.value == "completed"
        assert TodoStatus.CANCELLED.value == "cancelled"

    def test_status_from_string(self):
        assert TodoStatus("pending") == TodoStatus.PENDING
        assert TodoStatus("in_progress") == TodoStatus.IN_PROGRESS


class TestTodoPriority:
    def test_priority_values(self):
        assert TodoPriority.HIGH.value == "high"
        assert TodoPriority.MEDIUM.value == "medium"
        assert TodoPriority.LOW.value == "low"


class TestSDKTodo:
    def test_create_default(self):
        todo = SDKTodo(id="test-1", content="Test task")
        assert todo.id == "test-1"
        assert todo.content == "Test task"
        assert todo.status == TodoStatus.PENDING
        assert todo.priority == TodoPriority.MEDIUM
        assert todo.phase_execution_id is None
        assert todo.workflow_execution_id is None

    def test_create_with_all_fields(self):
        todo = SDKTodo(
            id="test-2",
            content="Complex task",
            status=TodoStatus.IN_PROGRESS,
            priority=TodoPriority.HIGH,
            phase_execution_id="phase-123",
            workflow_execution_id="wf-456",
            metadata={"key": "value"},
        )
        assert todo.status == TodoStatus.IN_PROGRESS
        assert todo.priority == TodoPriority.HIGH
        assert todo.phase_execution_id == "phase-123"
        assert todo.workflow_execution_id == "wf-456"
        assert todo.metadata == {"key": "value"}

    def test_to_dict(self):
        todo = SDKTodo(
            id="test-3",
            content="Dict test",
            status=TodoStatus.COMPLETED,
            priority=TodoPriority.LOW,
        )
        d = todo.to_dict()
        assert d["id"] == "test-3"
        assert d["content"] == "Dict test"
        assert d["status"] == "completed"
        assert d["priority"] == "low"

    def test_from_dict(self):
        data = {
            "id": "test-4",
            "content": "From dict",
            "status": "in_progress",
            "priority": "high",
            "workflow_execution_id": "wf-789",
        }
        todo = SDKTodo.from_dict(data)
        assert todo.id == "test-4"
        assert todo.content == "From dict"
        assert todo.status == TodoStatus.IN_PROGRESS
        assert todo.priority == TodoPriority.HIGH

    def test_from_dict_defaults(self):
        data = {"id": "test-5", "content": "Minimal"}
        todo = SDKTodo.from_dict(data)
        assert todo.status == TodoStatus.PENDING
        assert todo.priority == TodoPriority.MEDIUM

    def test_from_sdk_todo(self):
        sdk_todo = {
            "id": "sdk-1",
            "content": "SDK task",
            "status": "pending",
            "priority": "high",
        }
        todo = SDKTodo.from_sdk_todo(sdk_todo, workflow_id="wf-test")
        assert todo.id == "sdk-1"
        assert todo.content == "SDK task"
        assert todo.workflow_execution_id == "wf-test"

    def test_roundtrip(self):
        original = SDKTodo(
            id="round-1",
            content="Roundtrip test",
            status=TodoStatus.IN_PROGRESS,
            priority=TodoPriority.HIGH,
            workflow_execution_id="wf-round",
            metadata={"nested": {"key": "value"}},
        )
        d = original.to_dict()
        restored = SDKTodo.from_dict(d)
        assert restored.id == original.id
        assert restored.content == original.content
        assert restored.status == original.status
        assert restored.priority == original.priority


class TestTodoSyncState:
    def test_create_empty(self):
        state = TodoSyncState(workflow_execution_id="wf-1")
        assert state.workflow_execution_id == "wf-1"
        assert state.todos == []
        assert state.last_sync is None

    def test_get_progress_empty(self):
        state = TodoSyncState(workflow_execution_id="wf-1")
        completed, total = state.get_progress()
        assert completed == 0
        assert total == 0

    def test_get_progress_with_todos(self):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.COMPLETED),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.IN_PROGRESS),
            SDKTodo(id="3", content="Task 3", status=TodoStatus.PENDING),
            SDKTodo(id="4", content="Task 4", status=TodoStatus.COMPLETED),
        ]
        state = TodoSyncState(workflow_execution_id="wf-1", todos=todos)
        completed, total = state.get_progress()
        assert completed == 2
        assert total == 4

    def test_get_progress_percent(self):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.COMPLETED),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.COMPLETED),
            SDKTodo(id="3", content="Task 3", status=TodoStatus.PENDING),
            SDKTodo(id="4", content="Task 4", status=TodoStatus.PENDING),
        ]
        state = TodoSyncState(workflow_execution_id="wf-1", todos=todos)
        assert state.get_progress_percent() == 50

    def test_get_progress_percent_empty(self):
        state = TodoSyncState(workflow_execution_id="wf-1")
        assert state.get_progress_percent() == 0

    def test_get_in_progress(self):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.IN_PROGRESS),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.PENDING),
            SDKTodo(id="3", content="Task 3", status=TodoStatus.IN_PROGRESS),
        ]
        state = TodoSyncState(workflow_execution_id="wf-1", todos=todos)
        in_progress = state.get_in_progress()
        assert len(in_progress) == 2
        assert all(t.status == TodoStatus.IN_PROGRESS for t in in_progress)

    def test_get_pending(self):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.PENDING),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.COMPLETED),
        ]
        state = TodoSyncState(workflow_execution_id="wf-1", todos=todos)
        pending = state.get_pending()
        assert len(pending) == 1
        assert pending[0].id == "1"

    def test_update_todo_success(self):
        todos = [SDKTodo(id="1", content="Task 1", status=TodoStatus.PENDING)]
        state = TodoSyncState(workflow_execution_id="wf-1", todos=todos)
        result = state.update_todo("1", TodoStatus.COMPLETED)
        assert result is True
        assert state.todos[0].status == TodoStatus.COMPLETED

    def test_update_todo_not_found(self):
        state = TodoSyncState(workflow_execution_id="wf-1", todos=[])
        result = state.update_todo("nonexistent", TodoStatus.COMPLETED)
        assert result is False

    def test_to_dict(self):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.COMPLETED),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.IN_PROGRESS),
        ]
        state = TodoSyncState(
            workflow_execution_id="wf-1",
            todos=todos,
            last_sync="2024-01-01T00:00:00",
        )
        d = state.to_dict()
        assert d["workflow_execution_id"] == "wf-1"
        assert len(d["todos"]) == 2
        assert d["progress"]["completed"] == 1
        assert d["progress"]["total"] == 2
        assert d["progress"]["percent"] == 50
        assert d["progress"]["in_progress"] == 1


class TestSDKMessage:
    def test_create_empty(self):
        msg = SDKMessage()
        assert msg.content == ""
        assert msg.tool_uses == []
        assert msg.todos is None

    def test_has_todos_false(self):
        msg = SDKMessage()
        assert msg.has_todos() is False

    def test_has_todos_true(self):
        todos = [SDKTodo(id="1", content="Task")]
        msg = SDKMessage(todos=todos)
        assert msg.has_todos() is True

    def test_has_todos_empty_list(self):
        msg = SDKMessage(todos=[])
        assert msg.has_todos() is False


class TestSDKConfig:
    def test_defaults(self):
        config = SDKConfig()
        assert config.max_turns == 20
        assert config.timeout_seconds == 300
        assert config.enable_todo_sync is True

    def test_custom_values(self):
        config = SDKConfig(max_turns=10, timeout_seconds=600, enable_todo_sync=False)
        assert config.max_turns == 10
        assert config.timeout_seconds == 600
        assert config.enable_todo_sync is False


class TestSDKBridge:
    def test_init_default(self):
        bridge = SDKBridge()
        assert bridge.config.max_turns == 20
        assert bridge._on_todo_update is None

    def test_init_with_config(self):
        config = SDKConfig(max_turns=5)
        bridge = SDKBridge(config=config)
        assert bridge.config.max_turns == 5

    def test_init_with_callbacks(self):
        async def on_todo(wf_id, todos):
            pass
        bridge = SDKBridge(on_todo_update=on_todo)
        assert bridge._on_todo_update is on_todo

    @patch.dict("sys.modules", {"claude_agent_sdk": MagicMock()})
    def test_is_sdk_available_true(self):
        bridge = SDKBridge()
        bridge._sdk_available = None
        assert bridge.is_sdk_available() is True

    def test_is_sdk_available_cached(self):
        bridge = SDKBridge()
        bridge._sdk_available = True
        assert bridge.is_sdk_available() is True
        bridge._sdk_available = False
        assert bridge.is_sdk_available() is False

    def test_get_todos_empty(self):
        bridge = SDKBridge()
        todos = bridge.get_todos("nonexistent")
        assert todos == []

    def test_get_progress_empty(self):
        bridge = SDKBridge()
        completed, total = bridge.get_progress("nonexistent")
        assert completed == 0
        assert total == 0

    def test_get_sync_state_none(self):
        bridge = SDKBridge()
        state = bridge.get_sync_state("nonexistent")
        assert state is None

    def test_clear_session_nonexistent(self):
        bridge = SDKBridge()
        result = bridge.clear_session("nonexistent")
        assert result is False

    def test_clear_session_exists(self):
        bridge = SDKBridge()
        bridge._active_sessions["wf-1"] = TodoSyncState(workflow_execution_id="wf-1")
        result = bridge.clear_session("wf-1")
        assert result is True
        assert "wf-1" not in bridge._active_sessions

    def test_parse_todos(self):
        bridge = SDKBridge()
        sdk_todos = [
            {"id": "1", "content": "Task 1", "status": "pending", "priority": "high"},
            {"id": "2", "content": "Task 2", "status": "completed", "priority": "low"},
        ]
        todos = bridge._parse_todos(sdk_todos, "wf-1")
        assert len(todos) == 2
        assert todos[0].id == "1"
        assert todos[0].workflow_execution_id == "wf-1"
        assert todos[1].status == TodoStatus.COMPLETED


class TestTodoSyncManager:
    @pytest.fixture
    def manager(self):
        return TodoSyncManager()

    @pytest.fixture
    def mock_db(self):
        with patch("src.workflow.todo_sync.db") as mock:
            mock.upsert_sdk_todo = MagicMock()
            mock.update_sdk_todo = MagicMock()
            mock.get_sdk_todos = MagicMock(return_value=[])
            mock.delete_sdk_todos_by_workflow = MagicMock()
            yield mock

    def test_init_default(self, manager):
        assert manager._on_todo_update is None
        assert manager._sync_states == {}

    def test_set_update_callback(self, manager):
        async def callback(wf_id, todos):
            pass
        manager.set_update_callback(callback)
        assert manager._on_todo_update is callback

    @pytest.mark.asyncio
    async def test_sync_todos(self, manager, mock_db):
        todos = [
            SDKTodo(id="1", content="Task 1"),
            SDKTodo(id="2", content="Task 2"),
        ]
        state = await manager.sync_todos("wf-1", todos)
        assert state.workflow_execution_id == "wf-1"
        assert len(state.todos) == 2
        assert mock_db.upsert_sdk_todo.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_todos_with_phase(self, manager, mock_db):
        todos = [SDKTodo(id="1", content="Task 1")]
        state = await manager.sync_todos("wf-1", todos, phase_execution_id="phase-1")
        assert state.todos[0].phase_execution_id == "phase-1"

    @pytest.mark.asyncio
    async def test_sync_todos_with_callback(self, manager, mock_db):
        callback_called = False
        received_todos = None

        async def callback(wf_id, todos):
            nonlocal callback_called, received_todos
            callback_called = True
            received_todos = todos

        manager.set_update_callback(callback)
        todos = [SDKTodo(id="1", content="Task 1")]
        await manager.sync_todos("wf-1", todos)
        assert callback_called is True
        assert len(received_todos) == 1

    @pytest.mark.asyncio
    async def test_update_todo_status(self, manager, mock_db):
        todos = [SDKTodo(id="1", content="Task 1", status=TodoStatus.PENDING)]
        await manager.sync_todos("wf-1", todos)
        result = await manager.update_todo_status("wf-1", "1", TodoStatus.COMPLETED)
        assert result is True

    @pytest.mark.asyncio
    async def test_update_todo_status_not_found(self, manager, mock_db):
        result = await manager.update_todo_status("wf-1", "nonexistent", TodoStatus.COMPLETED)
        assert result is False

    def test_get_sync_state(self, manager):
        manager._sync_states["wf-1"] = TodoSyncState(workflow_execution_id="wf-1")
        state = manager.get_sync_state("wf-1")
        assert state is not None
        assert state.workflow_execution_id == "wf-1"

    def test_get_sync_state_none(self, manager):
        state = manager.get_sync_state("nonexistent")
        assert state is None

    def test_get_todos_from_cache(self, manager):
        todos = [SDKTodo(id="1", content="Task 1")]
        manager._sync_states["wf-1"] = TodoSyncState(
            workflow_execution_id="wf-1", todos=todos
        )
        result = manager.get_todos("wf-1")
        assert len(result) == 1
        assert result[0].id == "1"

    def test_get_todos_from_db(self, manager, mock_db):
        mock_db.get_sdk_todos.return_value = [
            {"id": "1", "content": "DB Task", "status": "pending", "priority": "medium"}
        ]
        result = manager.get_todos("wf-1")
        assert len(result) == 1
        assert result[0].content == "DB Task"

    def test_get_progress(self, manager):
        todos = [
            SDKTodo(id="1", content="Task 1", status=TodoStatus.COMPLETED),
            SDKTodo(id="2", content="Task 2", status=TodoStatus.IN_PROGRESS),
            SDKTodo(id="3", content="Task 3", status=TodoStatus.PENDING),
        ]
        manager._sync_states["wf-1"] = TodoSyncState(
            workflow_execution_id="wf-1", todos=todos
        )
        progress = manager.get_progress("wf-1")
        assert progress["completed"] == 1
        assert progress["in_progress"] == 1
        assert progress["pending"] == 1
        assert progress["total"] == 3
        assert progress["percent"] == 33

    def test_clear_workflow(self, manager, mock_db):
        manager._sync_states["wf-1"] = TodoSyncState(workflow_execution_id="wf-1")
        manager.clear_workflow("wf-1")
        assert "wf-1" not in manager._sync_states
        mock_db.delete_sdk_todos_by_workflow.assert_called_once_with("wf-1")

    def test_load_from_db_cached(self, manager):
        existing = TodoSyncState(workflow_execution_id="wf-1")
        manager._sync_states["wf-1"] = existing
        result = manager.load_from_db("wf-1")
        assert result is existing

    def test_load_from_db_fresh(self, manager, mock_db):
        mock_db.get_sdk_todos.return_value = [
            {"id": "1", "content": "DB Task", "status": "pending", "priority": "medium"}
        ]
        result = manager.load_from_db("wf-1")
        assert len(result.todos) == 1
        assert result.last_sync is not None


class TestProviderTypeSDK:
    def test_claude_sdk_exists(self):
        assert hasattr(ProviderType, "CLAUDE_SDK")
        assert ProviderType.CLAUDE_SDK.value == "claude_sdk"
