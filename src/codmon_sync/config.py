from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    codmon_email: str
    codmon_password: str
    huckleberry_email: str
    huckleberry_password: str
    timezone_name: str
    dry_run: bool
    data_dir: Path
    headless: bool
    sync_temperature: bool
    sync_diaper: bool
    sync_bath: bool
    bath_time: time
    codmon_transport: str
    child: str | None
    dedup_window_minutes: int
    translate_activity: bool
    llm_base_url: str
    llm_model: str
    llm_api_key: str | None

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)

    @property
    def state_path(self) -> Path:
        return self.data_dir / "state.json"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_time(value: str | None, default: time) -> time:
    if not value:
        return default
    try:
        hour, minute = value.strip().split(":", 1)
        return time(int(hour), int(minute))
    except ValueError, AttributeError:
        return default


def load_config(env_path: Path | None = None, *, dry_run: bool | None = None) -> Config:
    load_dotenv(env_path)

    def require(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            raise ConfigError(f"Missing required environment variable {name}")
        return value

    codmon_email = require("CODMON_EMAIL")
    codmon_password = require("CODMON_PASSWORD")
    huckleberry_email = require("HUCKLEBERRY_EMAIL")
    huckleberry_password = require("HUCKLEBERRY_PASSWORD")

    if dry_run is None:
        dry_run = _parse_bool(os.environ.get("DRY_RUN"), True)

    project_root = Path(__file__).resolve().parent.parent
    default_data_dir = project_root / "data"
    data_dir = Path(os.environ.get("DATA_DIR", default_data_dir)).expanduser()
    translate_activity = _parse_bool(os.environ.get("TRANSLATE_ACTIVITY"), False)
    llm_base_url = (os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
    if translate_activity and not llm_base_url:
        raise ConfigError("TRANSLATE_ACTIVITY is true but LLM_BASE_URL is not set")
    return Config(
        codmon_email=codmon_email,
        codmon_password=codmon_password,
        huckleberry_email=huckleberry_email,
        huckleberry_password=huckleberry_password,
        timezone_name=os.environ.get("TIMEZONE", "Asia/Tokyo"),
        dry_run=dry_run,
        data_dir=data_dir,
        headless=_parse_bool(os.environ.get("HEADLESS"), True),
        sync_temperature=_parse_bool(os.environ.get("SYNC_TEMPERATURE"), True),
        sync_diaper=_parse_bool(os.environ.get("SYNC_DIAPER"), True),
        sync_bath=_parse_bool(os.environ.get("SYNC_BATH"), True),
        bath_time=_parse_time(os.environ.get("BATH_TIME"), time(9, 30)),
        codmon_transport=os.environ.get("CODMON_TRANSPORT", "api").strip().lower(),
        child=os.environ.get("CHILD") or None,
        dedup_window_minutes=_parse_int(os.environ.get("DEDUP_WINDOW_MINUTES"), 15),
        translate_activity=translate_activity,
        llm_base_url=llm_base_url,
        llm_model=os.environ.get("LLM_MODEL", "chat-default").strip(),
        llm_api_key=os.environ.get("LLM_API_KEY") or None,
    )
