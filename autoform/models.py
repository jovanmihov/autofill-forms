"""Plain dataclasses shared by the modules. No I/O, no Playwright."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum


class FormType(str, Enum):
    MIDTERM = "midterm"
    LAB = "lab"
    IGNORE = "ignore"


class Action(str, Enum):
    SUBMIT = "submit"
    SKIP = "skip"              # nothing to do, never look at it again
    NEEDS_USER = "needs_user"  # can't decide safely, tell the user
    RETRY = "retry"            # form not open yet, look again next run


class PageStatus(str, Enum):
    OPEN = "open"
    NOT_OPEN_YET = "not_open_yet"
    CLOSED = "closed"


@dataclass(frozen=True)
class Email:
    message_id: str
    subject: str
    body: str  # plain text
    received: datetime | None  # local time, None if Outlook's date format wasn't understood
    links: tuple[tuple[str, str], ...] = ()  # (url, link text)


@dataclass(frozen=True)
class Option:
    """One answer on a Moodle form, as read from the page."""
    value: str
    label: str
    input_name: str = "answer"
    disabled: bool = False
    checked: bool = False


@dataclass(frozen=True)
class FormPage:
    """What the browser layer reports about an opened activity."""
    url: str
    title: str = ""
    intro: str = ""
    options: tuple[Option, ...] = ()
    already_answered: bool = False
    selection: str = ""  # Moodle's "Your selection: ..." text, if shown
    status: PageStatus = PageStatus.OPEN


@dataclass(frozen=True)
class Slot:
    """A weekly time slot. Times are minutes after midnight, end exclusive."""
    weekday: int  # 0 = Monday
    start: int
    end: int
    day: date | None = None
    label: str = ""


@dataclass(frozen=True)
class Profile:
    program_aliases: tuple[str, ...]
    other_programs: tuple[str, ...] = ()
    min_gap_minutes: int = 0
    prefer: str = "earliest"  # "earliest" | "latest"


@dataclass(frozen=True)
class Decision:
    action: Action
    form_type: FormType | None = None
    option: Option | None = None
    reason: str = ""
    notes: tuple[str, ...] = ()
