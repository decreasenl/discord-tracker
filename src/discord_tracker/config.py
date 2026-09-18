import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    database: Path

    @classmethod
    def load(cls):
        token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("DISCORD_BOT_TOKEN is required")
        return cls(token, Path(os.getenv("SQLITE_PATH", "data/community.sqlite3")))


def bootstrap_settings():
    settings = {}
    for key in ("DISCORD_GUILD_ID", "DISCORD_BOT_ACCESS_ROLE_ID", "DISCORD_MANAGER_ROLE_ID"):
        value = os.getenv(key, "")
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError(f"{key} must be a positive Discord ID for initial setup")
        settings[key] = value
    timezone = os.getenv("COMMUNITY_TIMEZONE", "Europe/Amsterdam")
    ZoneInfo(timezone)
    settings["COMMUNITY_TIMEZONE"] = timezone
    group = os.getenv("WOM_GROUP_ID", "")
    if group:
        if not group.isdecimal() or int(group) <= 0:
            raise ValueError("WOM_GROUP_ID must be a positive integer")
        settings["WOM_GROUP_ID"] = group
    return settings
