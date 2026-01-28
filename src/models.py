"""
Data models for Autowrkers GitHub Integration
"""
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any
from cryptography.fernet import Fernet

# Data directory
DATA_DIR = Path.home() / ".autowrkers"
PROJECTS_FILE = DATA_DIR / "projects.json"
ISSUE_SESSIONS_FILE = DATA_DIR / "issue_sessions.json"
ENCRYPTION_KEY_FILE = DATA_DIR / ".encryption_key"


def get_encryption_key() -> bytes:
    """Get or create encryption key for token storage"""
    if ENCRYPTION_KEY_FILE.exists():
        return ENCRYPTION_KEY_FILE.read_bytes()
    else:
        key = Fernet.generate_key()
        ENCRYPTION_KEY_FILE.write_bytes(key)
        ENCRYPTION_KEY_FILE.chmod(0o600)  # Owner read/write only
        return key


def encrypt_token(token: str) -> str:
    """Encrypt a GitHub token"""
    if not token:
        return ""
    f = Fernet(get_encryption_key())
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a GitHub token"""
    if not encrypted:
        return ""
    f = Fernet(get_encryption_key())
    return f.decrypt(encrypted.encode()).decode()


class ProjectStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class IssueSessionStatus(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    VERIFYING = "verifying"
    VERIFICATION_FAILED = "verification_failed"
    PR_CREATED = "pr_created"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


@dataclass
class IssueFilter:
    """Filter criteria for GitHub issues"""
    labels: List[str] = field(default_factory=list)
    exclude_labels: List[str] = field(default_factory=list)
    assignee: Optional[str] = None
    milestone: Optional[str] = None
    state: str = "open"  # open, closed, all

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'IssueFilter':
        return cls(**data) if data else cls()


@dataclass
class Project:
    """A project links Autowrkers to a GitHub repository"""
    id: int
    name: str
    github_repo: str  # owner/repo format
    github_token_encrypted: str = ""
    working_dir: str = ""
    default_branch: str = "main"
    issue_filter: IssueFilter = field(default_factory=IssueFilter)
    auto_sync: bool = True
    auto_start: bool = False
    verification_command: str = ""
    lint_command: str = ""
    build_command: str = ""
    test_command: str = ""
    max_concurrent: int = 1
    status: ProjectStatus = ProjectStatus.IDLE
    last_sync: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    # LLM Provider Settings
    llm_provider: str = "claude_code"  # claude_code, ollama, lm_studio, openrouter
    llm_model: str = ""                 # e.g., "llama3.2:latest", "anthropic/claude-3.5-sonnet"
    llm_api_url: str = ""               # API endpoint URL
    llm_api_key_encrypted: str = ""     # API key (encrypted)
    llm_context_length: int = 8192      # Context window size
    llm_temperature: float = 0.1        # Temperature for generation

    def set_token(self, token: str):
        """Set GitHub token (encrypts automatically)"""
        self.github_token_encrypted = encrypt_token(token)

    def get_token(self) -> str:
        """Get decrypted GitHub token"""
        return decrypt_token(self.github_token_encrypted)

    def set_llm_api_key(self, api_key: str):
        """Set LLM API key (encrypts automatically)"""
        self.llm_api_key_encrypted = encrypt_token(api_key) if api_key else ""

    def get_llm_api_key(self) -> str:
        """Get decrypted LLM API key"""
        return decrypt_token(self.llm_api_key_encrypted) if self.llm_api_key_encrypted else ""

    def get_llm_config(self):
        """Get LLM provider configuration"""
        from .llm_provider import LLMProviderConfig, LLMProviderType
        return LLMProviderConfig(
            provider_type=LLMProviderType(self.llm_provider),
            model_name=self.llm_model,
            api_url=self.llm_api_url,
            api_key=self.get_llm_api_key(),
            context_length=self.llm_context_length,
            temperature=self.llm_temperature,
        )

    def uses_claude_code(self) -> bool:
        """Check if this project uses Claude Code (default provider)"""
        return self.llm_provider == "claude_code"

    def to_dict(self) -> dict:
        """Convert to dictionary (safe for API response - no token)"""
        return {
            "id": self.id,
            "name": self.name,
            "github_repo": self.github_repo,
            "working_dir": self.working_dir,
            "default_branch": self.default_branch,
            "issue_filter": self.issue_filter.to_dict() if isinstance(self.issue_filter, IssueFilter) else self.issue_filter,
            "auto_sync": self.auto_sync,
            "auto_start": self.auto_start,
            "verification_command": self.verification_command,
            "lint_command": self.lint_command,
            "build_command": self.build_command,
            "test_command": self.test_command,
            "max_concurrent": self.max_concurrent,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "last_sync": self.last_sync,
            "created_at": self.created_at,
            "has_token": bool(self.github_token_encrypted),
            # LLM settings
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_api_url": self.llm_api_url,
            "llm_context_length": self.llm_context_length,
            "llm_temperature": self.llm_temperature,
            "has_llm_api_key": bool(self.llm_api_key_encrypted),
        }

    def to_persist_dict(self) -> dict:
        """Convert to dictionary for persistence (includes encrypted tokens)"""
        d = self.to_dict()
        d["github_token_encrypted"] = self.github_token_encrypted
        d["llm_api_key_encrypted"] = self.llm_api_key_encrypted
        del d["has_token"]
        del d["has_llm_api_key"]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'Project':
        """Create from dictionary"""
        data = data.copy()
        if "issue_filter" in data and isinstance(data["issue_filter"], dict):
            data["issue_filter"] = IssueFilter.from_dict(data["issue_filter"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = ProjectStatus(data["status"])
        # Remove computed fields that shouldn't be passed to constructor
        if "has_token" in data:
            del data["has_token"]
        if "has_llm_api_key" in data:
            del data["has_llm_api_key"]
        return cls(**data)


@dataclass
class GitHubIssue:
    """Represents a GitHub issue"""
    number: int
    title: str
    body: str
    labels: List[str] = field(default_factory=list)
    assignees: List[str] = field(default_factory=list)
    milestone: Optional[str] = None
    state: str = "open"
    html_url: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_api_response(cls, data: dict) -> 'GitHubIssue':
        """Create from GitHub API response"""
        return cls(
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            labels=[l["name"] for l in data.get("labels", [])],
            assignees=[a["login"] for a in data.get("assignees", [])],
            milestone=data.get("milestone", {}).get("title") if data.get("milestone") else None,
            state=data["state"],
            html_url=data.get("html_url", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


@dataclass
class VerificationResult:
    """Result of a verification check"""
    check_type: str  # lint, test, build, custom
    passed: bool
    output: str = ""
    duration_ms: int = 0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'VerificationResult':
        return cls(**data)


@dataclass
class IssueSession:
    """Links a GitHub issue to an Autowrkers session"""
    id: int
    project_id: int
    github_issue_number: int
    github_issue_title: str
    github_issue_body: str
    github_issue_labels: List[str] = field(default_factory=list)
    github_issue_url: str = ""
    session_id: Optional[int] = None
    status: IssueSessionStatus = IssueSessionStatus.PENDING
    branch_name: str = ""
    pr_number: Optional[int] = None
    pr_url: str = ""
    attempts: int = 0
    max_attempts: int = 3
    last_error: str = ""
    verification_results: List[VerificationResult] = field(default_factory=list)
    context_files: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "github_issue_number": self.github_issue_number,
            "github_issue_title": self.github_issue_title,
            "github_issue_body": self.github_issue_body[:500] + "..." if len(self.github_issue_body) > 500 else self.github_issue_body,
            "github_issue_labels": self.github_issue_labels,
            "github_issue_url": self.github_issue_url,
            "session_id": self.session_id,
            "status": self.status.value if isinstance(self.status, IssueSessionStatus) else self.status,
            "branch_name": self.branch_name,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "verification_results": [v.to_dict() if isinstance(v, VerificationResult) else v for v in self.verification_results],
            "context_files": self.context_files,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    def to_persist_dict(self) -> dict:
        d = self.to_dict()
        d["github_issue_body"] = self.github_issue_body  # Full body for persistence
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'IssueSession':
        data = data.copy()
        if "status" in data and isinstance(data["status"], str):
            data["status"] = IssueSessionStatus(data["status"])
        if "verification_results" in data:
            data["verification_results"] = [
                VerificationResult.from_dict(v) if isinstance(v, dict) else v
                for v in data["verification_results"]
            ]
        return cls(**data)

    @classmethod
    def from_github_issue(cls, id: int, project_id: int, issue: GitHubIssue) -> 'IssueSession':
        """Create IssueSession from a GitHub issue"""
        return cls(
            id=id,
            project_id=project_id,
            github_issue_number=issue.number,
            github_issue_title=issue.title,
            github_issue_body=issue.body,
            github_issue_labels=issue.labels,
            github_issue_url=issue.html_url,
            branch_name=f"fix/issue-{issue.number}",
        )


class ProjectManager:
    """Manages projects persistence and operations"""

    def __init__(self):
        self.projects: Dict[int, Project] = {}
        self._next_id = 1
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        """Load projects from disk"""
        if PROJECTS_FILE.exists():
            try:
                with open(PROJECTS_FILE, 'r') as f:
                    data = json.load(f)
                self._next_id = data.get("next_id", 1)
                for p in data.get("projects", []):
                    project = Project.from_dict(p)
                    self.projects[project.id] = project
            except Exception as e:
                print(f"[ERROR] Failed to load projects: {e}")

    def _save(self):
        """Save projects to disk"""
        try:
            data = {
                "next_id": self._next_id,
                "projects": [p.to_persist_dict() for p in self.projects.values()]
            }
            with open(PROJECTS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save projects: {e}")

    def create(self, name: str, github_repo: str, github_token: str = "", **kwargs) -> Project:
        """Create a new project"""
        project = Project(
            id=self._next_id,
            name=name,
            github_repo=github_repo,
            **kwargs
        )
        if github_token:
            project.set_token(github_token)

        self._next_id += 1
        self.projects[project.id] = project
        self._save()
        return project

    def get(self, project_id: int) -> Optional[Project]:
        """Get a project by ID"""
        return self.projects.get(project_id)

    def get_all(self) -> List[Project]:
        """Get all projects"""
        return list(self.projects.values())

    def update(self, project_id: int, **kwargs) -> Optional[Project]:
        """Update a project"""
        project = self.projects.get(project_id)
        if not project:
            return None

        for key, value in kwargs.items():
            if key == "github_token":
                project.set_token(value)
            elif hasattr(project, key):
                setattr(project, key, value)

        self._save()
        return project

    def delete(self, project_id: int) -> bool:
        """Delete a project"""
        if project_id in self.projects:
            del self.projects[project_id]
            self._save()
            return True
        return False


class IssueSessionManager:
    """Manages issue sessions persistence and operations"""

    def __init__(self):
        self.sessions: Dict[int, IssueSession] = {}
        self._next_id = 1
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self):
        """Load issue sessions from disk"""
        if ISSUE_SESSIONS_FILE.exists():
            try:
                with open(ISSUE_SESSIONS_FILE, 'r') as f:
                    data = json.load(f)
                self._next_id = data.get("next_id", 1)
                for s in data.get("sessions", []):
                    session = IssueSession.from_dict(s)
                    self.sessions[session.id] = session
            except Exception as e:
                print(f"[ERROR] Failed to load issue sessions: {e}")

    def _save(self):
        """Save issue sessions to disk"""
        try:
            data = {
                "next_id": self._next_id,
                "sessions": [s.to_persist_dict() for s in self.sessions.values()]
            }
            with open(ISSUE_SESSIONS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save issue sessions: {e}")

    def create(self, project_id: int, issue: GitHubIssue) -> IssueSession:
        """Create a new issue session"""
        session = IssueSession.from_github_issue(
            id=self._next_id,
            project_id=project_id,
            issue=issue
        )
        self._next_id += 1
        self.sessions[session.id] = session
        self._save()
        return session

    def get(self, session_id: int) -> Optional[IssueSession]:
        """Get an issue session by ID"""
        return self.sessions.get(session_id)

    def get_by_project(self, project_id: int) -> List[IssueSession]:
        """Get all issue sessions for a project"""
        return [s for s in self.sessions.values() if s.project_id == project_id]

    def get_by_issue(self, project_id: int, issue_number: int) -> Optional[IssueSession]:
        """Get issue session by project and issue number"""
        for s in self.sessions.values():
            if s.project_id == project_id and s.github_issue_number == issue_number:
                return s
        return None

    def get_by_session_id(self, session_id: int) -> Optional[IssueSession]:
        """Get issue session by linked Autowrkers session ID"""
        for s in self.sessions.values():
            if s.session_id == session_id:
                return s
        return None

    def get_by_status(self, project_id: int, status: IssueSessionStatus) -> List[IssueSession]:
        """Get issue sessions by status"""
        return [s for s in self.sessions.values()
                if s.project_id == project_id and s.status == status]

    def get_pending(self, project_id: int) -> List[IssueSession]:
        """Get pending issue sessions for a project"""
        return self.get_by_status(project_id, IssueSessionStatus.PENDING)

    def get_in_progress(self, project_id: int) -> List[IssueSession]:
        """Get in-progress issue sessions for a project"""
        return [s for s in self.sessions.values()
                if s.project_id == project_id and s.status in (
                    IssueSessionStatus.IN_PROGRESS,
                    IssueSessionStatus.VERIFYING,
                    IssueSessionStatus.QUEUED
                )]

    def update(self, session_id: int, **kwargs) -> Optional[IssueSession]:
        """Update an issue session"""
        session = self.sessions.get(session_id)
        if not session:
            return None

        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)

        self._save()
        return session

    def add_verification_result(self, session_id: int, result: VerificationResult) -> Optional[IssueSession]:
        """Add a verification result to a session"""
        session = self.sessions.get(session_id)
        if not session:
            return None

        session.verification_results.append(result)
        self._save()
        return session

    def delete(self, session_id: int) -> bool:
        """Delete an issue session"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            self._save()
            return True
        return False


