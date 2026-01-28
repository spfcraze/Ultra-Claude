"""
Session Manager - Tmux-based session management for persistence
"""
import asyncio
import json
import os
import subprocess
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, List, Any, Awaitable


class SessionStatus(Enum):
    STARTING = "starting"
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    STOPPED = "stopped"
    ERROR = "error"
    QUEUED = "queued"      # Waiting for parent session to complete
    COMPLETED = "completed" # Session finished its task


# Completion signal patterns Claude types when done with task
# These must be on their own line or followed by whitespace/newline
COMPLETION_PATTERNS = [
    "/complete\n",
    "/complete ",
    "/complete\r",
    "/done\n",
    "/done ",
    "/done\r",
]


# Data directory for persistence
DATA_DIR = Path.home() / ".autowrkers"
SESSIONS_FILE = DATA_DIR / "sessions.json"


@dataclass
class Session:
    id: int
    name: str
    working_dir: str
    tmux_session: str  # tmux session name
    status: SessionStatus = SessionStatus.STARTING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_output: str = ""
    output_buffer: List[str] = field(default_factory=list)
    needs_input: bool = False
    parent_id: Optional[int] = None  # ID of parent session to wait for
    initial_prompt: Optional[str] = None  # Prompt to send when session starts
    llm_provider_type: str = "claude_code"  # claude_code, ollama, lm_studio, openrouter
    _reader_task: Optional["asyncio.Task[None]"] = field(default=None, repr=False)
    _last_line_count: int = field(default=0, repr=False)
    _llm_provider: Optional[Any] = field(default=None, repr=False)  # LLMProvider instance for local LLMs
    _llm_config: Optional[Any] = field(default=None, repr=False)  # LLMProviderConfig for local LLMs

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "working_dir": self.working_dir,
            "tmux_session": self.tmux_session,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_output": self.last_output[-500:] if self.last_output else "",
            "needs_input": self.needs_input,
            "parent_id": self.parent_id,
            "initial_prompt": self.initial_prompt,
            "llm_provider_type": self.llm_provider_type,
        }

    def to_persist_dict(self):
        """Dict for saving to disk (excludes runtime fields)"""
        return {
            "id": self.id,
            "name": self.name,
            "working_dir": self.working_dir,
            "tmux_session": self.tmux_session,
            "status": self.status.value,
            "created_at": self.created_at,
            "parent_id": self.parent_id,
            "initial_prompt": self.initial_prompt,
            "llm_provider_type": self.llm_provider_type,
        }

    def uses_claude_code(self) -> bool:
        """Check if this session uses Claude Code (tmux-based)"""
        return self.llm_provider_type == "claude_code"


