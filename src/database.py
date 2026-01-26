"""
SQLite database backend for UltraClaude
"""
import sqlite3
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
from datetime import datetime

DATA_DIR = Path.home() / ".ultraclaude"
DB_FILE = DATA_DIR / "ultraclaude.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    github_repo TEXT NOT NULL,
    github_token_encrypted TEXT DEFAULT '',
    working_dir TEXT DEFAULT '',
    default_branch TEXT DEFAULT 'main',
    issue_filter TEXT DEFAULT '{}',
    auto_sync INTEGER DEFAULT 1,
    auto_start INTEGER DEFAULT 0,
    verification_command TEXT DEFAULT '',
    lint_command TEXT DEFAULT '',
    build_command TEXT DEFAULT '',
    test_command TEXT DEFAULT '',
    max_concurrent INTEGER DEFAULT 1,
    status TEXT DEFAULT 'idle',
    last_sync TEXT,
    created_at TEXT NOT NULL,
    llm_provider TEXT DEFAULT 'claude_code',
    llm_model TEXT DEFAULT '',
    llm_api_url TEXT DEFAULT '',
    llm_api_key_encrypted TEXT DEFAULT '',
    llm_context_length INTEGER DEFAULT 8192,
    llm_temperature REAL DEFAULT 0.1
);

CREATE TABLE IF NOT EXISTS issue_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    github_issue_number INTEGER NOT NULL,
    github_issue_title TEXT NOT NULL,
    github_issue_body TEXT DEFAULT '',
    github_issue_labels TEXT DEFAULT '[]',
    github_issue_url TEXT DEFAULT '',
    session_id INTEGER,
    status TEXT DEFAULT 'pending',
    branch_name TEXT DEFAULT '',
    pr_number INTEGER,
    pr_url TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    last_error TEXT DEFAULT '',
    verification_results TEXT DEFAULT '[]',
    context_files TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE INDEX IF NOT EXISTS idx_issue_sessions_project ON issue_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_issue_sessions_status ON issue_sessions(status);
CREATE INDEX IF NOT EXISTS idx_issue_sessions_issue ON issue_sessions(project_id, github_issue_number);

-- Workflow Pipeline Tables