class SQLiteProjectManager:
    def __init__(self):
        from .database import db
        self._db = db
        self._cache: Dict[int, Project] = {}
        self._load_cache()
    
    def _load_cache(self):
        for data in self._db.get_all_projects():
            self._cache[data['id']] = Project.from_dict(data)
    
    def _refresh(self, project_id: int) -> Optional[Project]:
        data = self._db.get_project(project_id)
        if data:
            self._cache[project_id] = Project.from_dict(data)
            return self._cache[project_id]
        return None
    
    def create(self, name: str, github_repo: str, github_token: str = "", **kwargs: Any) -> Project:
        project_data: Dict[str, Any] = {
            'name': name,
            'github_repo': github_repo,
            'created_at': datetime.now().isoformat(),
        }
        
        for key, value in kwargs.items():
            if key == 'issue_filter' and isinstance(value, IssueFilter):
                project_data[key] = value.to_dict()
            elif key == 'status' and isinstance(value, ProjectStatus):
                project_data[key] = value.value
            else:
                project_data[key] = value
        
        if github_token:
            project_data['github_token_encrypted'] = encrypt_token(github_token)
        
        project_id = self._db.create_project(project_data)
        project = self._refresh(project_id)
        if project is None:
            raise RuntimeError(f"Failed to retrieve newly created project {project_id}")
        return project
    
    def get(self, project_id: int) -> Optional[Project]:
        if project_id in self._cache:
            return self._cache[project_id]
        return self._refresh(project_id)
    
    def get_all(self) -> List[Project]:
        return list(self._cache.values())
    
    def update(self, project_id: int, **kwargs) -> Optional[Project]:
        project = self._cache.get(project_id)
        if not project:
            return None
        
        update_data = {}
        for key, value in kwargs.items():
            if key == "github_token":
                update_data['github_token_encrypted'] = encrypt_token(value)
            elif key == "llm_api_key":
                update_data['llm_api_key_encrypted'] = encrypt_token(value)
            elif key == "issue_filter" and isinstance(value, IssueFilter):
                update_data[key] = value.to_dict()
            elif key == "status" and isinstance(value, ProjectStatus):
                update_data[key] = value.value
            else:
                update_data[key] = value
        
        self._db.update_project(project_id, update_data)
        return self._refresh(project_id)
    
    def delete(self, project_id: int) -> bool:
        result = self._db.delete_project(project_id)
        if result and project_id in self._cache:
            del self._cache[project_id]
        return result


