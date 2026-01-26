"""
Main Telegram bot for UltraClaude remote control.

Provides a command interface to manage sessions, trigger automation,
and monitor progress via Telegram.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

from .models import TelegramBotConfig
from .commands import (
    COMMANDS,
    format_help_text,
    format_session_status,
    format_session_list,
    format_project_list,
    format_issue_list,
    format_system_status,
    truncate_output,
)

logger = logging.getLogger("ultraclaude.telegram")

# Config persistence path
CONFIG_DIR = Path.home() / ".ultraclaude"
CONFIG_FILE = CONFIG_DIR / "telegram_bot.json"


class TelegramBot:
    """Telegram bot for remote control of UltraClaude."""

    def __init__(self):
        self._app = None  # python-telegram-bot Application
        self._running = False
        self._allowed_users: Set[int] = set()
        self._chat_subscriptions: Dict[int, Set[str]] = {}  # chat_id -> subscribed project IDs
        self._bot_token: str = ""
        self._config: TelegramBotConfig = TelegramBotConfig()
        self._bot_username: str = ""
        self._started_at: Optional[datetime] = None
        self._polling_task: Optional[asyncio.Task] = None

        # Load saved config
        self._load_config()

    def _load_config(self):
        """Load config from disk."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                self._config = TelegramBotConfig.from_dict(data.get("config", {}))
                self._chat_subscriptions = {
                    int(k): set(v) for k, v in data.get("subscriptions", {}).items()
                }
                # Decrypt token if needed
                try:
                    from ..crypto import decrypt_or_return
                    if self._config.bot_token:
                        self._config.bot_token = decrypt_or_return(self._config.bot_token)
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Failed to load telegram config: {e}")

    def _save_config(self):
        """Save config to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            # Encrypt token before saving
            token_to_save = self._config.bot_token
            try:
                from ..crypto import encrypt_if_needed
                if token_to_save:
                    token_to_save = encrypt_if_needed(token_to_save)
            except Exception:
                pass

            config_data = self._config.to_dict()
            config_data["bot_token"] = token_to_save

            data = {
                "config": config_data,
                "subscriptions": {
                    str(k): list(v) for k, v in self._chat_subscriptions.items()
                },
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save telegram config: {e}")

    async def start(self, token: Optional[str] = None, allowed_users: Optional[list] = None):
        """Start the bot with long-polling."""
        if self._running:
            logger.warning("Telegram bot is already running")
            return

        try:
            from telegram import Update, BotCommand as TGBotCommand
            from telegram.ext import (
                Application,
                CommandHandler,
                ContextTypes,
            )
        except ImportError:
            logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot==22.1")
            return

        if token:
            self._config.bot_token = token
        if allowed_users is not None:
            self._config.allowed_user_ids = allowed_users

        if not self._config.bot_token:
            logger.error("No bot token configured")
            return

        self._allowed_users = set(self._config.allowed_user_ids)
        self._bot_token = self._config.bot_token

        # Save config
        self._config.enabled = True
        self._save_config()

        # Build Application
        self._app = Application.builder().token(self._bot_token).build()
        self._register_handlers()

        # Initialize and start
        await self._app.initialize()
        await self._app.start()

        # Set bot commands in Telegram
        try:
            tg_commands = [
                TGBotCommand(cmd.command, cmd.description) for cmd in COMMANDS
            ]
            await self._app.bot.set_my_commands(tg_commands)
            me = await self._app.bot.get_me()
            self._bot_username = me.username or ""
            logger.info(f"Telegram bot started as @{self._bot_username}")
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}")

        # Start polling in background
        self._running = True
        self._started_at = datetime.now()

        # Start updater polling
        await self._app.updater.start_polling(drop_pending_updates=True)

        logger.info("Telegram bot polling started")

    async def stop(self):
        """Stop the bot gracefully."""
        if not self._running:
            return

        self._running = False
        self._config.enabled = False
        self._save_config()

        try:
            if self._app:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
                self._app = None
        except Exception as e:
            logger.error(f"Error stopping telegram bot: {e}")

        self._bot_username = ""
        self._started_at = None
        logger.info("Telegram bot stopped")

    def _register_handlers(self):
        """Register all command and message handlers."""
        from telegram.ext import CommandHandler

        handlers = [
            ("start", self._cmd_start),
            ("help", self._cmd_help),
            ("status", self._cmd_status),
            ("sessions", self._cmd_sessions),
            ("session", self._cmd_session),
            ("send", self._cmd_send),
            ("create", self._cmd_create),
            ("stop", self._cmd_stop_session),
            ("output", self._cmd_output),
            ("projects", self._cmd_projects),
            ("issues", self._cmd_issues),
            ("startissue", self._cmd_startissue),
            ("subscribe", self._cmd_subscribe),
            ("unsubscribe", self._cmd_unsubscribe),
        ]

        for command, handler in handlers:
            self._app.add_handler(CommandHandler(command, handler))

    # --- Auth ---
    def _check_auth(self, user_id: int) -> bool:
        """Check if user is in allowlist."""
        if not self._allowed_users:
            return False
        return user_id in self._allowed_users

    # --- Command Handlers ---

    async def _cmd_start(self, update, context):
        """Handle /start command."""
        user_id = update.effective_user.id
        if not self._check_auth(user_id):
            await self._reply(
                update,
                "Not authorized. Your Telegram user ID is "
                f"<code>{user_id}</code>.\n\n"
                "Add this ID to the allowed users list in UltraClaude Settings "
                "to connect this bot.",
            )
            return

        await self._reply(
            update,
            f"Welcome to <b>UltraClaude</b>!\n\n"
            f"You are authorized (ID: <code>{user_id}</code>).\n\n"
            f"Use /help to see available commands.",
        )

    async def _cmd_help(self, update, context):
        """Handle /help command."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        await self._reply(update, format_help_text())

    async def _cmd_status(self, update, context):
        """Handle /status command - system overview."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        try:
            from ..session_manager import manager
            from ..models import project_manager

            sessions = manager.get_all_sessions()
            session_dicts = [s.to_dict() for s in sessions]
            projects = project_manager.get_all()

            msg = format_system_status(session_dicts, projects)
            await self._reply(update, msg)
        except Exception as e:
            await self._reply(update, f"Error fetching status: {e}")

    async def _cmd_sessions(self, update, context):
        """Handle /sessions command - list active sessions."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        try:
            from ..session_manager import manager

            sessions = manager.get_all_sessions()
            msg = format_session_list(sessions)
            await self._reply(update, msg)
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_session(self, update, context):
        """Handle /session <id> command - session details."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /session &lt;id&gt;")
            return

        try:
            session_id = int(args[0])
            from ..session_manager import manager

            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return

            msg = format_session_status(session.to_dict())
            await self._reply(update, msg)
        except ValueError:
            await self._reply(update, "Invalid session ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_send(self, update, context):
        """Handle /send <id> <text> command - send input to session."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args or len(args) < 2:
            await self._reply(update, "Usage: /send &lt;id&gt; &lt;text&gt;")
            return

        try:
            session_id = int(args[0])
            text = " ".join(args[1:])

            from ..session_manager import manager

            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return

            success = await manager.send_input(session_id, text + "\r")
            if success:
                await self._reply(update, f"Sent to session #{session_id}.")
            else:
                await self._reply(update, f"Failed to send to session #{session_id}.")
        except ValueError:
            await self._reply(update, "Invalid session ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_create(self, update, context):
        """Handle /create <name> command - create new session."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        name = " ".join(args) if args else None

        try:
            from ..session_manager import manager

            session = manager.create_session(name=name)
            success = await manager.start_session(session)
            if success:
                await self._reply(
                    update,
                    f"Session #{session.id} '{session.name}' created and started.",
                )
            else:
                await self._reply(
                    update,
                    f"Session #{session.id} created but failed to start.",
                )
        except Exception as e:
            await self._reply(update, f"Error creating session: {e}")

    async def _cmd_stop_session(self, update, context):
        """Handle /stop <id> command - stop a session."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /stop &lt;id&gt;")
            return

        try:
            session_id = int(args[0])
            from ..session_manager import manager

            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return

            success = await manager.stop_session(session_id)
            if success:
                await self._reply(update, f"Session #{session_id} stopped.")
            else:
                await self._reply(update, f"Failed to stop session #{session_id}.")
        except ValueError:
            await self._reply(update, "Invalid session ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_output(self, update, context):
        """Handle /output <id> command - get recent session output."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /output &lt;id&gt;")
            return

        try:
            session_id = int(args[0])
            from ..session_manager import manager
            import html as html_mod

            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return

            output = manager.get_session_output(session_id)
            truncated = truncate_output(output, self._config.output_max_lines)
            msg = (
                f"<b>Output for session #{session_id}</b>\n\n"
                f"<pre>{html_mod.escape(truncated)}</pre>"
            )
            # Telegram message limit is 4096 chars
            if len(msg) > 4000:
                msg = msg[:3990] + "...</pre>"
            await self._reply(update, msg)
        except ValueError:
            await self._reply(update, "Invalid session ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_projects(self, update, context):
        """Handle /projects command - list projects."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        try:
            from ..models import project_manager

            projects = project_manager.get_all()
            msg = format_project_list(projects)
            await self._reply(update, msg)
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_issues(self, update, context):
        """Handle /issues <project_id> command - list issues for a project."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /issues &lt;project_id&gt;")
            return

        try:
            project_id = int(args[0])
            from ..models import issue_session_manager

            issues = issue_session_manager.get_by_project(project_id)
            msg = format_issue_list(issues)
            await self._reply(update, msg)
        except ValueError:
            await self._reply(update, "Invalid project ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_startissue(self, update, context):
        """Handle /startissue <issue_session_id> command - start working on an issue."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /startissue &lt;issue_session_id&gt;")
            return

        try:
            issue_session_id = int(args[0])
            from ..models import issue_session_manager, project_manager
            from ..automation import automation_controller

            issue_session = issue_session_manager.get(issue_session_id)
            if not issue_session:
                await self._reply(update, f"Issue session #{issue_session_id} not found.")
                return

            project = project_manager.get(issue_session.project_id)
            if not project:
                await self._reply(update, "Project not found for this issue session.")
                return

            await automation_controller.start_issue_session(project, issue_session)
            await self._reply(
                update,
                f"Started issue session #{issue_session_id} "
                f"(Issue #{issue_session.github_issue_number}: "
                f"{issue_session.github_issue_title[:50]})",
            )
        except ValueError:
            await self._reply(update, "Invalid issue session ID. Must be a number.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_subscribe(self, update, context):
        """Handle /subscribe <project_id> command."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /subscribe &lt;project_id&gt;")
            return

        try:
            project_id = args[0]
            chat_id = update.effective_chat.id

            if chat_id not in self._chat_subscriptions:
                self._chat_subscriptions[chat_id] = set()

            self._chat_subscriptions[chat_id].add(project_id)
            self._save_config()

            await self._reply(update, f"Subscribed to events for project #{project_id}.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_unsubscribe(self, update, context):
        """Handle /unsubscribe <project_id> command."""
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return

        args = context.args
        if not args:
            await self._reply(update, "Usage: /unsubscribe &lt;project_id&gt;")
            return

        try:
            project_id = args[0]
            chat_id = update.effective_chat.id

            if chat_id in self._chat_subscriptions:
                self._chat_subscriptions[chat_id].discard(project_id)
                self._save_config()

            await self._reply(update, f"Unsubscribed from project #{project_id}.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    # --- Event Relay ---

    async def _on_session_status(self, session_id: int, status):
        """Relay session status changes to subscribed chats."""
        if not self._running or not self._config.push_session_status:
            return

        status_val = status.value if hasattr(status, "value") else str(status)

        # Only notify for significant status changes
        if status_val not in ("needs_attention", "error", "completed", "stopped"):
            return

        try:
            from ..session_manager import manager

            session = manager.get_session(session_id)
            if not session:
                return

            status_icons = {
                "needs_attention": "[!]",
                "error": "[ERR]",
                "completed": "[OK]",
                "stopped": "[X]",
            }
            icon = status_icons.get(status_val, "")
            msg = f"{icon} Session #{session_id} '{session.name}': {status_val}"
            await self._broadcast_to_all(msg)
        except Exception as e:
            logger.error(f"Error relaying session status: {e}")

    async def _on_session_complete(self, session_id: int):
        """Notify subscribed chats when session completes."""
        if not self._running or not self._config.push_session_status:
            return

        try:
            from ..session_manager import manager

            session = manager.get_session(session_id)
            name = session.name if session else f"#{session_id}"
            msg = f"[OK] Session #{session_id} '{name}' completed - ready for verification"
            await self._broadcast_to_all(msg)
        except Exception as e:
            logger.error(f"Error relaying session completion: {e}")

    async def _on_automation_event(self, event_type: str, data: dict):
        """Relay automation events (issue started, PR created, etc.)."""
        if not self._running or not self._config.push_automation_events:
            return

        project_id = str(data.get("project_id", ""))
        issue_num = data.get("issue_number", "?")
        issue_title = data.get("issue_title", "")

        messages = {
            "issue_started": f"Started working on issue #{issue_num}: {issue_title}",
            "verification_started": f"Verifying issue #{issue_num}...",
            "verification_passed": f"[OK] Issue #{issue_num} passed verification",
            "verification_failed": f"[!] Issue #{issue_num} failed verification"
                + (f" ({data.get('error', '')})" if data.get("error") else ""),
            "pr_created": f"[PR] PR #{data.get('pr_number', '?')} created for issue #{issue_num}",
            "issue_failed": f"[ERR] Issue #{issue_num} failed: {data.get('error', 'unknown')}",
            "issue_completed": f"[OK] Issue #{issue_num} completed",
        }

        msg = messages.get(event_type)
        if not msg:
            return

        # Broadcast to subscribers of this project, or all if no project
        if project_id:
            await self._broadcast_to_subscribers(project_id, msg)
        else:
            await self._broadcast_to_all(msg)

    # --- Helpers ---

    async def _reply(self, update, text: str, parse_mode: str = "HTML"):
        """Send reply with error handling."""
        try:
            # Telegram limits messages to 4096 chars
            if len(text) > 4096:
                text = text[:4090] + "\n..."
            await update.message.reply_text(text, parse_mode=parse_mode)
        except Exception as e:
            logger.error(f"Failed to send reply: {e}")
            try:
                await update.message.reply_text(f"Error formatting response: {e}")
            except Exception:
                pass

    async def _broadcast_to_subscribers(self, project_id: str, message: str):
        """Send message to all chats subscribed to a project."""
        if not self._app or not self._running:
            return

        for chat_id, subs in self._chat_subscriptions.items():
            if project_id in subs:
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    logger.error(f"Failed to send to chat {chat_id}: {e}")

    async def _broadcast_to_all(self, message: str):
        """Send message to all subscribed chats (any subscription)."""
        if not self._app or not self._running:
            return

        # Send to all chats that have any subscription
        sent_chats = set()
        for chat_id in self._chat_subscriptions:
            if chat_id not in sent_chats:
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                    )
                    sent_chats.add(chat_id)
                except Exception as e:
                    logger.error(f"Failed to broadcast to chat {chat_id}: {e}")

    def get_status(self) -> dict:
        """Get bot status for API."""
        return {
            "running": self._running,
            "username": self._bot_username,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "allowed_users": len(self._allowed_users),
            "subscribed_chats": len(self._chat_subscriptions),
        }

    def get_config(self) -> TelegramBotConfig:
        """Get current config."""
        return self._config

    def update_config(self, config_data: dict):
        """Update config from dict."""
        if "bot_token" in config_data:
            self._config.bot_token = config_data["bot_token"]
        if "allowed_user_ids" in config_data:
            self._config.allowed_user_ids = config_data["allowed_user_ids"]
            self._allowed_users = set(self._config.allowed_user_ids)
        if "push_session_status" in config_data:
            self._config.push_session_status = config_data["push_session_status"]
        if "push_automation_events" in config_data:
            self._config.push_automation_events = config_data["push_automation_events"]
        if "push_session_output" in config_data:
            self._config.push_session_output = config_data["push_session_output"]
        if "output_max_lines" in config_data:
            self._config.output_max_lines = config_data["output_max_lines"]

        self._save_config()


# Global singleton
telegram_bot = TelegramBot()
