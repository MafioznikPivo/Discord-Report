from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing or invalid."""


def _parse_required_int(name: str) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ConfigError(f"Отсутствует обязательная переменная окружения: {name}")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigError(f"Переменная окружения {name} должна быть целым числом") from exc
    if parsed <= 0:
        raise ConfigError(f"Переменная окружения {name} должна быть больше 0")
    return parsed


def _parse_optional_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ConfigError(f"Переменная окружения {name} должна быть целым числом") from exc
    if parsed <= 0:
        raise ConfigError(f"Переменная окружения {name} должна быть больше 0")
    return parsed


def _parse_required_role_ids(name: str) -> set[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        raise ConfigError(f"Отсутствует обязательная переменная окружения: {name}")
    role_ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            role_id = int(part)
        except ValueError as exc:
            raise ConfigError(
                f"Переменная {name} должна содержать ID ролей (целые числа) через запятую"
            ) from exc
        if role_id <= 0:
            raise ConfigError(
                f"Переменная {name} должна содержать только ID ролей больше 0"
            )
        role_ids.add(role_id)
    if not role_ids:
        raise ConfigError(f"Переменная {name} должна содержать хотя бы один ID роли")
    return role_ids


@dataclass(frozen=True)
class AppConfig:
    bot_token: str
    target_guild_id: int
    report_intake_channel_id: int
    help_intake_channel_id: int
    report_category_id: int
    moderator_role_ids: set[int]
    report_initial_join_deadline_sec: int
    report_missing_move_deadline_sec: int
    scheduler_poll_sec: int
    db_path: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()

        bot_token = os.getenv("BOT_TOKEN")
        if bot_token is None or not bot_token.strip():
            raise ConfigError("Отсутствует обязательная переменная окружения: BOT_TOKEN")

        return cls(
            bot_token=bot_token.strip(),
            target_guild_id=_parse_required_int("TARGET_GUILD_ID"),
            report_intake_channel_id=_parse_required_int("REPORT_INTAKE_CHANNEL_ID"),
            help_intake_channel_id=_parse_required_int("HELP_INTAKE_CHANNEL_ID"),
            report_category_id=_parse_required_int("REPORT_CATEGORY_ID"),
            moderator_role_ids=_parse_required_role_ids("MODERATOR_ROLE_IDS"),
            report_initial_join_deadline_sec=_parse_optional_int(
                "REPORT_INITIAL_JOIN_DEADLINE_SEC", 180
            ),
            report_missing_move_deadline_sec=_parse_optional_int(
                "REPORT_MISSING_MOVE_DEADLINE_SEC", 300
            ),
            scheduler_poll_sec=_parse_optional_int("SCHEDULER_POLL_SEC", 30),
            db_path=os.getenv("DB_PATH", "data/report_sataki.db").strip(),
        )
