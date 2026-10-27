"""Parse schedule.txt and slot labels, check for overlaps. Pure: no I/O."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from .models import Slot

# Full names may carry the Macedonian definite article ("средата", "петокот").
_FULL_NAMES = {
    0: ["понеделник", "ponedelnik", "monday"],
    1: ["вторник", "vtornik", "tuesday"],
    2: ["среда", "sreda", "wednesday"],
    3: ["четврток", "cetvrtok", "chetvrtok", "četvrtok", "thursday"],
    4: ["петок", "petok", "friday"],
    5: ["сабота", "sabota", "saturday"],
    6: ["недела", "nedela", "sunday"],
}
_ABBREVIATIONS = {
    0: ["пон", "pon", "mon"],
    1: ["вто", "вт", "vto", "tue", "tues"],
    2: ["сре", "ср", "sre", "wed"],
    3: ["чет", "чт", "cet", "chet", "thu", "thur", "thurs"],
    4: ["пет", "пт", "pet", "fri"],
    5: ["саб", "сб", "sab", "sat"],
    6: ["нед", "нд", "ned", "sun"],
}
_DAY_LOOKUP = {name: day for names in (_FULL_NAMES, _ABBREVIATIONS) for day, words in names.items() for name in words}
_FULL_SET = {name for words in _FULL_NAMES.values() for name in words}

_DAY_RE = re.compile(
    r"(?<!\w)(?P<name>" + "|".join(sorted(map(re.escape, _DAY_LOOKUP), key=len, reverse=True)) + r")"
    r"(?P<suffix>от|та|ot|ta)?\.?(?!\w)"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?:(?P<d>\d{1,2})[./](?P<m>\d{1,2})[./](?P<y>\d{4})"
    r"|(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2}))(?!\d)"
)
# Lookahead so overlapping candidates are all seen ("Група 3 - 10-12" yields "3 - 10" and "10-12").
_RANGE_RE = re.compile(
    r"(?=(?<![\d:.])(?P<h1>\d{1,2})(?:[:.](?P<m1>\d{2}))?\s*(?:-|–|—|до|to)\s*"
    r"(?P<h2>\d{1,2})(?:[:.](?P<m2>\d{2}))?(?![\d:]))"
)

# Sanity bounds for a class slot, used to reject room numbers and group numbers.
_EARLIEST = 6 * 60
_LATEST = 23 * 60
_MAX_LENGTH = 6 * 60


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def fmt_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _weekdays(text: str) -> set[int]:
    days = set()
    for m in _DAY_RE.finditer(text):
        name, suffix = m.group("name"), m.group("suffix")
        if suffix and name not in _FULL_SET:
            continue  # an article only goes on a full name
        days.add(_DAY_LOOKUP[name])
    return days


def _dates(text: str) -> set[date]:
    found = set()
    for m in _DATE_RE.finditer(text):
        try:
            if m["y"]:
                found.add(date(int(m["y"]), int(m["m"]), int(m["d"])))
            else:
                found.add(date(int(m["iy"]), int(m["im"]), int(m["id"])))
        except ValueError:
            pass
    return found


def _time_range(text: str) -> tuple[int, int] | None:
    ranges: dict[tuple[int, int], bool] = {}
    for m in _RANGE_RE.finditer(text):
        h1, h2 = int(m["h1"]), int(m["h2"])
        m1, m2 = int(m["m1"] or 0), int(m["m2"] or 0)
        if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
            continue
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if not (_EARLIEST <= start < end <= _LATEST and end - start <= _MAX_LENGTH):
            continue
        has_minutes = m["m1"] is not None or m["m2"] is not None
        ranges[(start, end)] = ranges.get((start, end), False) or has_minutes
    precise = [r for r, has_minutes in ranges.items() if has_minutes]
    candidates = precise or list(ranges)
    return candidates[0] if len(candidates) == 1 else None


def parse_slot(label: str) -> Slot | None:
    """Read a weekday and a time range out of a label, or None if unclear.

    The weekday comes from a day name or a dd.mm.yyyy date. A label whose
    date and day name disagree, or that has several days or time ranges,
    is treated as unreadable rather than guessed.
    """
    text = normalize(label)
    dates = _dates(text)
    if len(dates) > 1:
        return None
    text_without_dates = _DATE_RE.sub(" ", text)
    days = _weekdays(text_without_dates)
    day = next(iter(dates), None)
    if day is not None:
        if days and days != {day.weekday()}:
            return None
        days = {day.weekday()}
    if len(days) != 1:
        return None
    times = _time_range(text_without_dates)
    if times is None:
        return None
    return Slot(weekday=days.pop(), start=times[0], end=times[1], day=day, label=label.strip())


def parse_schedule(text: str) -> list[Slot]:
    """Parse schedule.txt: one "<day> <HH:MM>-<HH:MM> <what>" per line, # comments."""
    busy = []
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        slot = parse_slot(line)
        if slot is None:
            raise ValueError(f"schedule.txt line {number}: can't read a day and time range in {line!r}")
        busy.append(slot)
    return busy


def clashes(a: Slot, b: Slot, gap_minutes: int = 0) -> bool:
    if a.weekday != b.weekday:
        return False
    if a.day and b.day and a.day != b.day:
        return False
    return a.start < b.end + gap_minutes and b.start < a.end + gap_minutes


def clashing(slot: Slot, busy: list[Slot], gap_minutes: int = 0) -> list[Slot]:
    return [b for b in busy if clashes(slot, b, gap_minutes)]