CREATE TABLE IF NOT EXISTS workflow_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    phases TEXT DEFAULT '[]',
    max_iterations INTEGER DEFAULT 3,
    iteration_behavior TEXT DEFAULT 'auto_iterate',
    failure_behavior TEXT DEFAULT 'pause_notify',
    budget_limit REAL,
    budget_scope TEXT DEFAULT 'execution',
    is_default INTEGER DEFAULT 0,
    is_global INTEGER DEFAULT 1,
    project_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workflow_templates_project ON workflow_templates(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_templates_global ON workflow_templates(is_global);
CREATE INDEX IF NOT EXISTS idx_workflow_templates_default ON workflow_templates(is_default);

CREATE TABLE IF NOT EXISTS workflow_executions (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    template_name TEXT NOT NULL,
    trigger_mode TEXT NOT NULL,
    project_id INTEGER,
    project_path TEXT DEFAULT '',
    issue_session_id INTEGER,
    task_description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    current_phase_id TEXT,
    iteration INTEGER DEFAULT 1,
    artifact_ids TEXT DEFAULT '[]',
    total_tokens_input INTEGER DEFAULT 0,
    total_tokens_output INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0.0,
    budget_limit REAL,
    iteration_behavior TEXT DEFAULT 'auto_iterate',
    interactive_mode INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (template_id) REFERENCES workflow_templates(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    FOREIGN KEY (issue_session_id) REFERENCES issue_sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_executions_template ON workflow_executions(template_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_project ON workflow_executions(project_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON workflow_executions(status);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_issue ON workflow_executions(issue_session_id);

CREATE TABLE IF NOT EXISTS phase_executions (
    id TEXT PRIMARY KEY,
    workflow_execution_id TEXT NOT NULL,
    phase_id TEXT NOT NULL,
    phase_name TEXT NOT NULL,
    phase_role TEXT NOT NULL,
    session_id INTEGER,
    provider_used TEXT DEFAULT '',
    model_used TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    iteration INTEGER DEFAULT 1,
    input_artifact_ids TEXT DEFAULT '[]',
    output_artifact_id TEXT,
    tokens_input INTEGER DEFAULT 0,
    tokens_output INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT DEFAULT '',
    FOREIGN KEY (workflow_execution_id) REFERENCES workflow_executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_phase_executions_workflow ON phase_executions(workflow_execution_id);
CREATE INDEX IF NOT EXISTS idx_phase_executions_status ON phase_executions(status);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    workflow_execution_id TEXT NOT NULL,
    phase_execution_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    metadata TEXT DEFAULT '{}',
    is_edited INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (workflow_execution_id) REFERENCES workflow_executions(id) ON DELETE CASCADE,
    FOREIGN KEY (phase_execution_id) REFERENCES phase_executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_artifacts_workflow ON artifacts(workflow_execution_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_phase ON artifacts(phase_execution_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);

CREATE TABLE IF NOT EXISTS budget_tracking (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    budget_limit REAL,
    total_spent REAL DEFAULT 0.0,
    token_count_input INTEGER DEFAULT 0,
    token_count_output INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_budget_tracking_scope ON budget_tracking(scope, scope_id);

CREATE TABLE IF NOT EXISTS provider_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gemini_api_key_encrypted TEXT DEFAULT '',
    openai_api_key_encrypted TEXT DEFAULT '',
    openrouter_api_key_encrypted TEXT DEFAULT '',
    ollama_url TEXT DEFAULT 'http://localhost:11434',
    lm_studio_url TEXT DEFAULT 'http://localhost:1234/v1',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_name TEXT NOT NULL,
    context_length INTEGER DEFAULT 8192,
    supports_tools INTEGER DEFAULT 0,
    supports_vision INTEGER DEFAULT 0,
    cost_input_per_1k REAL DEFAULT 0.0,
    cost_output_per_1k REAL DEFAULT 0.0,
    is_available INTEGER DEFAULT 1,
    last_checked TEXT,
    metadata TEXT DEFAULT '{}',
    UNIQUE(provider, model_id)
);

CREATE INDEX IF NOT EXISTS idx_model_registry_provider ON model_registry(provider);
CREATE INDEX IF NOT EXISTS idx_model_registry_available ON model_registry(is_available);

-- OAuth Tokens Table
CREATE TABLE IF NOT EXISTS oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    user_id TEXT DEFAULT 'default',
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    token_uri TEXT,
    client_id TEXT,
    client_secret_encrypted TEXT,
    scopes TEXT,
    expires_at TEXT,
    account_email TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, user_id)
);

CREATE INDEX IF NOT EXISTS idx_oauth_tokens_provider ON oauth_tokens(provider);

-- OAuth Client Configs (for user-provided OAuth app credentials)
CREATE TABLE IF NOT EXISTS oauth_client_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL UNIQUE,
    client_config_encrypted TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Approval History for audit trail
CREATE TABLE IF NOT EXISTS approval_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    message TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT DEFAULT 'web',
    responded_at TEXT NOT NULL,
    timeout_seconds REAL,
    was_timeout INTEGER DEFAULT 0,
    FOREIGN KEY (execution_id) REFERENCES workflow_executions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approval_history_execution ON approval_history(execution_id);
CREATE INDEX IF NOT EXISTS idx_approval_history_action ON approval_history(action);

-- SDK Todo Tracking Table
CREATE TABLE IF NOT EXISTS sdk_todos (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'medium',
    phase_execution_id TEXT,
    workflow_execution_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    FOREIGN KEY (workflow_execution_id) REFERENCES workflow_executions(id) ON DELETE CASCADE,
    FOREIGN KEY (phase_execution_id) REFERENCES phase_executions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sdk_todos_workflow ON sdk_todos(workflow_execution_id);
CREATE INDEX IF NOT EXISTS idx_sdk_todos_status ON sdk_todos(status);
CREATE INDEX IF NOT EXISTS idx_sdk_todos_phase ON sdk_todos(phase_execution_id);

-- Scheduled Tasks Table
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    task_type TEXT NOT NULL,
    schedule TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    project_id INTEGER,
    last_run TEXT,
    next_run TEXT,
    run_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    last_error TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project ON scheduled_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_type ON scheduled_tasks(task_type);

-- Webhook Configurations Table
CREATE TABLE IF NOT EXISTS webhook_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    github_secret_encrypted TEXT DEFAULT '',
    auto_queue_issues INTEGER DEFAULT 1,
    auto_start_on_label TEXT DEFAULT '',
    trigger_labels TEXT DEFAULT '[]',
    ignore_labels TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_webhook_configs_project ON webhook_configs(project_id);

-- Webhook Event Log Table
CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,
    project_id INTEGER,
    payload TEXT DEFAULT '{}',
    processed INTEGER DEFAULT 0,
    result TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_project ON webhook_events(project_id);
CREATE INDEX IF NOT EXISTS idx_webhook_events_type ON webhook_events(event_type);
CREATE INDEX IF NOT EXISTS idx_webhook_events_created ON webhook_events(created_at);

-- Notification Configurations Table
CREATE TABLE IF NOT EXISTS notification_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER UNIQUE,  -- NULL for global config
    enabled INTEGER DEFAULT 1,
    events TEXT DEFAULT '[]',
    discord_enabled INTEGER DEFAULT 0,
    discord_webhook_url_encrypted TEXT DEFAULT '',
    slack_enabled INTEGER DEFAULT 0,
    slack_webhook_url_encrypted TEXT DEFAULT '',
    email_enabled INTEGER DEFAULT 0,
    email_smtp_host TEXT DEFAULT '',
    email_smtp_port INTEGER DEFAULT 587,
    email_smtp_user TEXT DEFAULT '',
    email_smtp_password_encrypted TEXT DEFAULT '',
    email_from TEXT DEFAULT '',
    email_to TEXT DEFAULT '[]',
    email_use_tls INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notification_configs_project ON notification_configs(project_id);

-- Notification Log Table
CREATE TABLE IF NOT EXISTS notification_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    project_id INTEGER,
    issue_number INTEGER,
    pr_number INTEGER,
    severity TEXT DEFAULT 'info',
    channels_sent TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_notification_log_project ON notification_log(project_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_event ON notification_log(event);
CREATE INDEX IF NOT EXISTS idx_notification_log_created ON notification_log(created_at);

-- System Settings Table (for global configurations)
CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._initialized = True
    
    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript(SCHEMA)
    
    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(DB_FILE, timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def create_project(self, data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO projects (
                    name, github_repo, github_token_encrypted, working_dir,
                    default_branch, issue_filter, auto_sync, auto_start,
                    verification_command, lint_command, build_command, test_command,
                    max_concurrent, status, created_at, llm_provider, llm_model,
                    llm_api_url, llm_api_key_encrypted, llm_context_length, llm_temperature
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('name', ''),
                data.get('github_repo', ''),
                data.get('github_token_encrypted', ''),
                data.get('working_dir', ''),
                data.get('default_branch', 'main'),
                json.dumps(data.get('issue_filter', {})),
                int(data.get('auto_sync', True)),
                int(data.get('auto_start', False)),
                data.get('verification_command', ''),
                data.get('lint_command', ''),
                data.get('build_command', ''),
                data.get('test_command', ''),
                data.get('max_concurrent', 1),
                data.get('status', 'idle'),
                data.get('created_at', datetime.now().isoformat()),
                data.get('llm_provider', 'claude_code'),
                data.get('llm_model', ''),
                data.get('llm_api_url', ''),
                data.get('llm_api_key_encrypted', ''),
                data.get('llm_context_length', 8192),
                data.get('llm_temperature', 0.1),
            ))
            return cursor.lastrowid
    
    def get_project(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            return self._row_to_project(row) if row else None
    
    def get_all_projects(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM projects").fetchall()
            return [self._row_to_project(row) for row in rows]
    
    def update_project(self, project_id: int, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key == 'issue_filter':
                value = json.dumps(value) if isinstance(value, dict) else value
            elif key in ('auto_sync', 'auto_start'):
                value = int(value)
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(project_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE projects SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return True
    
    def delete_project(self, project_id: int) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM issue_sessions WHERE project_id = ?", (project_id,))
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cursor.rowcount > 0
    
    def _row_to_project(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'name': row['name'],
            'github_repo': row['github_repo'],
            'github_token_encrypted': row['github_token_encrypted'],
            'working_dir': row['working_dir'],
            'default_branch': row['default_branch'],
            'issue_filter': json.loads(row['issue_filter']) if row['issue_filter'] else {},
            'auto_sync': bool(row['auto_sync']),
            'auto_start': bool(row['auto_start']),
            'verification_command': row['verification_command'],
            'lint_command': row['lint_command'],
            'build_command': row['build_command'],
            'test_command': row['test_command'],
            'max_concurrent': row['max_concurrent'],
            'status': row['status'],
            'last_sync': row['last_sync'],
            'created_at': row['created_at'],
            'llm_provider': row['llm_provider'],
            'llm_model': row['llm_model'],
            'llm_api_url': row['llm_api_url'],
            'llm_api_key_encrypted': row['llm_api_key_encrypted'],
            'llm_context_length': row['llm_context_length'],
            'llm_temperature': row['llm_temperature'],
        }
    
    def create_issue_session(self, data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO issue_sessions (
                    project_id, github_issue_number, github_issue_title,
                    github_issue_body, github_issue_labels, github_issue_url,
                    session_id, status, branch_name, pr_number, pr_url,
                    attempts, max_attempts, last_error, verification_results,
                    context_files, created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('project_id'),
                data.get('github_issue_number'),
                data.get('github_issue_title', ''),
                data.get('github_issue_body', ''),
                json.dumps(data.get('github_issue_labels', [])),
                data.get('github_issue_url', ''),
                data.get('session_id'),
                data.get('status', 'pending'),
                data.get('branch_name', ''),
                data.get('pr_number'),
                data.get('pr_url', ''),
                data.get('attempts', 0),
                data.get('max_attempts', 3),
                data.get('last_error', ''),
                json.dumps(data.get('verification_results', [])),
                json.dumps(data.get('context_files', [])),
                data.get('created_at', datetime.now().isoformat()),
                data.get('started_at'),
                data.get('completed_at'),
            ))
            return cursor.lastrowid
    
    def get_issue_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM issue_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return self._row_to_issue_session(row) if row else None
    
    def get_issue_sessions_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM issue_sessions WHERE project_id = ?", (project_id,)
            ).fetchall()
            return [self._row_to_issue_session(row) for row in rows]
    
    def get_issue_session_by_issue(self, project_id: int, issue_number: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM issue_sessions WHERE project_id = ? AND github_issue_number = ?",
                (project_id, issue_number)
            ).fetchone()
            return self._row_to_issue_session(row) if row else None
    
    def get_issue_session_by_session_id(self, session_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM issue_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            return self._row_to_issue_session(row) if row else None
    
    def get_all_issue_sessions(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM issue_sessions").fetchall()
            return [self._row_to_issue_session(row) for row in rows]
    
    def get_issue_sessions_by_status(self, project_id: int, status: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM issue_sessions WHERE project_id = ? AND status = ?",
                (project_id, status)
            ).fetchall()
            return [self._row_to_issue_session(row) for row in rows]
    
    def update_issue_session(self, session_id: int, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key in ('github_issue_labels', 'verification_results', 'context_files'):
                value = json.dumps(value) if isinstance(value, list) else value
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(session_id)
        with self._get_connection() as conn:
            conn.execute(
                f"UPDATE issue_sessions SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return True
    
    def add_verification_result(self, session_id: int, result: Dict[str, Any]) -> bool:
        session = self.get_issue_session(session_id)
        if not session:
            return False
        results = session.get('verification_results', [])
        results.append(result)
        return self.update_issue_session(session_id, {'verification_results': results})
    
    def delete_issue_session(self, session_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM issue_sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0
    
    def _row_to_issue_session(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'project_id': row['project_id'],
            'github_issue_number': row['github_issue_number'],
            'github_issue_title': row['github_issue_title'],
            'github_issue_body': row['github_issue_body'],
            'github_issue_labels': json.loads(row['github_issue_labels']) if row['github_issue_labels'] else [],
            'github_issue_url': row['github_issue_url'],
            'session_id': row['session_id'],
            'status': row['status'],
            'branch_name': row['branch_name'],
            'pr_number': row['pr_number'],
            'pr_url': row['pr_url'],
            'attempts': row['attempts'],
            'max_attempts': row['max_attempts'],
            'last_error': row['last_error'],
            'verification_results': json.loads(row['verification_results']) if row['verification_results'] else [],
            'context_files': json.loads(row['context_files']) if row['context_files'] else [],
            'created_at': row['created_at'],
            'started_at': row['started_at'],
            'completed_at': row['completed_at'],
        }
    
    def migrate_from_json(self, projects_file: Path, issue_sessions_file: Path):
        """Migrate data from JSON files to SQLite"""
        if projects_file.exists():
            with open(projects_file, 'r') as f:
                data = json.load(f)
            for p in data.get('projects', []):
                if not self.get_project(p.get('id', 0)):
                    self.create_project(p)
        
        if issue_sessions_file.exists():
            with open(issue_sessions_file, 'r') as f:
                data = json.load(f)
            for s in data.get('sessions', []):
                if not self.get_issue_session(s.get('id', 0)):
                    self.create_issue_session(s)

    # ==================== Workflow Template Methods ====================

    def create_workflow_template(self, data: Dict[str, Any]) -> str:
        """Create a workflow template and return its ID"""
        template_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO workflow_templates (
                    id, name, description, phases, max_iterations,
                    iteration_behavior, failure_behavior, budget_limit,
                    budget_scope, is_default, is_global, project_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                template_id,
                data.get('name', ''),
                data.get('description', ''),
                json.dumps(data.get('phases', [])),
                data.get('max_iterations', 3),
                data.get('iteration_behavior', 'auto_iterate'),
                data.get('failure_behavior', 'pause_notify'),
                data.get('budget_limit'),
                data.get('budget_scope', 'execution'),
                int(data.get('is_default', False)),
                int(data.get('is_global', True)),
                data.get('project_id'),
                data.get('created_at', datetime.now().isoformat()),
                data.get('updated_at', datetime.now().isoformat()),
            ))
            return template_id

    def get_workflow_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_templates WHERE id = ?", (template_id,)
            ).fetchone()
            return self._row_to_workflow_template(row) if row else None

    def get_workflow_templates(
        self, 
        project_id: Optional[int] = None, 
        include_global: bool = True
    ) -> List[Dict[str, Any]]:
        """Get templates for a project, optionally including global templates"""
        with self._get_connection() as conn:
            if project_id is not None:
                if include_global:
                    rows = conn.execute(
                        "SELECT * FROM workflow_templates WHERE project_id = ? OR is_global = 1",
                        (project_id,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM workflow_templates WHERE project_id = ?",
                        (project_id,)
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM workflow_templates WHERE is_global = 1"
                ).fetchall()
            return [self._row_to_workflow_template(row) for row in rows]

    def get_default_workflow_template(self, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get the default template for a project or globally"""
        with self._get_connection() as conn:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM workflow_templates WHERE project_id = ? AND is_default = 1",
                    (project_id,)
                ).fetchone()
                if row:
                    return self._row_to_workflow_template(row)
            row = conn.execute(
                "SELECT * FROM workflow_templates WHERE is_global = 1 AND is_default = 1"
            ).fetchone()
            return self._row_to_workflow_template(row) if row else None

    def update_workflow_template(self, template_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key == 'phases':
                value = json.dumps(value) if isinstance(value, list) else value
            elif key in ('is_default', 'is_global'):
                value = int(value)
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(template_id)
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE workflow_templates SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_workflow_template(self, template_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM workflow_templates WHERE id = ?", (template_id,))
            return cursor.rowcount > 0

    def _row_to_workflow_template(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'name': row['name'],
            'description': row['description'],
            'phases': json.loads(row['phases']) if row['phases'] else [],
            'max_iterations': row['max_iterations'],
            'iteration_behavior': row['iteration_behavior'],
            'failure_behavior': row['failure_behavior'],
            'budget_limit': row['budget_limit'],
            'budget_scope': row['budget_scope'],
            'is_default': bool(row['is_default']),
            'is_global': bool(row['is_global']),
            'project_id': row['project_id'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== Workflow Execution Methods ====================

    def create_workflow_execution(self, data: Dict[str, Any]) -> str:
        """Create a workflow execution and return its ID"""
        execution_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO workflow_executions (
                    id, template_id, template_name, trigger_mode, project_id,
                    project_path, issue_session_id, task_description, status,
                    current_phase_id, iteration, artifact_ids, total_tokens_input,
                    total_tokens_output, total_cost_usd, budget_limit,
                    iteration_behavior, interactive_mode, created_at, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                execution_id,
                data.get('template_id', ''),
                data.get('template_name', ''),
                data.get('trigger_mode', 'manual_task'),
                data.get('project_id'),
                data.get('project_path', ''),
                data.get('issue_session_id'),
                data.get('task_description', ''),
                data.get('status', 'pending'),
                data.get('current_phase_id'),
                data.get('iteration', 1),
                json.dumps(data.get('artifact_ids', [])),
                data.get('total_tokens_input', 0),
                data.get('total_tokens_output', 0),
                data.get('total_cost_usd', 0.0),
                data.get('budget_limit'),
                data.get('iteration_behavior', 'auto_iterate'),
                int(data.get('interactive_mode', False)),
                data.get('created_at', datetime.now().isoformat()),
                data.get('started_at'),
                data.get('completed_at'),
            ))
            return execution_id

    def get_workflow_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_executions WHERE id = ?", (execution_id,)
            ).fetchone()
            if not row:
                return None
            result = self._row_to_workflow_execution(row)
            result['phase_executions'] = self.get_phase_executions_by_workflow(execution_id)
            return result

    def get_workflow_executions(
        self,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            query = "SELECT * FROM workflow_executions WHERE 1=1"
            params: List[Any] = []
            
            if project_id is not None:
                query += " AND project_id = ?"
                params.append(project_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_workflow_execution(row) for row in rows]

    def get_workflow_execution_by_issue(self, issue_session_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_executions WHERE issue_session_id = ? ORDER BY created_at DESC LIMIT 1",
                (issue_session_id,)
            ).fetchone()
            if not row:
                return None
            result = self._row_to_workflow_execution(row)
            result['phase_executions'] = self.get_phase_executions_by_workflow(result['id'])
            return result

    def update_workflow_execution(self, execution_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key in ('id', 'phase_executions'):
                continue
            if key == 'artifact_ids':
                value = json.dumps(value) if isinstance(value, list) else value
            elif key == 'interactive_mode':
                value = int(value)
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(execution_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE workflow_executions SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_workflow_execution(self, execution_id: str) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM phase_executions WHERE workflow_execution_id = ?", (execution_id,))
            conn.execute("DELETE FROM artifacts WHERE workflow_execution_id = ?", (execution_id,))
            cursor = conn.execute("DELETE FROM workflow_executions WHERE id = ?", (execution_id,))
            return cursor.rowcount > 0

    def _row_to_workflow_execution(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'template_id': row['template_id'],
            'template_name': row['template_name'],
            'trigger_mode': row['trigger_mode'],
            'project_id': row['project_id'],
            'project_path': row['project_path'],
            'issue_session_id': row['issue_session_id'],
            'task_description': row['task_description'],
            'status': row['status'],
            'current_phase_id': row['current_phase_id'],
            'iteration': row['iteration'],
            'artifact_ids': json.loads(row['artifact_ids']) if row['artifact_ids'] else [],
            'total_tokens_input': row['total_tokens_input'],
            'total_tokens_output': row['total_tokens_output'],
            'total_cost_usd': row['total_cost_usd'],
            'budget_limit': row['budget_limit'],
            'iteration_behavior': row['iteration_behavior'],
            'interactive_mode': bool(row['interactive_mode']),
            'created_at': row['created_at'],
            'started_at': row['started_at'],
            'completed_at': row['completed_at'],
            'phase_executions': [],
        }

    # ==================== Phase Execution Methods ====================

    def create_phase_execution(self, data: Dict[str, Any]) -> str:
        """Create a phase execution and return its ID"""
        phase_exec_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO phase_executions (
                    id, workflow_execution_id, phase_id, phase_name, phase_role,
                    session_id, provider_used, model_used, status, iteration,
                    input_artifact_ids, output_artifact_id, tokens_input,
                    tokens_output, cost_usd, started_at, completed_at, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                phase_exec_id,
                data.get('workflow_execution_id', ''),
                data.get('phase_id', ''),
                data.get('phase_name', ''),
                data.get('phase_role', ''),
                data.get('session_id'),
                data.get('provider_used', ''),
                data.get('model_used', ''),
                data.get('status', 'pending'),
                data.get('iteration', 1),
                json.dumps(data.get('input_artifact_ids', [])),
                data.get('output_artifact_id'),
                data.get('tokens_input', 0),
                data.get('tokens_output', 0),
                data.get('cost_usd', 0.0),
                data.get('started_at'),
                data.get('completed_at'),
                data.get('error_message', ''),
            ))
            return phase_exec_id

    def get_phase_execution(self, phase_exec_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM phase_executions WHERE id = ?", (phase_exec_id,)
            ).fetchone()
            return self._row_to_phase_execution(row) if row else None

    def get_phase_executions_by_workflow(self, workflow_execution_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM phase_executions WHERE workflow_execution_id = ?",
                (workflow_execution_id,)
            ).fetchall()
            return [self._row_to_phase_execution(row) for row in rows]

    def update_phase_execution(self, phase_exec_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key == 'input_artifact_ids':
                value = json.dumps(value) if isinstance(value, list) else value
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(phase_exec_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE phase_executions SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def _row_to_phase_execution(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'workflow_execution_id': row['workflow_execution_id'],
            'phase_id': row['phase_id'],
            'phase_name': row['phase_name'],
            'phase_role': row['phase_role'],
            'session_id': row['session_id'],
            'provider_used': row['provider_used'],
            'model_used': row['model_used'],
            'status': row['status'],
            'iteration': row['iteration'],
            'input_artifact_ids': json.loads(row['input_artifact_ids']) if row['input_artifact_ids'] else [],
            'output_artifact_id': row['output_artifact_id'],
            'tokens_input': row['tokens_input'],
            'tokens_output': row['tokens_output'],
            'cost_usd': row['cost_usd'],
            'started_at': row['started_at'],
            'completed_at': row['completed_at'],
            'error_message': row['error_message'],
        }

    # ==================== Artifact Methods ====================

    def create_artifact(self, data: Dict[str, Any]) -> str:
        """Create an artifact and return its ID"""
        artifact_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO artifacts (
                    id, workflow_execution_id, phase_execution_id, artifact_type,
                    name, content, file_path, metadata, is_edited, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                artifact_id,
                data.get('workflow_execution_id', ''),
                data.get('phase_execution_id', ''),
                data.get('artifact_type', ''),
                data.get('name', ''),
                data.get('content', ''),
                data.get('file_path', ''),
                json.dumps(data.get('metadata', {})),
                int(data.get('is_edited', False)),
                data.get('created_at', datetime.now().isoformat()),
                data.get('updated_at', datetime.now().isoformat()),
            ))
            return artifact_id

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ).fetchone()
            return self._row_to_artifact(row) if row else None

    def get_artifacts_by_workflow(self, workflow_execution_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE workflow_execution_id = ?",
                (workflow_execution_id,)
            ).fetchall()
            return [self._row_to_artifact(row) for row in rows]

    def get_artifacts_by_phase(self, phase_execution_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE phase_execution_id = ?",
                (phase_execution_id,)
            ).fetchall()
            return [self._row_to_artifact(row) for row in rows]

    def update_artifact(self, artifact_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key == 'metadata':
                value = json.dumps(value) if isinstance(value, dict) else value
            elif key == 'is_edited':
                value = int(value)
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(artifact_id)
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE artifacts SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_artifact(self, artifact_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
            return cursor.rowcount > 0

    def _row_to_artifact(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'workflow_execution_id': row['workflow_execution_id'],
            'phase_execution_id': row['phase_execution_id'],
            'artifact_type': row['artifact_type'],
            'name': row['name'],
            'content': row['content'],
            'file_path': row['file_path'],
            'metadata': json.loads(row['metadata']) if row['metadata'] else {},
            'is_edited': bool(row['is_edited']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== Budget Tracking Methods ====================

    def create_budget_tracker(self, data: Dict[str, Any]) -> str:
        """Create a budget tracker and return its ID"""
        tracker_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO budget_tracking (
                    id, scope, scope_id, period_start, budget_limit,
                    total_spent, token_count_input, token_count_output
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tracker_id,
                data.get('scope', 'execution'),
                data.get('scope_id', ''),
                data.get('period_start', datetime.now().isoformat()),
                data.get('budget_limit'),
                data.get('total_spent', 0.0),
                data.get('token_count_input', 0),
                data.get('token_count_output', 0),
            ))
            return tracker_id

    def get_budget_tracker(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM budget_tracking WHERE scope = ? AND scope_id = ?",
                (scope, scope_id)
            ).fetchone()
            return self._row_to_budget_tracker(row) if row else None

    def update_budget_tracker(self, tracker_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        values.append(tracker_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE budget_tracking SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def increment_budget(
        self, 
        scope: str, 
        scope_id: str, 
        cost: float, 
        tokens_in: int, 
        tokens_out: int
    ) -> bool:
        """Increment budget tracking for a scope"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE budget_tracking 
                SET total_spent = total_spent + ?,
                    token_count_input = token_count_input + ?,
                    token_count_output = token_count_output + ?
                WHERE scope = ? AND scope_id = ?
            """, (cost, tokens_in, tokens_out, scope, scope_id))
            return cursor.rowcount > 0

    def _row_to_budget_tracker(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'scope': row['scope'],
            'scope_id': row['scope_id'],
            'period_start': row['period_start'],
            'budget_limit': row['budget_limit'],
            'total_spent': row['total_spent'],
            'token_count_input': row['token_count_input'],
            'token_count_output': row['token_count_output'],
        }

    # ==================== Provider Keys Methods ====================

    def get_provider_keys(self) -> Optional[Dict[str, Any]]:
        """Get provider keys (singleton row)"""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM provider_keys LIMIT 1").fetchone()
            return self._row_to_provider_keys(row) if row else None

    def save_provider_keys(self, data: Dict[str, Any]) -> bool:
        """Save provider keys (upsert singleton row)"""
        with self._get_connection() as conn:
            existing = conn.execute("SELECT id FROM provider_keys LIMIT 1").fetchone()
            if existing:
                conn.execute("""
                    UPDATE provider_keys SET
                        gemini_api_key_encrypted = ?,
                        openai_api_key_encrypted = ?,
                        openrouter_api_key_encrypted = ?,
                        ollama_url = ?,
                        lm_studio_url = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    data.get('gemini_api_key_encrypted', ''),
                    data.get('openai_api_key_encrypted', ''),
                    data.get('openrouter_api_key_encrypted', ''),
                    data.get('ollama_url', 'http://localhost:11434'),
                    data.get('lm_studio_url', 'http://localhost:1234/v1'),
                    datetime.now().isoformat(),
                    existing['id'],
                ))
            else:
                conn.execute("""
                    INSERT INTO provider_keys (
                        gemini_api_key_encrypted, openai_api_key_encrypted,
                        openrouter_api_key_encrypted, ollama_url, lm_studio_url, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    data.get('gemini_api_key_encrypted', ''),
                    data.get('openai_api_key_encrypted', ''),
                    data.get('openrouter_api_key_encrypted', ''),
                    data.get('ollama_url', 'http://localhost:11434'),
                    data.get('lm_studio_url', 'http://localhost:1234/v1'),
                    datetime.now().isoformat(),
                ))
            return True

    def _row_to_provider_keys(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'gemini_api_key_encrypted': row['gemini_api_key_encrypted'],
            'openai_api_key_encrypted': row['openai_api_key_encrypted'],
            'openrouter_api_key_encrypted': row['openrouter_api_key_encrypted'],
            'ollama_url': row['ollama_url'],
            'lm_studio_url': row['lm_studio_url'],
            'updated_at': row['updated_at'],
        }

    # ==================== Model Registry Methods ====================

    def upsert_model(self, data: Dict[str, Any]) -> int:
        """Insert or update a model in the registry"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO model_registry (
                    provider, model_id, model_name, context_length,
                    supports_tools, supports_vision, cost_input_per_1k,
                    cost_output_per_1k, is_available, last_checked, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, model_id) DO UPDATE SET
                    model_name = excluded.model_name,
                    context_length = excluded.context_length,
                    supports_tools = excluded.supports_tools,
                    supports_vision = excluded.supports_vision,
                    cost_input_per_1k = excluded.cost_input_per_1k,
                    cost_output_per_1k = excluded.cost_output_per_1k,
                    is_available = excluded.is_available,
                    last_checked = excluded.last_checked,
                    metadata = excluded.metadata
            """, (
                data.get('provider', ''),
                data.get('model_id', ''),
                data.get('model_name', ''),
                data.get('context_length', 8192),
                int(data.get('supports_tools', False)),
                int(data.get('supports_vision', False)),
                data.get('cost_input_per_1k', 0.0),
                data.get('cost_output_per_1k', 0.0),
                int(data.get('is_available', True)),
                data.get('last_checked', datetime.now().isoformat()),
                json.dumps(data.get('metadata', {})),
            ))
            return cursor.lastrowid or 0

    def get_models_by_provider(self, provider: str, available_only: bool = True) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if available_only:
                rows = conn.execute(
                    "SELECT * FROM model_registry WHERE provider = ? AND is_available = 1",
                    (provider,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM model_registry WHERE provider = ?",
                    (provider,)
                ).fetchall()
            return [self._row_to_model(row) for row in rows]

    def get_all_available_models(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM model_registry WHERE is_available = 1"
            ).fetchall()
            return [self._row_to_model(row) for row in rows]

    def mark_models_unavailable(self, provider: str) -> bool:
        """Mark all models for a provider as unavailable (before refresh)"""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE model_registry SET is_available = 0 WHERE provider = ?",
                (provider,)
            )
            return True

    def _row_to_model(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'provider': row['provider'],
            'model_id': row['model_id'],
            'model_name': row['model_name'],
            'context_length': row['context_length'],
            'supports_tools': bool(row['supports_tools']),
            'supports_vision': bool(row['supports_vision']),
            'cost_input_per_1k': row['cost_input_per_1k'],
            'cost_output_per_1k': row['cost_output_per_1k'],
            'is_available': bool(row['is_available']),
            'last_checked': row['last_checked'],
            'metadata': json.loads(row['metadata']) if row['metadata'] else {},
        }

    # ==================== OAuth Token Methods ====================

    def save_oauth_token(self, data: Dict[str, Any]) -> int:
        """Save or update OAuth token for a provider"""
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM oauth_tokens WHERE provider = ? AND user_id = ?",
                (data.get('provider', ''), data.get('user_id', 'default'))
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE oauth_tokens SET
                        access_token_encrypted = ?,
                        refresh_token_encrypted = ?,
                        token_uri = ?,
                        client_id = ?,
                        client_secret_encrypted = ?,
                        scopes = ?,
                        expires_at = ?,
                        account_email = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    data.get('access_token_encrypted', ''),
                    data.get('refresh_token_encrypted', ''),
                    data.get('token_uri', ''),
                    data.get('client_id', ''),
                    data.get('client_secret_encrypted', ''),
                    json.dumps(data.get('scopes', [])),
                    data.get('expires_at'),
                    data.get('account_email', ''),
                    datetime.now().isoformat(),
                    existing['id'],
                ))
                return existing['id']
            else:
                cursor = conn.execute("""
                    INSERT INTO oauth_tokens (
                        provider, user_id, access_token_encrypted, refresh_token_encrypted,
                        token_uri, client_id, client_secret_encrypted, scopes,
                        expires_at, account_email, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    data.get('provider', ''),
                    data.get('user_id', 'default'),
                    data.get('access_token_encrypted', ''),
                    data.get('refresh_token_encrypted', ''),
                    data.get('token_uri', ''),
                    data.get('client_id', ''),
                    data.get('client_secret_encrypted', ''),
                    json.dumps(data.get('scopes', [])),
                    data.get('expires_at'),
                    data.get('account_email', ''),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ))
                return cursor.lastrowid or 0

    def get_oauth_token(self, provider: str, user_id: str = 'default') -> Optional[Dict[str, Any]]:
        """Get OAuth token for a provider"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_tokens WHERE provider = ? AND user_id = ?",
                (provider, user_id)
            ).fetchone()
            return self._row_to_oauth_token(row) if row else None

    def get_all_oauth_tokens(self, user_id: str = 'default') -> List[Dict[str, Any]]:
        """Get all OAuth tokens for a user"""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM oauth_tokens WHERE user_id = ?",
                (user_id,)
            ).fetchall()
            return [self._row_to_oauth_token(row) for row in rows]

    def delete_oauth_token(self, provider: str, user_id: str = 'default') -> bool:
        """Delete OAuth token for a provider"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM oauth_tokens WHERE provider = ? AND user_id = ?",
                (provider, user_id)
            )
            return cursor.rowcount > 0

    def update_oauth_token_expiry(
        self, 
        provider: str, 
        access_token_encrypted: str,
        expires_at: str,
        user_id: str = 'default'
    ) -> bool:
        """Update just the access token and expiry (for refresh)"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE oauth_tokens SET
                    access_token_encrypted = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE provider = ? AND user_id = ?
            """, (
                access_token_encrypted,
                expires_at,
                datetime.now().isoformat(),
                provider,
                user_id,
            ))
            return cursor.rowcount > 0

    def _row_to_oauth_token(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'provider': row['provider'],
            'user_id': row['user_id'],
            'access_token_encrypted': row['access_token_encrypted'],
            'refresh_token_encrypted': row['refresh_token_encrypted'],
            'token_uri': row['token_uri'],
            'client_id': row['client_id'],
            'client_secret_encrypted': row['client_secret_encrypted'],
            'scopes': json.loads(row['scopes']) if row['scopes'] else [],
            'expires_at': row['expires_at'],
            'account_email': row['account_email'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== OAuth Client Config Methods ====================

    def save_oauth_client_config(self, provider: str, client_config_encrypted: str) -> int:
        """Save OAuth client config (OAuth app credentials) for a provider"""
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM oauth_client_configs WHERE provider = ?",
                (provider,)
            ).fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE oauth_client_configs SET
                        client_config_encrypted = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    client_config_encrypted,
                    datetime.now().isoformat(),
                    existing['id'],
                ))
                return existing['id']
            else:
                cursor = conn.execute("""
                    INSERT INTO oauth_client_configs (
                        provider, client_config_encrypted, created_at, updated_at
                    ) VALUES (?, ?, ?, ?)
                """, (
                    provider,
                    client_config_encrypted,
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ))
                return cursor.lastrowid or 0

    def get_oauth_client_config(self, provider: str) -> Optional[Dict[str, Any]]:
        """Get OAuth client config for a provider"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_client_configs WHERE provider = ?",
                (provider,)
            ).fetchone()
            if not row:
                return None
            return {
                'id': row['id'],
                'provider': row['provider'],
                'client_config_encrypted': row['client_config_encrypted'],
                'created_at': row['created_at'],
                'updated_at': row['updated_at'],
            }

    def delete_oauth_client_config(self, provider: str) -> bool:
        """Delete OAuth client config for a provider"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM oauth_client_configs WHERE provider = ?",
                (provider,)
            )
            return cursor.rowcount > 0

    # ==================== Approval History Methods ====================

    def create_approval_record(self, data: Dict[str, Any]) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO approval_history (
                    execution_id, message, action, source,
                    responded_at, timeout_seconds, was_timeout
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data.get('execution_id', ''),
                data.get('message', ''),
                data.get('action', ''),
                data.get('source', 'web'),
                data.get('responded_at', datetime.now().isoformat()),
                data.get('timeout_seconds'),
                int(data.get('was_timeout', False)),
            ))
            return cursor.lastrowid or 0

    def get_approval_history(self, execution_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_history WHERE execution_id = ? ORDER BY responded_at DESC",
                (execution_id,)
            ).fetchall()
            return [self._row_to_approval_record(row) for row in rows]

    def get_recent_approvals(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM approval_history ORDER BY responded_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [self._row_to_approval_record(row) for row in rows]

    def _row_to_approval_record(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'execution_id': row['execution_id'],
            'message': row['message'],
            'action': row['action'],
            'source': row['source'],
            'responded_at': row['responded_at'],
            'timeout_seconds': row['timeout_seconds'],
            'was_timeout': bool(row['was_timeout']),
        }

    # ==================== SDK Todo Methods ====================

    def upsert_sdk_todo(self, data: Dict[str, Any]) -> bool:
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO sdk_todos (
                    id, content, status, priority, phase_execution_id,
                    workflow_execution_id, created_at, updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content = excluded.content,
                    status = excluded.status,
                    priority = excluded.priority,
                    phase_execution_id = excluded.phase_execution_id,
                    updated_at = excluded.updated_at,
                    metadata = excluded.metadata
            """, (
                data.get('id', ''),
                data.get('content', ''),
                data.get('status', 'pending'),
                data.get('priority', 'medium'),
                data.get('phase_execution_id'),
                data.get('workflow_execution_id', ''),
                data.get('created_at', datetime.now().isoformat()),
                data.get('updated_at', datetime.now().isoformat()),
                json.dumps(data.get('metadata', {})),
            ))
            return True

    def get_sdk_todos(self, workflow_execution_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sdk_todos WHERE workflow_execution_id = ? ORDER BY created_at",
                (workflow_execution_id,)
            ).fetchall()
            return [self._row_to_sdk_todo(row) for row in rows]

    def get_sdk_todo(self, todo_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM sdk_todos WHERE id = ?", (todo_id,)
            ).fetchone()
            return self._row_to_sdk_todo(row) if row else None

    def update_sdk_todo(self, todo_id: str, data: Dict[str, Any]) -> bool:
        fields = []
        values = []
        for key, value in data.items():
            if key == 'id':
                continue
            if key == 'metadata':
                value = json.dumps(value) if isinstance(value, dict) else value
            fields.append(f"{key} = ?")
            values.append(value)
        
        if not fields:
            return False
        
        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(todo_id)
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE sdk_todos SET {', '.join(fields)} WHERE id = ?",
                values
            )
            return cursor.rowcount > 0

    def delete_sdk_todos_by_workflow(self, workflow_execution_id: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sdk_todos WHERE workflow_execution_id = ?",
                (workflow_execution_id,)
            )
            return cursor.rowcount

    def _row_to_sdk_todo(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'content': row['content'],
            'status': row['status'],
            'priority': row['priority'],
            'phase_execution_id': row['phase_execution_id'],
            'workflow_execution_id': row['workflow_execution_id'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
            'metadata': json.loads(row['metadata']) if row['metadata'] else {},
        }

    # ==================== Scheduled Tasks Methods ====================

    def upsert_scheduled_task(self, data: Dict[str, Any]) -> str:
        """Create or update a scheduled task"""
        task_id = data.get('id', '')
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO scheduled_tasks (
                    id, name, task_type, schedule, enabled, project_id,
                    last_run, next_run, run_count, error_count, last_error,
                    config, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    task_type = excluded.task_type,
                    schedule = excluded.schedule,
                    enabled = excluded.enabled,
                    last_run = excluded.last_run,
                    next_run = excluded.next_run,
                    run_count = excluded.run_count,
                    error_count = excluded.error_count,
                    last_error = excluded.last_error,
                    config = excluded.config,
                    updated_at = excluded.updated_at
            """, (
                task_id,
                data.get('name', ''),
                data.get('task_type', ''),
                data.get('schedule', ''),
                int(data.get('enabled', True)),
                data.get('project_id'),
                data.get('last_run'),
                data.get('next_run'),
                data.get('run_count', 0),
                data.get('error_count', 0),
                data.get('last_error', ''),
                json.dumps(data.get('config', {})),
                data.get('created_at', datetime.now().isoformat()),
                datetime.now().isoformat(),
            ))
            return task_id

    def get_scheduled_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            return self._row_to_scheduled_task(row) if row else None

    def get_all_scheduled_tasks(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM scheduled_tasks").fetchall()
            return [self._row_to_scheduled_task(row) for row in rows]

    def get_scheduled_tasks_by_project(self, project_id: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE project_id = ?", (project_id,)
            ).fetchall()
            return [self._row_to_scheduled_task(row) for row in rows]

    def delete_scheduled_task(self, task_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            return cursor.rowcount > 0

    def _row_to_scheduled_task(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'name': row['name'],
            'task_type': row['task_type'],
            'schedule': row['schedule'],
            'enabled': bool(row['enabled']),
            'project_id': row['project_id'],
            'last_run': row['last_run'],
            'next_run': row['next_run'],
            'run_count': row['run_count'],
            'error_count': row['error_count'],
            'last_error': row['last_error'],
            'config': json.loads(row['config']) if row['config'] else {},
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== Webhook Config Methods ====================

    def save_webhook_config(self, data: Dict[str, Any]) -> int:
        """Save webhook configuration for a project"""
        project_id = data.get('project_id')
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM webhook_configs WHERE project_id = ?", (project_id,)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE webhook_configs SET
                        enabled = ?,
                        github_secret_encrypted = ?,
                        auto_queue_issues = ?,
                        auto_start_on_label = ?,
                        trigger_labels = ?,
                        ignore_labels = ?,
                        updated_at = ?
                    WHERE project_id = ?
                """, (
                    int(data.get('enabled', True)),
                    data.get('github_secret_encrypted', ''),
                    int(data.get('auto_queue_issues', True)),
                    data.get('auto_start_on_label', ''),
                    json.dumps(data.get('trigger_labels', [])),
                    json.dumps(data.get('ignore_labels', [])),
                    datetime.now().isoformat(),
                    project_id,
                ))
                return existing['id']
            else:
                cursor = conn.execute("""
                    INSERT INTO webhook_configs (
                        project_id, enabled, github_secret_encrypted, auto_queue_issues,
                        auto_start_on_label, trigger_labels, ignore_labels, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project_id,
                    int(data.get('enabled', True)),
                    data.get('github_secret_encrypted', ''),
                    int(data.get('auto_queue_issues', True)),
                    data.get('auto_start_on_label', ''),
                    json.dumps(data.get('trigger_labels', [])),
                    json.dumps(data.get('ignore_labels', [])),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ))
                return cursor.lastrowid or 0

    def get_webhook_config(self, project_id: int) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM webhook_configs WHERE project_id = ?", (project_id,)
            ).fetchone()
            return self._row_to_webhook_config(row) if row else None

    def delete_webhook_config(self, project_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM webhook_configs WHERE project_id = ?", (project_id,)
            )
            return cursor.rowcount > 0

    def _row_to_webhook_config(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'project_id': row['project_id'],
            'enabled': bool(row['enabled']),
            'github_secret_encrypted': row['github_secret_encrypted'],
            'auto_queue_issues': bool(row['auto_queue_issues']),
            'auto_start_on_label': row['auto_start_on_label'],
            'trigger_labels': json.loads(row['trigger_labels']) if row['trigger_labels'] else [],
            'ignore_labels': json.loads(row['ignore_labels']) if row['ignore_labels'] else [],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== Notification Config Methods ====================

    def save_notification_config(self, data: Dict[str, Any]) -> int:
        """Save notification configuration (global if project_id is None)"""
        project_id = data.get('project_id')
        with self._get_connection() as conn:
            if project_id is None:
                existing = conn.execute(
                    "SELECT id FROM notification_configs WHERE project_id IS NULL"
                ).fetchone()
            else:
                existing = conn.execute(
                    "SELECT id FROM notification_configs WHERE project_id = ?", (project_id,)
                ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE notification_configs SET
                        enabled = ?,
                        events = ?,
                        discord_enabled = ?,
                        discord_webhook_url_encrypted = ?,
                        slack_enabled = ?,
                        slack_webhook_url_encrypted = ?,
                        email_enabled = ?,
                        email_smtp_host = ?,
                        email_smtp_port = ?,
                        email_smtp_user = ?,
                        email_smtp_password_encrypted = ?,
                        email_from = ?,
                        email_to = ?,
                        email_use_tls = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    int(data.get('enabled', True)),
                    json.dumps(data.get('events', [])),
                    int(data.get('discord_enabled', False)),
                    data.get('discord_webhook_url_encrypted', ''),
                    int(data.get('slack_enabled', False)),
                    data.get('slack_webhook_url_encrypted', ''),
                    int(data.get('email_enabled', False)),
                    data.get('email_smtp_host', ''),
                    data.get('email_smtp_port', 587),
                    data.get('email_smtp_user', ''),
                    data.get('email_smtp_password_encrypted', ''),
                    data.get('email_from', ''),
                    json.dumps(data.get('email_to', [])),
                    int(data.get('email_use_tls', True)),
                    datetime.now().isoformat(),
                    existing['id'],
                ))
                return existing['id']
            else:
                cursor = conn.execute("""
                    INSERT INTO notification_configs (
                        project_id, enabled, events, discord_enabled, discord_webhook_url_encrypted,
                        slack_enabled, slack_webhook_url_encrypted, email_enabled, email_smtp_host,
                        email_smtp_port, email_smtp_user, email_smtp_password_encrypted, email_from,
                        email_to, email_use_tls, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    project_id,
                    int(data.get('enabled', True)),
                    json.dumps(data.get('events', [])),
                    int(data.get('discord_enabled', False)),
                    data.get('discord_webhook_url_encrypted', ''),
                    int(data.get('slack_enabled', False)),
                    data.get('slack_webhook_url_encrypted', ''),
                    int(data.get('email_enabled', False)),
                    data.get('email_smtp_host', ''),
                    data.get('email_smtp_port', 587),
                    data.get('email_smtp_user', ''),
                    data.get('email_smtp_password_encrypted', ''),
                    data.get('email_from', ''),
                    json.dumps(data.get('email_to', [])),
                    int(data.get('email_use_tls', True)),
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ))
                return cursor.lastrowid or 0

    def get_notification_config(self, project_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Get notification config (global if project_id is None)"""
        with self._get_connection() as conn:
            if project_id is None:
                row = conn.execute(
                    "SELECT * FROM notification_configs WHERE project_id IS NULL"
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM notification_configs WHERE project_id = ?", (project_id,)
                ).fetchone()
            return self._row_to_notification_config(row) if row else None

    def _row_to_notification_config(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            'id': row['id'],
            'project_id': row['project_id'],
            'enabled': bool(row['enabled']),
            'events': json.loads(row['events']) if row['events'] else [],
            'discord_enabled': bool(row['discord_enabled']),
            'discord_webhook_url_encrypted': row['discord_webhook_url_encrypted'],
            'slack_enabled': bool(row['slack_enabled']),
            'slack_webhook_url_encrypted': row['slack_webhook_url_encrypted'],
            'email_enabled': bool(row['email_enabled']),
            'email_smtp_host': row['email_smtp_host'],
            'email_smtp_port': row['email_smtp_port'],
            'email_smtp_user': row['email_smtp_user'],
            'email_smtp_password_encrypted': row['email_smtp_password_encrypted'],
            'email_from': row['email_from'],
            'email_to': json.loads(row['email_to']) if row['email_to'] else [],
            'email_use_tls': bool(row['email_use_tls']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    # ==================== System Settings Methods ====================

    def get_setting(self, key: str) -> Optional[str]:
        """Get a system setting value"""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value FROM system_settings WHERE key = ?", (key,)
            ).fetchone()
            return row['value'] if row else None

    def set_setting(self, key: str, value: str) -> bool:
        """Set a system setting value"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO system_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
            """, (key, value, datetime.now().isoformat()))
            return True

    def get_all_settings(self) -> Dict[str, str]:
        """Get all system settings"""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
            return {row['key']: row['value'] for row in rows}


db = Database()
