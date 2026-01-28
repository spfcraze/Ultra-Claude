"""
Main Telegram bot for Autowrkers remote control.

Provides a command interface to manage sessions, trigger automation,
and monitor progress via Telegram. Inline keyboard buttons for mobile UX.
"""
import asyncio
import html as html_mod
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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

logger = logging.getLogger("autowrkers.telegram")

# Config persistence path
CONFIG_DIR = Path.home() / ".autowrkers"
CONFIG_FILE = CONFIG_DIR / "telegram_bot.json"

# ANSI escape code stripper
_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


class TelegramBot:
    """Telegram bot for remote control of Autowrkers."""

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
        self._focused_sessions: Dict[int, int] = {}  # chat_id -> session_id

        # Load saved config
        self._load_config()

    # ─── Config persistence ────────────────────────────────────────────

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

    # ─── Lifecycle ─────────────────────────────────────────────────────

    async def start(self, token: Optional[str] = None, allowed_users: Optional[list] = None):
        """Start the bot with long-polling."""
        if self._running:
            raise RuntimeError("Telegram bot is already running")

        if token:
            self._config.bot_token = token
        if allowed_users is not None:
            self._config.allowed_user_ids = allowed_users

        if not self._config.bot_token:
            raise ValueError("No bot token configured. Please enter a bot token and save configuration first.")

        try:
            from telegram import Update, BotCommand as TGBotCommand
            from telegram.ext import Application, CommandHandler, ContextTypes
        except ImportError:
            raise RuntimeError("python-telegram-bot not installed. Run: pip install python-telegram-bot==22.1")

        self._allowed_users = set(self._config.allowed_user_ids)
        self._bot_token = self._config.bot_token

        self._config.enabled = True
        self._save_config()

        self._app = Application.builder().token(self._bot_token).build()
        self._register_handlers()

        await self._app.initialize()
        await self._app.start()

        try:
            tg_commands = [TGBotCommand(cmd.command, cmd.description) for cmd in COMMANDS]
            await self._app.bot.set_my_commands(tg_commands)
            me = await self._app.bot.get_me()
            self._bot_username = me.username or ""
            logger.info(f"Telegram bot started as @{self._bot_username}")
        except Exception as e:
            logger.error(f"Failed to set bot commands: {e}")

        self._running = True
        self._started_at = datetime.now()
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

    # ─── Handler registration ──────────────────────────────────────────

    def _register_handlers(self):
        """Register all command, message, and callback handlers."""
        from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, filters

        commands = [
            ("start", self._cmd_start),
            ("help", self._cmd_help),
            ("status", self._cmd_status),
            ("sessions", self._cmd_sessions),
            ("session", self._cmd_session),
            ("send", self._cmd_send),
            ("focus", self._cmd_focus),
            ("unfocus", self._cmd_unfocus),
            ("create", self._cmd_create),
            ("stop", self._cmd_stop_session),
            ("output", self._cmd_output),
            ("projects", self._cmd_projects),
            ("issues", self._cmd_issues),
            ("startissue", self._cmd_startissue),
            ("subscribe", self._cmd_subscribe),
            ("unsubscribe", self._cmd_unsubscribe),
        ]
        for command, handler in commands:
            self._app.add_handler(CommandHandler(command, handler))

        # Inline button callbacks
        self._app.add_handler(CallbackQueryHandler(self._handle_callback))

        # Plain text fallback
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text))

    # ─── Auth ──────────────────────────────────────────────────────────

    def _check_auth(self, user_id: int) -> bool:
        if not self._allowed_users:
            return False
        return user_id in self._allowed_users

    # ─── Keyboard builders ─────────────────────────────────────────────

    def _kb(self, rows: List[List[Tuple[str, str]]]):
        """Build InlineKeyboardMarkup from rows of (label, callback_data) tuples."""
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in rows
        ])

    def _main_menu_kb(self):
        """Main menu keyboard shown on /start, /help, unfocused text."""
        return self._kb([
            [("Sessions", "sl"), ("Status", "ss")],
            [("Projects", "pj"), ("Create Session", "cr")],
        ])

    def _session_actions_kb(self, session_id: int, include_focus: bool = True):
        """Action buttons for a specific session."""
        row1 = []
        if include_focus:
            row1.append(("Focus", f"f:{session_id}"))
        row1.append(("Output", f"o:{session_id}"))
        row1.append(("Details", f"s:{session_id}"))
        row2 = [("Stop", f"st:{session_id}"), ("Sessions", "sl")]
        return self._kb([row1, row2])

    def _focused_kb(self, session_id: int):
        """Quick actions while focused on a session."""
        return self._kb([
            [("Output", f"o:{session_id}"), ("Details", f"s:{session_id}"), ("Unfocus", "uf")],
            [("Sessions", "sl"), ("Status", "ss")],
        ])

    def _permission_kb(self, session_id: int, options: List[Tuple[str, str]]):
        """Buttons for a Claude Code permission prompt."""
        rows = []
        for num, label in options:
            # Truncate long labels for button display
            short = label[:40] + "..." if len(label) > 40 else label
            rows.append([(f"{num}. {short}", f"p:{num}:{session_id}")])
        return self._kb(rows)

    def _yesno_kb(self, session_id: int):
        """Buttons for a yes/no prompt."""
        return self._kb([
            [("Yes", f"yn:y:{session_id}"), ("No", f"yn:n:{session_id}")],
            [("Output", f"o:{session_id}"), ("Sessions", "sl")],
        ])

    def _continue_kb(self, session_id: int):
        """Button for Press Enter / confirm prompts."""
        return self._kb([
            [("Continue (Enter)", f"cont:{session_id}")],
            [("Output", f"o:{session_id}"), ("Sessions", "sl")],
        ])

    def _input_needed_kb(self, session_id: int):
        """Buttons when session needs input but type is unknown."""
        return self._kb([
            [("Yes", f"yn:y:{session_id}"), ("No", f"yn:n:{session_id}")],
            [("Continue (Enter)", f"cont:{session_id}")],
            [("Focus (type reply)", f"f:{session_id}"), ("Output", f"o:{session_id}")],
        ])

    # ─── Callback handler ──────────────────────────────────────────────

    async def _handle_callback(self, update, context):
        """Route inline keyboard button presses."""
        query = update.callback_query
        user_id = query.from_user.id

        if not self._check_auth(user_id):
            await query.answer("Not authorized.", show_alert=True)
            return

        await query.answer()  # Dismiss loading spinner

        data = query.data
        chat_id = query.message.chat_id

        try:
            if data == "sl":
                await self._cb_sessions(query)
            elif data == "ss":
                await self._cb_status(query, chat_id)
            elif data == "pj":
                await self._cb_projects(query)
            elif data == "cr":
                await self._cb_create(query)
            elif data == "uf":
                await self._cb_unfocus(query, chat_id)
            elif data == "h":
                await self._cb_help(query)
            elif data.startswith("f:"):
                session_id = int(data[2:])
                await self._cb_focus(query, chat_id, session_id)
            elif data.startswith("o:"):
                session_id = int(data[2:])
                await self._cb_output(query, session_id)
            elif data.startswith("s:"):
                session_id = int(data[2:])
                await self._cb_session_detail(query, session_id)
            elif data.startswith("st:"):
                session_id = int(data[3:])
                await self._cb_stop(query, session_id)
            elif data.startswith("p:"):
                # Permission: p:NUM:SESSION_ID
                parts = data.split(":")
                option_num = parts[1]
                session_id = int(parts[2])
                await self._cb_permission(query, session_id, option_num)
            elif data.startswith("yn:"):
                # Yes/No: yn:y:SESSION_ID or yn:n:SESSION_ID
                parts = data.split(":")
                answer = parts[1]  # "y" or "n"
                session_id = int(parts[2])
                await self._cb_yesno(query, session_id, answer)
            elif data.startswith("cont:"):
                # Continue/Enter: cont:SESSION_ID
                session_id = int(data[5:])
                await self._cb_continue(query, session_id)
            elif data.startswith("inp:"):
                # Input needed notification - focus session: inp:SESSION_ID
                session_id = int(data[4:])
                await self._cb_focus(query, chat_id, session_id)
            else:
                await query.edit_message_text("Unknown action.")
        except Exception as e:
            logger.error(f"Callback error: {e}")
            try:
                await query.message.reply_text(f"Error: {e}")
            except Exception:
                pass

    async def _cb_sessions(self, query):
        """Handle sessions button."""
        from ..session_manager import manager
        sessions = manager.get_all_sessions()
        if not sessions:
            await query.edit_message_text(
                "No active sessions.",
                reply_markup=self._kb([[("Create Session", "cr"), ("Refresh", "sl")]]),
            )
            return

        lines = [f"<b>Sessions ({len(sessions)})</b>\n"]
        rows = []
        for s in sessions:
            sd = s.to_dict() if hasattr(s, "to_dict") else s
            status_val = sd.get("status", "unknown")
            icons = {"running": "[RUN]", "needs_attention": "[!]", "stopped": "[X]",
                     "error": "[ERR]", "queued": "[Q]", "completed": "[OK]", "starting": "..."}
            icon = icons.get(status_val, "?")
            sid = sd.get("id", "?")
            name = html_mod.escape(str(sd.get("name", "?")))
            lines.append(f"{icon} <b>#{sid}</b> {name} - {status_val}")
            rows.append([
                (f"Focus #{sid}", f"f:{sid}"),
                (f"Output", f"o:{sid}"),
                (f"Details", f"s:{sid}"),
            ])
        rows.append([("Refresh", "sl"), ("Status", "ss"), ("Create", "cr")])

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=self._kb(rows),
        )

    async def _cb_status(self, query, chat_id: int):
        """Handle status button."""
        from ..session_manager import manager
        from ..models import project_manager
        sessions = manager.get_all_sessions()
        session_dicts = [s.to_dict() for s in sessions]
        projects = project_manager.get_all()
        msg = format_system_status(session_dicts, projects)

        focused_id = self._focused_sessions.get(chat_id)
        if focused_id is not None:
            session = manager.get_session(focused_id)
            if session:
                status_val = session.status.value if hasattr(session.status, "value") else str(session.status)
                msg += f"\n\nFocused: <b>#{focused_id}</b> ({session.name}) [{status_val}]"

        kb = self._focused_kb(focused_id) if focused_id else self._main_menu_kb()
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)

    async def _cb_projects(self, query):
        """Handle projects button."""
        from ..models import project_manager
        projects = project_manager.get_all()
        msg = format_project_list(projects)
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=self._main_menu_kb())

    async def _cb_create(self, query):
        """Handle create session button."""
        from ..session_manager import manager
        session = manager.create_session()
        success = await manager.start_session(session)
        if success:
            msg = f"Session <b>#{session.id}</b> '{html_mod.escape(session.name)}' created and started."
            kb = self._session_actions_kb(session.id)
        else:
            msg = f"Session #{session.id} created but failed to start."
            kb = self._main_menu_kb()
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)

    async def _cb_focus(self, query, chat_id: int, session_id: int):
        """Handle focus button."""
        from ..session_manager import manager
        session = manager.get_session(session_id)
        if not session:
            await query.edit_message_text(f"Session #{session_id} not found.", reply_markup=self._main_menu_kb())
            return

        self._focused_sessions[chat_id] = session_id
        status_val = session.status.value if hasattr(session.status, "value") else str(session.status)
        await query.edit_message_text(
            f"Focused on <b>#{session_id}</b> ({html_mod.escape(session.name)})\n"
            f"Status: {status_val}\n\n"
            f"Send any text message to interact with this session.",
            parse_mode="HTML",
            reply_markup=self._focused_kb(session_id),
        )

    async def _cb_unfocus(self, query, chat_id: int):
        """Handle unfocus button."""
        old = self._focused_sessions.pop(chat_id, None)
        msg = f"Unfocused from session #{old}." if old else "No session was focused."
        await query.edit_message_text(msg, reply_markup=self._main_menu_kb())

    async def _cb_output(self, query, session_id: int):
        """Handle output button."""
        from ..session_manager import manager
        session = manager.get_session(session_id)
        if not session:
            await query.edit_message_text(f"Session #{session_id} not found.", reply_markup=self._main_menu_kb())
            return

        terminal = manager.get_session_output(session_id)
        response, _ = self._extract_response(terminal)
        if response:
            if len(response) > 3500:
                response = "...(trimmed)\n" + response[-3500:]
            msg = f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}"
        else:
            msg = f"Session #{session_id}: No output."

        await query.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._session_actions_kb(session_id),
        )

    async def _cb_session_detail(self, query, session_id: int):
        """Handle session detail button."""
        from ..session_manager import manager
        session = manager.get_session(session_id)
        if not session:
            await query.edit_message_text(f"Session #{session_id} not found.", reply_markup=self._main_menu_kb())
            return

        msg = format_session_status(session.to_dict())
        await query.message.reply_text(
            msg,
            parse_mode="HTML",
            reply_markup=self._session_actions_kb(session_id),
        )

    async def _cb_stop(self, query, session_id: int):
        """Handle stop session button."""
        from ..session_manager import manager
        success = await manager.stop_session(session_id)
        msg = f"Session #{session_id} stopped." if success else f"Failed to stop session #{session_id}."
        await query.edit_message_text(msg, reply_markup=self._kb([[("Sessions", "sl"), ("Status", "ss")]]))

    async def _cb_permission(self, query, session_id: int, option_num: str):
        """Handle permission prompt button - send the option number to the session."""
        from ..session_manager import manager
        chat_id = query.message.chat_id

        # Send the option number to the session
        success = await manager.send_input(session_id, option_num + "\r")
        if not success:
            await query.edit_message_text(
                f"Failed to send selection to session #{session_id}.",
                reply_markup=self._focused_kb(session_id),
            )
            return

        # Update the button message to show what was selected
        await query.edit_message_text(
            f"Selected option {option_num} for session #{session_id}.\nWaiting for response...",
            parse_mode="HTML",
        )

        # Now poll for the response
        await self._poll_and_reply(query.message, session_id)

    async def _cb_yesno(self, query, session_id: int, answer: str):
        """Handle yes/no button press - send y or n to the session."""
        from ..session_manager import manager

        label = "Yes" if answer == "y" else "No"
        success = await manager.send_input(session_id, answer + "\r")
        if not success:
            await query.edit_message_text(
                f"Failed to send '{label}' to session #{session_id}.",
                reply_markup=self._focused_kb(session_id),
            )
            return

        await query.edit_message_text(
            f"Sent '{label}' to session #{session_id}.\nWaiting for response...",
            parse_mode="HTML",
        )
        await self._poll_and_reply(query.message, session_id)

    async def _cb_continue(self, query, session_id: int):
        """Handle continue/enter button press."""
        from ..session_manager import manager

        success = await manager.send_input(session_id, "\r")
        if not success:
            await query.edit_message_text(
                f"Failed to send Enter to session #{session_id}.",
                reply_markup=self._focused_kb(session_id),
            )
            return

        await query.edit_message_text(
            f"Sent Enter to session #{session_id}.\nWaiting for response...",
            parse_mode="HTML",
        )
        await self._poll_and_reply(query.message, session_id)

    async def _cb_help(self, query):
        """Handle help button."""
        await query.edit_message_text(format_help_text(), parse_mode="HTML", reply_markup=self._main_menu_kb())

    # ─── Command handlers ──────────────────────────────────────────────

    async def _cmd_start(self, update, context):
        user_id = update.effective_user.id
        if not self._check_auth(user_id):
            await self._reply(update,
                "Not authorized. Your Telegram user ID is "
                f"<code>{user_id}</code>.\n\n"
                "Add this ID to the allowed users list in Autowrkers Settings.")
            return
        await self._reply(update,
            f"Welcome to <b>Autowrkers</b>!\n\n"
            f"You are authorized (ID: <code>{user_id}</code>).",
            reply_markup=self._main_menu_kb())

    async def _cmd_help(self, update, context):
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        await self._reply(update, format_help_text(), reply_markup=self._main_menu_kb())

    async def _cmd_status(self, update, context):
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

            chat_id = update.effective_chat.id
            focused_id = self._focused_sessions.get(chat_id)
            if focused_id is not None:
                session = manager.get_session(focused_id)
                if session:
                    sv = session.status.value if hasattr(session.status, "value") else str(session.status)
                    msg += f"\n\nFocused: <b>#{focused_id}</b> ({session.name}) [{sv}]"

            kb = self._focused_kb(focused_id) if focused_id else self._main_menu_kb()
            await self._reply(update, msg, reply_markup=kb)
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_sessions(self, update, context):
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        try:
            from ..session_manager import manager
            sessions = manager.get_all_sessions()
            if not sessions:
                await self._reply(update, "No active sessions.",
                    reply_markup=self._kb([[("Create Session", "cr")]]))
                return

            lines = [f"<b>Sessions ({len(sessions)})</b>\n"]
            rows = []
            for s in sessions:
                sd = s.to_dict() if hasattr(s, "to_dict") else s
                sv = sd.get("status", "unknown")
                icons = {"running": "[RUN]", "needs_attention": "[!]", "stopped": "[X]",
                         "error": "[ERR]", "queued": "[Q]", "completed": "[OK]", "starting": "..."}
                sid = sd.get("id", "?")
                name = html_mod.escape(str(sd.get("name", "?")))
                lines.append(f"{icons.get(sv, '?')} <b>#{sid}</b> {name} - {sv}")
                rows.append([
                    (f"Focus #{sid}", f"f:{sid}"),
                    ("Output", f"o:{sid}"),
                    ("Details", f"s:{sid}"),
                ])
            rows.append([("Create", "cr"), ("Status", "ss")])
            await self._reply(update, "\n".join(lines), reply_markup=self._kb(rows))
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_session(self, update, context):
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
            await self._reply(update, msg, reply_markup=self._session_actions_kb(session_id))
        except ValueError:
            await self._reply(update, "Invalid session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_send(self, update, context):
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
            await self._send_and_stream(update, session_id, text)
        except ValueError:
            await self._reply(update, "Invalid session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_focus(self, update, context):
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        args = context.args
        chat_id = update.effective_chat.id

        if not args:
            focused = self._focused_sessions.get(chat_id)
            if focused:
                from ..session_manager import manager
                session = manager.get_session(focused)
                name = session.name if session else "?"
                await self._reply(update,
                    f"Currently focused on <b>#{focused}</b> ({html_mod.escape(str(name))}).\n"
                    f"Send text to interact.",
                    reply_markup=self._focused_kb(focused))
            else:
                # Show session list with focus buttons
                await self._cmd_sessions(update, context)
            return

        try:
            session_id = int(args[0])
            from ..session_manager import manager
            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return
            self._focused_sessions[chat_id] = session_id
            sv = session.status.value if hasattr(session.status, "value") else str(session.status)
            await self._reply(update,
                f"Focused on <b>#{session_id}</b> ({html_mod.escape(session.name)})\n"
                f"Status: {sv}\n\nSend any text to interact.",
                reply_markup=self._focused_kb(session_id))
        except ValueError:
            await self._reply(update, "Invalid session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_unfocus(self, update, context):
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        chat_id = update.effective_chat.id
        old = self._focused_sessions.pop(chat_id, None)
        msg = f"Unfocused from session #{old}." if old else "No session was focused."
        await self._reply(update, msg, reply_markup=self._main_menu_kb())

    async def _cmd_create(self, update, context):
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
                await self._reply(update,
                    f"Session <b>#{session.id}</b> '{html_mod.escape(session.name)}' created and started.",
                    reply_markup=self._session_actions_kb(session.id))
            else:
                await self._reply(update, f"Session #{session.id} created but failed to start.",
                    reply_markup=self._main_menu_kb())
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_stop_session(self, update, context):
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
            msg = f"Session #{session_id} stopped." if success else f"Failed to stop #{session_id}."
            await self._reply(update, msg, reply_markup=self._kb([[("Sessions", "sl"), ("Status", "ss")]]))
        except ValueError:
            await self._reply(update, "Invalid session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_output(self, update, context):
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
            session = manager.get_session(session_id)
            if not session:
                await self._reply(update, f"Session #{session_id} not found.")
                return
            terminal = manager.get_session_output(session_id)
            response, _ = self._extract_response(terminal)
            if response:
                if len(response) > 3500:
                    response = "...(trimmed)\n" + response[-3500:]
                msg = f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}"
            else:
                msg = f"Session #{session_id}: No parsed output."
            await self._reply(update, msg, reply_markup=self._session_actions_kb(session_id))
        except ValueError:
            await self._reply(update, "Invalid session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_projects(self, update, context):
        if not self._check_auth(update.effective_user.id):
            await self._reply(update, "Not authorized.")
            return
        try:
            from ..models import project_manager
            projects = project_manager.get_all()
            msg = format_project_list(projects)
            await self._reply(update, msg, reply_markup=self._main_menu_kb())
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_issues(self, update, context):
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
            await self._reply(update, msg, reply_markup=self._main_menu_kb())
        except ValueError:
            await self._reply(update, "Invalid project ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_startissue(self, update, context):
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
                await self._reply(update, "Project not found.")
                return
            await automation_controller.start_issue_session(project, issue_session)
            await self._reply(update,
                f"Started issue session #{issue_session_id} "
                f"(Issue #{issue_session.github_issue_number}: "
                f"{issue_session.github_issue_title[:50]})")
        except ValueError:
            await self._reply(update, "Invalid issue session ID.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_subscribe(self, update, context):
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
            await self._reply(update, f"Subscribed to project #{project_id}.")
        except Exception as e:
            await self._reply(update, f"Error: {e}")

    async def _cmd_unsubscribe(self, update, context):
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

    # ─── Text handler ──────────────────────────────────────────────────

    async def _handle_text(self, update, context):
        """Handle plain text - forward to focused session or show menu."""
        user_id = update.effective_user.id
        if not self._check_auth(user_id):
            await self._reply(update,
                f"Not authorized. Your ID: <code>{user_id}</code>.\n"
                "Add it to allowed users in Autowrkers Settings.")
            return

        chat_id = update.effective_chat.id
        text = update.message.text.strip()
        text_lower = text.lower()

        focused_id = self._focused_sessions.get(chat_id)
        if focused_id is not None:
            # Quick text commands while focused
            if text_lower in ("unfocus", "detach"):
                return await self._cmd_unfocus(update, context)
            if text_lower in ("status", "stats"):
                return await self._cmd_status(update, context)
            if text_lower in ("sessions", "list"):
                return await self._cmd_sessions(update, context)

            # Forward to focused session
            from ..session_manager import manager
            session = manager.get_session(focused_id)
            if not session:
                self._focused_sessions.pop(chat_id, None)
                await self._reply(update,
                    f"Session #{focused_id} no longer exists. Focus cleared.",
                    reply_markup=self._main_menu_kb())
                return

            sv = session.status.value if hasattr(session.status, "value") else str(session.status)
            if sv in ("stopped", "error", "completed"):
                await self._reply(update,
                    f"Session #{focused_id} is {sv}. Cannot send input.",
                    reply_markup=self._kb([
                        [("Sessions", "sl"), ("Create", "cr")],
                        [("Unfocus", "uf")],
                    ]))
                return

            await self._send_and_stream(update, focused_id, text)
            return

        # Unfocused shortcuts
        if text_lower in ("help", "?"):
            return await self._cmd_help(update, context)
        if text_lower in ("status", "stats"):
            return await self._cmd_status(update, context)
        if text_lower in ("sessions", "list"):
            return await self._cmd_sessions(update, context)
        if text_lower in ("projects",):
            return await self._cmd_projects(update, context)
        if text_lower in ("issues",):
            return await self._cmd_issues(update, context)

        await self._reply(update,
            "No session focused. Tap a button or use /focus &lt;id&gt;.",
            reply_markup=self._main_menu_kb())

    # ─── Send + stream response ────────────────────────────────────────

    async def _send_and_stream(self, update, session_id: int, text: str):
        """Send input to a session and stream Claude's response back with buttons."""
        from ..session_manager import manager

        session = manager.get_session(session_id)
        if not session:
            await self._reply(update, f"Session #{session_id} not found.")
            return

        success = await manager.send_input(session_id, text + "\r")
        if not success:
            await self._reply(update, f"Failed to send to session #{session_id}.",
                reply_markup=self._focused_kb(session_id))
            return

        placeholder = await update.message.reply_text(
            f"Session #{session_id} processing...", parse_mode="HTML")

        await self._poll_and_reply(placeholder, session_id)

    async def _poll_and_reply(self, message, session_id: int):
        """Poll session output and update the message with Claude's response."""
        from ..session_manager import manager

        max_wait = 300
        poll_interval = 1.5
        stable_count = 0
        last_response = ""
        elapsed = 0
        last_update_text = ""

        while elapsed < max_wait:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            terminal = manager.get_session_output(session_id)
            session = manager.get_session(session_id)
            if not session:
                break

            response, options = self._extract_response(terminal)
            sv = session.status.value if hasattr(session.status, "value") else str(session.status)

            # Permission prompt detected - show buttons
            if options:
                kb = self._permission_kb(session_id, options)
                try:
                    await message.edit_text(
                        f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}",
                        parse_mode="HTML", reply_markup=kb)
                except Exception:
                    pass
                return  # Stop polling - user will tap a button

            # Input needed - detect type and show appropriate buttons
            if sv == "needs_attention":
                input_type = self._detect_input_type(terminal)
                if input_type:
                    context = self._extract_question_context(terminal)
                    display = response if response and response != "(thinking...)" else context
                    if display:
                        display = display[-3000:]
                    await self._show_input_buttons(message, session_id, input_type, display)
                    return  # Stop polling - user will tap a button

            # Check stability
            if response and response == last_response:
                stable_count += 1
            else:
                stable_count = 0
                last_response = response

            # Done conditions
            if sv == "needs_attention" and response and response != "(thinking...)":
                break
            if stable_count >= 4 and response and response != "(thinking...)":
                break
            if sv in ("stopped", "error", "completed"):
                break

            # Progress update every ~10s
            if elapsed % 10 < poll_interval and response and response != last_update_text:
                try:
                    trimmed = response[-3500:]
                    await message.edit_text(
                        f"<b>Session #{session_id}</b> (responding...)\n\n{html_mod.escape(trimmed)}",
                        parse_mode="HTML")
                    last_update_text = response
                except Exception:
                    pass

        # Final response
        terminal = manager.get_session_output(session_id)
        response, options = self._extract_response(terminal)

        if options:
            kb = self._permission_kb(session_id, options)
            try:
                await message.edit_text(
                    f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}",
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass
            return

        # Check for input prompts at the end
        input_type = self._detect_input_type(terminal)
        if input_type:
            context = self._extract_question_context(terminal)
            display = response if response and response != "(thinking...)" else context
            if display:
                display = display[-3000:]
            await self._show_input_buttons(message, session_id, input_type, display)
            return

        chat_id = message.chat_id
        focused_id = self._focused_sessions.get(chat_id)
        kb = self._focused_kb(session_id) if focused_id == session_id else self._session_actions_kb(session_id)

        if response and response != "(thinking...)":
            if len(response) > 3500:
                response = "...(trimmed)\n" + response[-3500:]
            try:
                await message.edit_text(
                    f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}",
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                try:
                    await message.chat.send_message(
                        f"<b>Session #{session_id}</b>\n\n{html_mod.escape(response)}",
                        parse_mode="HTML", reply_markup=kb)
                except Exception:
                    pass
        else:
            try:
                await message.edit_text(
                    f"Session #{session_id}: No response received.",
                    reply_markup=kb)
            except Exception:
                pass

    async def _show_input_buttons(self, message, session_id: int, input_type: str, context: str):
        """Show appropriate input buttons based on what the terminal is waiting for."""
        if input_type == 'yesno':
            kb = self._yesno_kb(session_id)
            label = "Yes/No required"
        elif input_type == 'continue':
            kb = self._continue_kb(session_id)
            label = "Press Enter to continue"
        else:  # 'question' or fallback
            kb = self._input_needed_kb(session_id)
            label = "Input required"

        parts = [f"<b>Session #{session_id}</b> — {label}"]
        if context:
            parts.append(html_mod.escape(context))

        try:
            await message.edit_text(
                '\n\n'.join(parts),
                parse_mode="HTML", reply_markup=kb)
        except Exception:
            try:
                await message.chat.send_message(
                    '\n\n'.join(parts),
                    parse_mode="HTML", reply_markup=kb)
            except Exception:
                pass

    # ─── Terminal output parsing ───────────────────────────────────────

    def _extract_response(self, terminal_output: str) -> Tuple[str, Optional[List[Tuple[str, str]]]]:
        """Parse Claude Code terminal output. Returns (response_text, permission_options).

        permission_options is None for normal responses, or a list of (number, label) tuples
        for "Do you want to proceed?" prompts.
        """
        if not terminal_output:
            return ("", None)

        text = _ANSI_RE.sub('', terminal_output)
        lines = text.split('\n')

        # Check for permission prompt
        if 'Do you want to proceed?' in text:
            response, options = self._parse_permission_prompt(lines)
            return (response, options)

        # Find response blocks (● prefix) - check BEFORE thinking indicator
        # because ✻ persists after thinking completes (e.g. "✻ Sautéed for 39s")
        response_starts = [i for i, l in enumerate(lines) if l.strip().startswith('●')]

        # If no responses found, check if Claude is still thinking
        if not response_starts:
            if any(line.strip().startswith('✻') for line in lines):
                return ("(thinking...)", None)
            return ("", None)

        last_start = response_starts[-1]

        # Find end of response
        response_end = len(lines)
        for i in range(last_start + 1, len(lines)):
            s = lines[i].strip()
            if re.match(r'^[─]{10,}$', s):
                response_end = i
                break
            if s in ('❯', '❯ '):
                response_end = i
                break

        # Clean response lines
        cleaned = []
        for line in lines[last_start:response_end]:
            s = line.rstrip()
            if re.match(r'^[╭╰│╮╯┌┐└┘├┤┬┴┼─═║]+\s*$', s):
                continue
            if re.match(r'^\s*[─]{10,}\s*$', s):
                continue
            if '? for shortcuts' in s:
                continue
            if s.strip() in ('❯', '❯ '):
                continue
            if s.strip().startswith('✻'):
                continue
            if s.strip().startswith('●'):
                content = s.strip()[1:].strip()
                if content:
                    cleaned.append(content)
                continue
            if s.strip().startswith('⎿'):
                content = s.strip()[1:].strip()
                if content:
                    cleaned.append(content)
                continue
            cleaned.append(s)

        while cleaned and not cleaned[0].strip():
            cleaned.pop(0)
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()

        return ('\n'.join(cleaned), None)

    def _detect_input_type(self, terminal_output: str) -> Optional[str]:
        """Detect what kind of input the terminal is waiting for.

        Returns: 'yesno', 'continue', 'question', or None if no input needed.
        """
        if not terminal_output:
            return None

        text = _ANSI_RE.sub('', terminal_output)
        tail = text[-500:]

        # Permission prompts are handled separately by _extract_response
        if 'Do you want to proceed?' in tail:
            return None

        # Yes/No prompts
        if any(p in tail for p in ('(y/n)', '[Y/n]', '[y/N]', '(Y/n)', '(y/N)')):
            return 'yesno'

        # Continue / Enter prompts
        if any(p in tail for p in ('Press Enter', 'Enter to confirm', 'press enter',
                                    'Press ENTER', 'continue?')):
            return 'continue'

        # General question prompts (Would you like / Do you want to)
        if any(p in tail for p in ('Would you like', 'Do you want to')):
            return 'question'

        return None

    def _extract_question_context(self, terminal_output: str) -> str:
        """Extract the question/prompt text from terminal output for display."""
        if not terminal_output:
            return ""

        text = _ANSI_RE.sub('', terminal_output)
        lines = text.strip().split('\n')

        # Walk backwards from the end to find the question context
        context_lines = []
        for line in reversed(lines[-20:]):
            s = line.strip()
            if not s:
                if context_lines:
                    break
                continue
            if s in ('❯', '❯ ') or '? for shortcuts' in s:
                continue
            if re.match(r'^[╭╰│╮╯┌┐└┘├┤┬┴┼─═║]+\s*$', s):
                if context_lines:
                    break
                continue
            if re.match(r'^[─]{10,}$', s):
                if context_lines:
                    break
                continue
            context_lines.append(s)

        context_lines.reverse()
        # Limit to last 5 meaningful lines
        return '\n'.join(context_lines[-5:])

    def _parse_permission_prompt(self, lines: list) -> Tuple[str, List[Tuple[str, str]]]:
        """Parse permission prompt. Returns (description_text, [(num, label), ...])."""
        prompt_line = None
        for i, line in enumerate(lines):
            if 'Do you want to proceed?' in line:
                prompt_line = i
                break
        if prompt_line is None:
            return ("", [])

        # Find section start (after horizontal rule)
        section_start = prompt_line
        for i in range(prompt_line - 1, -1, -1):
            if re.match(r'^[─]{10,}$', lines[i].strip()):
                section_start = i + 1
                break

        # Find tool line (● above rule)
        tool_line = ""
        for i in range(section_start - 1, -1, -1):
            s = lines[i].strip()
            if s.startswith('●'):
                tool_line = s[1:].strip()
                break
            if s.startswith('❯') or re.match(r'^[╭╰]', s):
                break

        # Extract options
        options = []
        for i in range(prompt_line + 1, len(lines)):
            s = lines[i].strip()
            if 'Esc to cancel' in s or 'Tab to add' in s:
                continue
            if not s:
                continue
            match = re.match(r'^[❯\s]*(\d+)\.\s*(.+)$', s)
            if match:
                options.append((match.group(1), match.group(2).strip()))

        # Build description text
        desc_lines = []
        for i in range(section_start, prompt_line):
            s = lines[i].strip()
            if s and not re.match(r'^[─]{10,}$', s):
                desc_lines.append(s)

        parts = []
        if tool_line:
            parts.append(f"Claude wants to use: {tool_line}")
        if desc_lines:
            parts.append('\n'.join(desc_lines))

        return ('\n\n'.join(parts), options)

    # ─── Event relay ───────────────────────────────────────────────────

    async def _on_session_status(self, session_id: int, status):
        if not self._running or not self._config.push_session_status:
            return
        status_val = status.value if hasattr(status, "value") else str(status)
        if status_val not in ("needs_attention", "error", "completed", "stopped"):
            return
        try:
            from ..session_manager import manager
            session = manager.get_session(session_id)
            if not session:
                return
            icons = {"needs_attention": "[!]", "error": "[ERR]", "completed": "[OK]", "stopped": "[X]"}
            icon = icons.get(status_val, "")
            name = html_mod.escape(session.name)

            if status_val == "needs_attention":
                # Detect input type and send buttons
                terminal = manager.get_session_output(session_id)
                response, options = self._extract_response(terminal or "")
                if options:
                    kb = self._permission_kb(session_id, options)
                    context = ""
                    if response and response != "(thinking...)":
                        context = f"\n\n{html_mod.escape(response[-2000:])}"
                    msg = f"{icon} <b>Session #{session_id}</b> '{name}' needs permission{context}"
                else:
                    input_type = self._detect_input_type(terminal or "")
                    if input_type == 'yesno':
                        kb = self._yesno_kb(session_id)
                        context = self._extract_question_context(terminal or "")
                        ctx_text = f"\n\n{html_mod.escape(context)}" if context else ""
                        msg = f"{icon} <b>Session #{session_id}</b> '{name}' — Yes/No required{ctx_text}"
                    elif input_type == 'continue':
                        kb = self._continue_kb(session_id)
                        msg = f"{icon} <b>Session #{session_id}</b> '{name}' — waiting for Enter"
                    elif input_type == 'question':
                        kb = self._input_needed_kb(session_id)
                        context = self._extract_question_context(terminal or "")
                        ctx_text = f"\n\n{html_mod.escape(context)}" if context else ""
                        msg = f"{icon} <b>Session #{session_id}</b> '{name}' — input required{ctx_text}"
                    else:
                        kb = self._kb([
                            [("Focus", f"f:{session_id}"), ("Output", f"o:{session_id}")],
                            [("Sessions", "sl")],
                        ])
                        msg = f"{icon} <b>Session #{session_id}</b> '{name}' needs attention"
                await self._broadcast_to_all_with_kb(msg, kb)
            else:
                msg = f"{icon} Session #{session_id} '{name}': {status_val}"
                await self._broadcast_to_all(msg)
        except Exception as e:
            logger.error(f"Error relaying session status: {e}")

    async def _on_session_complete(self, session_id: int):
        if not self._running or not self._config.push_session_status:
            return
        try:
            from ..session_manager import manager
            session = manager.get_session(session_id)
            name = session.name if session else f"#{session_id}"
            msg = f"[OK] Session #{session_id} '{name}' completed"
            await self._broadcast_to_all(msg)
        except Exception as e:
            logger.error(f"Error relaying completion: {e}")

    async def _on_automation_event(self, event_type: str, data: dict):
        if not self._running or not self._config.push_automation_events:
            return
        project_id = str(data.get("project_id", ""))
        issue_num = data.get("issue_number", "?")
        issue_title = data.get("issue_title", "")
        messages = {
            "issue_started": f"Started issue #{issue_num}: {issue_title}",
            "verification_started": f"Verifying issue #{issue_num}...",
            "verification_passed": f"[OK] Issue #{issue_num} passed verification",
            "verification_failed": f"[!] Issue #{issue_num} failed verification"
                + (f" ({data.get('error', '')})" if data.get("error") else ""),
            "pr_created": f"[PR] PR #{data.get('pr_number', '?')} for issue #{issue_num}",
            "issue_failed": f"[ERR] Issue #{issue_num} failed: {data.get('error', 'unknown')}",
            "issue_completed": f"[OK] Issue #{issue_num} completed",
        }
        msg = messages.get(event_type)
        if not msg:
            return
        if project_id:
            await self._broadcast_to_subscribers(project_id, msg)
        else:
            await self._broadcast_to_all(msg)

    # ─── Helpers ───────────────────────────────────────────────────────

    async def _reply(self, update, text: str, parse_mode: str = "HTML", reply_markup=None):
        """Send reply with optional inline keyboard."""
        try:
            if len(text) > 4096:
                text = text[:4090] + "\n..."
            await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Failed to send reply: {e}")
            try:
                await update.message.reply_text(f"Error formatting response: {e}")
            except Exception:
                pass

    async def _broadcast_to_subscribers(self, project_id: str, message: str):
        if not self._app or not self._running:
            return
        for chat_id, subs in self._chat_subscriptions.items():
            if project_id in subs:
                try:
                    await self._app.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Failed to send to chat {chat_id}: {e}")

    async def _broadcast_to_all(self, message: str):
        if not self._app or not self._running:
            return
        sent = set()
        for chat_id in self._chat_subscriptions:
            if chat_id not in sent:
                try:
                    await self._app.bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
                    sent.add(chat_id)
                except Exception as e:
                    logger.error(f"Failed to broadcast to {chat_id}: {e}")

    async def _broadcast_to_all_with_kb(self, message: str, reply_markup):
        """Broadcast with inline keyboard buttons to all subscribed + focused chats."""
        if not self._app or not self._running:
            return
        sent = set()
        # Include subscribed chats and chats with focused sessions
        all_chat_ids = set(self._chat_subscriptions.keys()) | set(self._focused_sessions.keys())
        for chat_id in all_chat_ids:
            if chat_id not in sent:
                try:
                    await self._app.bot.send_message(
                        chat_id=chat_id, text=message,
                        parse_mode="HTML", reply_markup=reply_markup)
                    sent.add(chat_id)
                except Exception as e:
                    logger.error(f"Failed to broadcast to {chat_id}: {e}")

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "username": self._bot_username,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "allowed_users": len(self._allowed_users),
            "subscribed_chats": len(self._chat_subscriptions),
        }

    def get_config(self) -> TelegramBotConfig:
        return self._config

    def update_config(self, config_data: dict):
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