class SQLiteIssueSessionManager:
    def __init__(self):
        from .database import db
        self._db = db
        self.sessions: Dict[int, IssueSession] = {}
        self._load_cache()
    
    def _load_cache(self):
        for data in self._db.get_all_issue_sessions():
            self.sessions[data['id']] = IssueSession.from_dict(data)
    
    def _refresh(self, session_id: int) -> Optional[IssueSession]:
        data = self._db.get_issue_session(session_id)
        if data:
            self.sessions[session_id] = IssueSession.from_dict(data)
            return self.sessions[session_id]
        return None
    
    def create(self, project_id: int, issue: GitHubIssue) -> IssueSession:
        session_data = {
            'project_id': project_id,
            'github_issue_number': issue.number,
            'github_issue_title': issue.title,
            'github_issue_body': issue.body,
            'github_issue_labels': issue.labels,
            'github_issue_url': issue.html_url,
            'branch_name': f"fix/issue-{issue.number}",
            'created_at': datetime.now().isoformat(),
        }
        session_id = self._db.create_issue_session(session_data)
        session = self._refresh(session_id)
        if session is None:
            raise RuntimeError(f"Failed to retrieve newly created issue session {session_id}")
        return session
    
    def get(self, session_id: int) -> Optional[IssueSession]:
        if session_id in self.sessions:
            return self.sessions[session_id]
        return self._refresh(session_id)
    
    def get_by_project(self, project_id: int) -> List[IssueSession]:
        return [s for s in self.sessions.values() if s.project_id == project_id]
    
    def get_by_issue(self, project_id: int, issue_number: int) -> Optional[IssueSession]:
        for s in self.sessions.values():
            if s.project_id == project_id and s.github_issue_number == issue_number:
                return s
        return None
    
    def get_by_session_id(self, session_id: int) -> Optional[IssueSession]:
        for s in self.sessions.values():
            if s.session_id == session_id:
                return s
        return None
    
    def get_by_status(self, project_id: int, status: IssueSessionStatus) -> List[IssueSession]:
        return [s for s in self.sessions.values()
                if s.project_id == project_id and s.status == status]
    
    def get_pending(self, project_id: int) -> List[IssueSession]:
        return self.get_by_status(project_id, IssueSessionStatus.PENDING)
    
    def get_in_progress(self, project_id: int) -> List[IssueSession]:
        return [s for s in self.sessions.values()
                if s.project_id == project_id and s.status in (
                    IssueSessionStatus.IN_PROGRESS,
                    IssueSessionStatus.VERIFYING,
                    IssueSessionStatus.QUEUED
                )]
    
    def update(self, session_id: int, **kwargs) -> Optional[IssueSession]:
        session = self.sessions.get(session_id)
        if not session:
            return None
        
        update_data = {}
        for key, value in kwargs.items():
            if key == "status" and isinstance(value, IssueSessionStatus):
                update_data[key] = value.value
            elif key == "verification_results":
                update_data[key] = [v.to_dict() if isinstance(v, VerificationResult) else v for v in value]
            else:
                update_data[key] = value
        
        self._db.update_issue_session(session_id, update_data)
        return self._refresh(session_id)
    
    def add_verification_result(self, session_id: int, result: VerificationResult) -> Optional[IssueSession]:
        session = self.sessions.get(session_id)
        if not session:
            return None
        
        self._db.add_verification_result(session_id, result.to_dict())
        return self._refresh(session_id)
    
    def delete(self, session_id: int) -> bool:
        result = self._db.delete_issue_session(session_id)
        if result and session_id in self.sessions:
            del self.sessions[session_id]
        return result


USE_SQLITE = os.environ.get('AUTOWRKERS_USE_SQLITE', '1') == '1'

if USE_SQLITE:
    project_manager = SQLiteProjectManager()
    issue_session_manager = SQLiteIssueSessionManager()
else:
    project_manager = ProjectManager()
    issue_session_manager = IssueSessionManager()
