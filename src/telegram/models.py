"""
Data models for the Telegram bot module.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class TelegramBotConfig:
    """Configuration for the Telegram bot."""
    enabled: bool = False
    bot_token: str = ""
    allowed_user_ids: List[int] = field(default_factory=list)
    # What events to push automatically
    push_session_status: bool = True
    push_automation_events: bool = True
    push_session_output: bool = False     # Verbose - disabled by default
    output_max_lines: int = 20            # Truncate output messages

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "bot_token": self.bot_token,
            "allowed_user_ids": self.allowed_user_ids,
            "push_session_status": self.push_session_status,
            "push_automation_events": self.push_automation_events,
            "push_session_output": self.push_session_output,
            "output_max_lines": self.output_max_lines,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TelegramBotConfig":
        return cls(
            enabled=data.get("enabled", False),
            bot_token=data.get("bot_token", ""),
            allowed_user_ids=data.get("allowed_user_ids", []),
            push_session_status=data.get("push_session_status", True),
            push_automation_events=data.get("push_automation_events", True),
            push_session_output=data.get("push_session_output", False),
            output_max_lines=data.get("output_max_lines", 20),
        )

    def to_safe_dict(self) -> dict:
        """Return config with token masked for API responses."""
        d = self.to_dict()
        if d["bot_token"]:
            token = d["bot_token"]
            if len(token) > 10:
                d["bot_token"] = token[:4] + "..." + token[-4:]
            else:
                d["bot_token"] = "***"
        return d
