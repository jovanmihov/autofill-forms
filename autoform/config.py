"""Load config.yaml and preferences/. Paths are fixed relative to the project folder."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .models import Profile, Slot
from .schedule import parse_schedule

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.yaml"
PREFERENCES_DIR = ROOT / "preferences"
EXAMPLE_DIR = ROOT / "preferences.example"
STATE_FILE = ROOT / "state.json"
LOGS_DIR = ROOT / "logs"
BROWSER_PROFILE_DIR = ROOT / "browser-profile"


class SetupError(Exception):
    """Something the user has to fix before the script can run."""


@dataclass(frozen=True)
class Config:
    moodle_url: str
    cas_login_url: str
    lookback_days: int
    headless: bool
    browser_channel: str | None
    page_timeout_seconds: int
    keywords: dict[str, tuple[str, ...]]
    outlook_url: str
    moodle_sender: str
    poll_seconds: int

    @property
    def moodle_host(self) -> str:
        return urlparse(self.moodle_url).hostname or ""


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as e:
        raise SetupError(f"{path.name}: {e}") from e
    if not isinstance(data, dict):
        raise SetupError(f"{path.name}: expected key: value pairs at the top level")
    return data


def _strings(data: dict, key: str, where: str) -> tuple[str, ...]:
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SetupError(f"{where}: {key} must be a list of strings")
    return tuple(v.strip() for v in value if v.strip())


def load_config(path: Path = CONFIG_FILE) -> Config:
    data = _read_yaml(path)
    keywords = data.get("keywords") or {}
    return Config(
        moodle_url=str(data.get("moodle_url", "https://courses.finki.ukim.mk")).rstrip("/"),
        cas_login_url=str(data["cas_login_url"]),
        lookback_days=int(data.get("lookback_days", 3)),
        headless=bool(data.get("headless", True)),
        browser_channel=data.get("browser_channel") or None,
        page_timeout_seconds=int(data.get("page_timeout_seconds", 30)),
        keywords={kind: _strings(keywords, kind, "config.yaml keywords") for kind in ("midterm", "lab")},
        outlook_url=str(data.get("outlook_url", "https://outlook.office.com/mail/")),
        moodle_sender=str(data.get("moodle_sender", "noreply@courses.finki.ukim.mk")),
        poll_seconds=max(10, int(data.get("poll_seconds", 30))),
    )


def _require_preferences(folder: Path) -> None:
    if not (folder / "profile.yaml").is_file() or not (folder / "schedule.txt").is_file():
        raise SetupError("copy preferences.example to preferences and fill it in")


def load_profile(folder: Path = PREFERENCES_DIR) -> Profile:
    _require_preferences(folder)
    where = "preferences/profile.yaml"
    data = _read_yaml(folder / "profile.yaml")
    lab = data.get("lab") or {}
    profile = Profile(
        program_aliases=_strings(data, "program_aliases", where),
        other_programs=_strings(data, "other_programs", where),
        min_gap_minutes=int(lab.get("min_gap_minutes", 0)),
        prefer=str(lab.get("prefer", "earliest")),
    )
    if not profile.program_aliases:
        raise SetupError(f"{where}: program_aliases is empty")
    if profile.prefer not in ("earliest", "latest"):
        raise SetupError(f"{where}: lab.prefer must be earliest or latest")
    if profile.min_gap_minutes < 0:
        raise SetupError(f"{where}: lab.min_gap_minutes can't be negative")
    return profile


def load_schedule(folder: Path = PREFERENCES_DIR) -> list[Slot]:
    _require_preferences(folder)
    try:
        return parse_schedule((folder / "schedule.txt").read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise SetupError(str(e)) from e


def check_not_example(folder: Path = PREFERENCES_DIR, example: Path = EXAMPLE_DIR) -> None:
    """Refuse to submit anything with the made-up example program or schedule."""
    if load_profile(folder).program_aliases == load_profile(example).program_aliases:
        raise SetupError("preferences/profile.yaml still has the example program_aliases: put in your own")
    mine = (folder / "schedule.txt").read_text(encoding="utf-8-sig")
    if mine.strip() == (example / "schedule.txt").read_text(encoding="utf-8-sig").strip():
        raise SetupError("preferences/schedule.txt is still the example schedule: put in your own")