class SessionManager:
    TMUX_PREFIX = "autowrkers"

    def __init__(self):
        self.sessions: dict[int, Session] = {}
        self._next_id = 1
        self._output_callbacks: list[Callable] = []
        self._status_callbacks: list[Callable] = []
        self._session_created_callbacks: list[Callable] = []
        self._completion_callbacks: list[Callable[[int], Awaitable[None]]] = []
        self._lock = threading.Lock()

        # Ensure data directory exists
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Load persisted sessions
        self._load_sessions()

    def _load_sessions(self):
        """Load sessions from disk and reconnect to existing tmux sessions"""
        if not SESSIONS_FILE.exists():
            return

        try:
            with open(SESSIONS_FILE, 'r') as f:
                data = json.load(f)

            # Get list of running tmux sessions
            running_tmux = self._get_running_tmux_sessions()

            for session_data in data.get("sessions", []):
                tmux_name = session_data.get("tmux_session")

                # Check if tmux session still exists
                if tmux_name in running_tmux:
                    # Restore saved status, default to RUNNING for active tmux sessions
                    saved_status = session_data.get("status", "running")
                    try:
                        status = SessionStatus(saved_status)
                    except ValueError:
                        status = SessionStatus.RUNNING
                    # If tmux is alive, it's at least running
                    if status in (SessionStatus.STOPPED, SessionStatus.ERROR):
                        status = SessionStatus.RUNNING

                    session = Session(
                        id=session_data["id"],
                        name=session_data["name"],
                        working_dir=session_data["working_dir"],
                        tmux_session=tmux_name,
                        status=status,
                        created_at=session_data.get("created_at", datetime.now().isoformat()),
                        parent_id=session_data.get("parent_id"),
                        initial_prompt=session_data.get("initial_prompt"),
                        llm_provider_type=session_data.get("llm_provider_type", "claude_code"),
                    )
                    self.sessions[session.id] = session

                    # Update next_id
                    if session.id >= self._next_id:
                        self._next_id = session.id + 1

                    print(f"[INFO] Reconnected to session {session.id}: {session.name} (status: {status.value})")
                else:
                    print(f"[INFO] Session {session_data['name']} tmux not found, skipping")

            self._next_id = data.get("next_id", self._next_id)

        except Exception as e:
            print(f"[ERROR] Failed to load sessions: {e}")

    def _save_sessions(self):
        """Save sessions to disk"""
        try:
            data = {
                "next_id": self._next_id,
                "sessions": [s.to_persist_dict() for s in self.sessions.values()
                            if s.status not in (SessionStatus.STOPPED, SessionStatus.ERROR)]
            }
            with open(SESSIONS_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save sessions: {e}")

    def _get_running_tmux_sessions(self) -> set:
        """Get set of running tmux session names"""
        try:
            result = subprocess.run(
                ["tmux", "list-sessions", "-F", "#{session_name}"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                return set(result.stdout.strip().split('\n')) if result.stdout.strip() else set()
        except Exception:
            pass
        return set()

    def _tmux_session_exists(self, name: str) -> bool:
        """Check if a tmux session exists"""
        result = subprocess.run(
            ["tmux", "has-session", "-t", name],
            capture_output=True
        )
        return result.returncode == 0

    def add_output_callback(self, callback: Callable):
        self._output_callbacks.append(callback)

    def add_status_callback(self, callback: Callable):
        self._status_callbacks.append(callback)

    def add_session_created_callback(self, callback: Callable):
        self._session_created_callbacks.append(callback)

    def add_completion_callback(self, callback: Callable[[int], Awaitable[None]]):
        """Register callback for when session completes via /complete signal"""
        self._completion_callbacks.append(callback)

    async def _notify_output(self, session_id: int, data: str):
        for callback in self._output_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(session_id, data)
                else:
                    callback(session_id, data)
            except Exception as e:
                print(f"Output callback error: {e}")

    async def _notify_status(self, session_id: int, status: SessionStatus):
        for callback in self._status_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(session_id, status)
                else:
                    callback(session_id, status)
            except Exception as e:
                print(f"Status callback error: {e}")

    async def _notify_session_created(self, session: 'Session'):
        for callback in self._session_created_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(session)
                else:
                    callback(session)
            except Exception as e:
                print(f"Session created callback error: {e}")

    async def _notify_completion(self, session_id: int):
        for callback in self._completion_callbacks:
            try:
                await callback(session_id)
            except Exception as e:
                print(f"Completion callback error: {e}")

    def create_session(
        self,
        name: Optional[str] = None,
        working_dir: Optional[str] = None,
        parent_id: Optional[int] = None,
        initial_prompt: Optional[str] = None,
        llm_provider_type: str = "claude_code",
        llm_config: Optional[Any] = None,
        create_dir: bool = False,
    ) -> Session:
        with self._lock:
            session_id = self._next_id
            self._next_id += 1

        if name is None:
            name = f"Claude {session_id}"

        if working_dir is None:
            cwd = os.getcwd()
            working_dir = cwd if os.path.isdir(cwd) else str(Path.home())

        # Create working directory if requested and it doesn't exist
        if not os.path.isdir(working_dir):
            if create_dir:
                os.makedirs(working_dir, exist_ok=True)
            else:
                raise ValueError(f"Working directory does not exist: {working_dir}")

        # Validate parent exists if specified
        if parent_id is not None:
            parent = self.sessions.get(parent_id)
            if not parent:
                raise ValueError(f"Parent session {parent_id} does not exist")

        tmux_session = f"{self.TMUX_PREFIX}-{session_id}"

        # Determine initial status
        if parent_id is not None:
            parent = self.sessions.get(parent_id)
            if parent is not None and parent.status in (SessionStatus.STARTING, SessionStatus.RUNNING, SessionStatus.NEEDS_ATTENTION):
                initial_status = SessionStatus.QUEUED
            else:
                initial_status = SessionStatus.STARTING
        else:
            initial_status = SessionStatus.STARTING

        # Determine provider type from config if provided
        if llm_config is not None:
            llm_provider_type = llm_config.provider_type.value

        session = Session(
            id=session_id,
            name=name,
            working_dir=working_dir,
            tmux_session=tmux_session,
            status=initial_status,
            parent_id=parent_id,
            initial_prompt=initial_prompt,
            llm_provider_type=llm_provider_type,
        )

        # Store LLM config for later use in start_session
        if llm_config is not None:
            session._llm_config = llm_config

        self.sessions[session_id] = session
        return session

    async def start_session(self, session: Session, auto_trust: bool = True) -> bool:
        """Start a session (Claude Code via tmux or local LLM via API)"""
        # If session is queued (waiting for parent), don't start yet
        if session.status == SessionStatus.QUEUED:
            print(f"[INFO] Session {session.id} is queued, waiting for parent {session.parent_id}")
            self._save_sessions()
            await self._notify_session_created(session)
            await self._notify_status(session.id, session.status)
            return True  # Successfully queued

        # Dispatch based on provider type
        if session.uses_claude_code():
            return await self._start_claude_code_session(session, auto_trust)
        else:
            return await self._start_local_llm_session(session)

    async def _start_claude_code_session(self, session: Session, auto_trust: bool = True) -> bool:
        """Start a Claude Code process in a tmux session (original implementation)"""
        try:
            # Create tmux session with claude command
            # Using -x and -y to set initial size
            result = subprocess.run(
                [
                    "tmux", "new-session",
                    "-d",  # detached
                    "-s", session.tmux_session,  # session name
                    "-c", session.working_dir,  # working directory
                    "-x", "120",  # width
                    "-y", "40",   # height
                    "claude"  # command to run
                ],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                print(f"[ERROR] Failed to create tmux session: {result.stderr}")
                session.status = SessionStatus.ERROR
                session.last_output = f"Failed to create tmux session: {result.stderr}"
                await self._notify_status(session.id, session.status)
                return False

            session.status = SessionStatus.RUNNING

            # Auto-accept trust prompt if enabled
            if auto_trust:
                # Wait for Claude to start and show trust prompt
                await asyncio.sleep(3)
                # Send "1" to select "Yes, proceed" then Enter
                subprocess.run(
                    ["tmux", "send-keys", "-t", session.tmux_session, "1"],
                    capture_output=True
                )
                await asyncio.sleep(0.2)
                subprocess.run(
                    ["tmux", "send-keys", "-t", session.tmux_session, "Enter"],
                    capture_output=True
                )

            # Send initial prompt if provided
            if session.initial_prompt:
                await asyncio.sleep(5)  # Wait for Claude to fully initialize after trust
                success = await self.send_input(session.id, session.initial_prompt + '\r')
                print(f"[INFO] Sent initial prompt to session {session.id}: success={success}")

                # Extra Enter to ensure prompt is submitted (sometimes needed)
                await asyncio.sleep(0.5)
                subprocess.run(
                    ["tmux", "send-keys", "-t", session.tmux_session, "Enter"],
                    capture_output=True
                )
                print(f"[INFO] Sent extra Enter to session {session.id}")

            # Start output reader
            session._reader_task = asyncio.create_task(self._read_output(session))

            # Save sessions to disk
            self._save_sessions()

            # Notify about new session and status
            await self._notify_session_created(session)
            await self._notify_status(session.id, session.status)

            return True

        except Exception as e:
            print(f"Start session error: {e}")
            session.status = SessionStatus.ERROR
            session.last_output = str(e)
            await self._notify_status(session.id, session.status)
            return False

    async def _start_local_llm_session(self, session: Session) -> bool:
        """Start a local LLM session using the agentic runner"""
        try:
            from .llm_provider import get_provider, LLMProviderStatus

            # Get config from session (stored during create_session)
            llm_config = getattr(session, '_llm_config', None)
            if not llm_config:
                print(f"[ERROR] No LLM config for session {session.id}")
                session.status = SessionStatus.ERROR
                session.last_output = "No LLM configuration provided"
                await self._notify_status(session.id, session.status)
                return False

            # Create provider instance
            provider = get_provider(llm_config, session.working_dir)
            session._llm_provider = provider

            # Set up callbacks for output and status
            async def output_callback(text: str):
                session.last_output = text
                session.output_buffer = [text]
                await self._notify_output(session.id, text)

            async def status_callback(status: LLMProviderStatus):
                # Map LLM provider status to session status
                status_map = {
                    LLMProviderStatus.STARTING: SessionStatus.STARTING,
                    LLMProviderStatus.RUNNING: SessionStatus.RUNNING,
                    LLMProviderStatus.PROCESSING: SessionStatus.RUNNING,
                    LLMProviderStatus.WAITING_INPUT: SessionStatus.NEEDS_ATTENTION,
                    LLMProviderStatus.COMPLETED: SessionStatus.COMPLETED,
                    LLMProviderStatus.ERROR: SessionStatus.ERROR,
                    LLMProviderStatus.STOPPED: SessionStatus.STOPPED,
                }
                new_status = status_map.get(status, SessionStatus.RUNNING)
                if session.status != new_status:
                    session.status = new_status
                    session.needs_input = (status == LLMProviderStatus.WAITING_INPUT)
                    await self._notify_status(session.id, session.status)

            provider.set_output_callback(output_callback)
            provider.set_status_callback(status_callback)

            # Start the provider
            success = await provider.start(session.initial_prompt)

            if success:
                session.status = SessionStatus.RUNNING
                print(f"[INFO] Started local LLM session {session.id} with provider {session.llm_provider_type}")
            else:
                session.status = SessionStatus.ERROR
                session.last_output = "Failed to start local LLM session"

            # Save sessions to disk
            self._save_sessions()

            # Notify about new session and status
            await self._notify_session_created(session)
            await self._notify_status(session.id, session.status)

            return success

        except ImportError as e:
            print(f"[ERROR] Failed to import LLM provider: {e}")
            session.status = SessionStatus.ERROR
            session.last_output = f"LLM provider not available: {e}"
            await self._notify_status(session.id, session.status)
            return False
        except Exception as e:
            print(f"[ERROR] Start local LLM session error: {e}")
            session.status = SessionStatus.ERROR
            session.last_output = str(e)
            await self._notify_status(session.id, session.status)
            return False

    async def start_output_readers(self):
        """Start output readers for all existing sessions (called on server start)"""
        for session in self.sessions.values():
            if session.status == SessionStatus.RUNNING and session._reader_task is None:
                session._reader_task = asyncio.create_task(self._read_output(session))
                print(f"[INFO] Started output reader for session {session.id}")

    async def mark_session_completed(self, session_id: int) -> bool:
        """Mark a session as completed and start any queued children"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        session.status = SessionStatus.COMPLETED
        await self._notify_status(session.id, session.status)
        self._save_sessions()

        print(f"[INFO] Session {session_id} marked as completed")

        # Find and start any queued children
        await self._start_queued_children(session_id)

        return True

    async def _start_queued_children(self, parent_id: int):
        """Start any sessions that were waiting for the given parent"""
        for session in self.sessions.values():
            if session.parent_id == parent_id and session.status == SessionStatus.QUEUED:
                print(f"[INFO] Starting queued child session {session.id} (parent {parent_id} completed)")
                session.status = SessionStatus.STARTING
                await self.start_session(session)

    def get_queued_sessions(self, parent_id: Optional[int] = None) -> List[Session]:
        """Get all queued sessions, optionally filtered by parent"""
        sessions = [s for s in self.sessions.values() if s.status == SessionStatus.QUEUED]
        if parent_id is not None:
            sessions = [s for s in sessions if s.parent_id == parent_id]
        return sessions

    async def _read_output(self, session: Session):
        """Read output from tmux session using capture-pane"""
        input_indicators = [
            "❯ ",           # Claude prompt
            "› ",           # Alternative prompt
            "(y/n)",        # Yes/no prompts
            "[Y/n]",        # Default yes prompts
            "[y/N]",        # Default no prompts
            "Press Enter",  # Continue prompts
            "Enter to confirm",
            "Would you like",
            "Do you want to",
            "? for shortcuts",  # Claude help indicator
        ]

        last_content = ""

        while session.status in (SessionStatus.RUNNING, SessionStatus.NEEDS_ATTENTION):
            try:
                # Check if tmux session still exists
                if not self._tmux_session_exists(session.tmux_session):
                    print(f"[INFO] Tmux session {session.tmux_session} no longer exists")
                    session.status = SessionStatus.STOPPED
                    break

                # Capture pane content
                result = subprocess.run(
                    ["tmux", "capture-pane", "-t", session.tmux_session, "-p", "-S", "-500"],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    content = result.stdout

                    # Check if content changed
                    if content != last_content:
                        # For terminal apps like Claude that redraw the screen,
                        # just send the full current content as the update
                        # The frontend will replace/refresh the display
                        new_content = content

                        if new_content.strip():
                            session.last_output = new_content[-500:]  # Keep last 500 chars for preview
                            session.output_buffer = [content]  # Store full screen content

                            # Keep buffer manageable
                            if len(session.output_buffer) > 1000:
                                session.output_buffer = session.output_buffer[-500:]

                            # Check for completion signal from Claude
                            recent_content = content[-1000:]
                            if any(pattern in recent_content for pattern in COMPLETION_PATTERNS):
                                print(f"[INFO] Completion signal detected in session {session.id}")
                                await self.mark_session_completed(session.id)
                                await self._notify_completion(session.id)
                                break

                            # Check for input indicators
                            old_needs_input = session.needs_input
                            session.needs_input = any(ind in content[-500:] for ind in input_indicators)

                            if session.needs_input and not old_needs_input:
                                session.status = SessionStatus.NEEDS_ATTENTION
                                await self._notify_status(session.id, session.status)
                            elif not session.needs_input and old_needs_input:
                                if session.status == SessionStatus.NEEDS_ATTENTION:
                                    session.status = SessionStatus.RUNNING
                                    await self._notify_status(session.id, session.status)

                            await self._notify_output(session.id, new_content)

                        last_content = content

                await asyncio.sleep(0.3)  # Poll interval

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Read error for session {session.id}: {e}")
                await asyncio.sleep(1)

        # Only mark as stopped if the tmux session is actually gone.
        # If tmux is still alive, the server is just shutting down — keep
        # the session as RUNNING so it persists and reconnects on restart.
        if session.status not in (SessionStatus.STOPPED, SessionStatus.ERROR):
            if not self._tmux_session_exists(session.tmux_session):
                session.status = SessionStatus.STOPPED
                await self._notify_status(session.id, session.status)

        self._save_sessions()

    async def send_input(self, session_id: int, data: str) -> bool:
        """Send input to a session (tmux for Claude Code, API for local LLM)"""
        session = self.sessions.get(session_id)
        if not session:
            print(f"[DEBUG] send_input failed: session not found")
            return False

        # Handle local LLM sessions
        if not session.uses_claude_code():
            return await self._send_input_local_llm(session, data)

        # Handle Claude Code (tmux) sessions
        if not self._tmux_session_exists(session.tmux_session):
            print(f"[DEBUG] send_input failed: tmux session doesn't exist")
            return False

        try:
            # For tmux, we need to handle the input differently
            # If data ends with \r, we send it as Enter
            if data.endswith('\r'):
                text = data[:-1]
                if text:
                    # Send the text first
                    subprocess.run(
                        ["tmux", "send-keys", "-t", session.tmux_session, "-l", text],
                        capture_output=True
                    )
                # Then send Enter
                subprocess.run(
                    ["tmux", "send-keys", "-t", session.tmux_session, "Enter"],
                    capture_output=True
                )
            else:
                # Send literal text
                subprocess.run(
                    ["tmux", "send-keys", "-t", session.tmux_session, "-l", data],
                    capture_output=True
                )

            print(f"[DEBUG] Sent input to session {session_id}: {repr(data)}")

            session.needs_input = False
            if session.status == SessionStatus.NEEDS_ATTENTION:
                session.status = SessionStatus.RUNNING
                await self._notify_status(session.id, session.status)

            return True

        except Exception as e:
            print(f"Send error for session {session_id}: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _send_input_local_llm(self, session: Session, data: str) -> bool:
        """Send input to a local LLM session"""
        if not session._llm_provider:
            print(f"[DEBUG] send_input failed: no LLM provider for session {session.id}")
            return False

        try:
            # Strip trailing carriage return if present
            text = data.rstrip('\r\n')
            success = await session._llm_provider.send_input(text)

            if success:
                session.needs_input = False
                if session.status == SessionStatus.NEEDS_ATTENTION:
                    session.status = SessionStatus.RUNNING
                    await self._notify_status(session.id, session.status)

            return success

        except Exception as e:
            print(f"Send error for local LLM session {session.id}: {e}")
            return False

    async def stop_session(self, session_id: int) -> bool:
        """Stop a session (tmux for Claude Code, API for local LLM)"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        try:
            # Cancel reader task (for Claude Code sessions)
            if session._reader_task:
                session._reader_task.cancel()
                try:
                    await session._reader_task
                except asyncio.CancelledError:
                    pass

            # Stop based on provider type
            if session.uses_claude_code():
                # Kill tmux session
                if self._tmux_session_exists(session.tmux_session):
                    subprocess.run(
                        ["tmux", "kill-session", "-t", session.tmux_session],
                        capture_output=True
                    )
            else:
                # Stop local LLM provider
                if session._llm_provider:
                    await session._llm_provider.stop()

            session.status = SessionStatus.STOPPED
            await self._notify_status(session.id, session.status)

            # Save sessions
            self._save_sessions()

            return True

        except Exception as e:
            print(f"Stop error for session {session_id}: {e}")
            return False

    async def remove_session(self, session_id: int) -> bool:
        """Remove a session completely (stop it first if running, then delete from storage)"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        try:
            # Stop the session first if it's running
            if session.status in (SessionStatus.RUNNING, SessionStatus.STARTING, SessionStatus.NEEDS_ATTENTION):
                await self.stop_session(session_id)

            # Remove from sessions dict
            del self.sessions[session_id]

            # Remove from output buffers if tracked
            if hasattr(self, 'output_buffers') and session_id in self.output_buffers:
                del self.output_buffers[session_id]

            # Save updated sessions to storage
            self._save_sessions()

            print(f"[INFO] Removed session {session_id}")
            return True

        except Exception as e:
            print(f"Remove error for session {session_id}: {e}")
            return False

    def get_session(self, session_id: int) -> Optional[Session]:
        return self.sessions.get(session_id)

    def get_all_sessions(self) -> list[Session]:
        return list(self.sessions.values())

    def get_sessions_needing_attention(self) -> list[Session]:
        return [s for s in self.sessions.values() if s.status == SessionStatus.NEEDS_ATTENTION]

    async def update_session_parent(self, session_id: int, parent_id: Optional[int] = None) -> bool:
        """Update a session's parent, used for Kanban drag & drop"""
        session = self.sessions.get(session_id)
        if not session:
            return False

        # Can't set a session as its own parent
        if parent_id == session_id:
            return False

        # Validate parent exists if specified
        if parent_id is not None:
            parent = self.sessions.get(parent_id)
            if not parent:
                return False
            # Prevent circular dependency
            if parent.parent_id == session_id:
                return False

        old_parent_id = session.parent_id
        session.parent_id = parent_id
        self._save_sessions()

        print(f"[INFO] Updated session {session_id} parent: {old_parent_id} -> {parent_id}")
        await self._notify_status(session.id, session.status)
        return True

    def get_session_output(self, session_id: int) -> str:
        """Get full output from tmux session"""
        session = self.sessions.get(session_id)
        if not session:
            return ""

        if not self._tmux_session_exists(session.tmux_session):
            return "".join(session.output_buffer)

        try:
            # Capture full scrollback
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", session.tmux_session, "-p", "-S", "-"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return result.stdout
        except Exception as e:
            print(f"Error getting session output: {e}")

        return "".join(session.output_buffer)


# Global instance
manager = SessionManager()
