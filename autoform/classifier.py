"""Decide whether a form is a midterm attendance form, a lab term form, or neither.

First from email keywords, then confirmed by the shape of the options on the page.
Pure: no I/O.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from functools import lru_cache

from .models import Analysis, Category, FormType, Option
from .schedule import normalize, parse_slot

# Moodle starts a course email's subject with the course's short name: "Аипс-2026/2027/Z-49_45136: ...".
_COURSE_RE = re.compile(r"\s*([^\s:]{1,30}?)(?:-\d{2,4}/\d{2,4}[^\s:]*)?:\s+")
_NOT_COURSES = ("re", "fw", "fwd", "aw", "tr")

# What a midterm option saying "I'll be there" can look like. Whole words, so "полагам" also covers
# "Ќе полагам (мк)", "Ке го полагам" and "Сакам да полагам". An option with any NO_WORDS is never a yes:
# "Не полагам", "Нема да присуствувам", "I won't take the exam".
YES_WORDS = (
    # Macedonian
    "да",
    "полагам",                                   # ќе полагам, ќе го полагам колоквиумот
    "присуствувам", "присутен", "присутна",      # ќе присуствувам
    "пристапувам", "пристапам",                  # ќе пристапам на колоквиумот
    "излегувам", "излезам",                      # ќе излезам на колоквиум
    "учествувам",                                # ќе учествувам
    "доаѓам", "дојдам",                          # ќе дојдам
    "пријавувам",                                # се пријавувам
    "заинтересиран", "заинтересирана",           # заинтересиран сум за полагање
    # Macedonian in Latin letters
    "da", "polagam", "prisustvuvam", "prisuten", "prisutna", "pristapuvam", "pristapam",
    "izleguvam", "izlezam", "uchestvuvam", "ucestvuvam", "doagjam", "doagam", "dojdam",
    "prijavuvam", "zainteresiran", "zainteresirana",
    # English
    "yes",
    "will take", "i'll take", "want to take", "take the exam", "take the midterm", "taking",
    "will sit", "i'll sit", "sit the exam",
    "attend", "attending",                       # I will attend
    "participate", "participating",
    "will come", "i'll come", "coming", "will be there", "i'll be there",
    "present", "interested", "count me in", "sign me up",
)
NO_WORDS = (
    "не", "нема", "откажувам", "одјавувам",
    "ne", "nema", "otkazuvam", "odjavuvam",
    "no", "not", "won't", "don't", "can't", "cannot",
)


def _phrase_pattern(phrase: str, prefix: bool) -> str:
    """Regex for a phrase at a word boundary; with prefix=True it may continue ("лабораториск" -> "лабораториски")."""
    parts = normalize(phrase).split()
    pattern = ""
    for i, part in enumerate(parts):
        if i:
            pattern += r"\s*" if not parts[i - 1][-1].isalnum() else r"\s+"
        pattern += re.escape(part)
    return r"(?<!\w)" + pattern + ("" if prefix else r"(?!\w)")


@lru_cache(maxsize=256)
def _compile(phrases: tuple[str, ...], prefix: bool) -> re.Pattern[str] | None:
    patterns = [_phrase_pattern(p, prefix) for p in phrases if p.strip()]
    return re.compile("|".join(patterns)) if patterns else None


def find_phrases(text: str, phrases: Iterable[str], prefix: bool = False) -> list[str]:
    """Return the phrases that occur in text (whole words, case-insensitive)."""
    text = normalize(text)
    return [p for p in phrases if p.strip() and _compile((p,), prefix).search(text)]


def mentions(text: str, phrases: Iterable[str], prefix: bool = False) -> bool:
    pattern = _compile(tuple(phrases), prefix)
    return bool(pattern and pattern.search(normalize(text)))


def is_yes(label: str) -> bool:
    return mentions(label, YES_WORDS) and not mentions(label, NO_WORDS)


def email_hints(text: str, keywords: Mapping[str, Iterable[str]]) -> frozenset[FormType]:
    """Form types whose keywords appear in the email. Empty means not a form email."""
    hints = set()
    for form_type in (FormType.MIDTERM, FormType.LAB):
        if mentions(text, tuple(keywords.get(form_type.value, ())), prefix=True):
            hints.add(form_type)
    return frozenset(hints)


def describe_hints(hints: frozenset[FormType]) -> str:
    if not hints:
        return FormType.IGNORE.value
    if len(hints) == 1:
        return next(iter(hints)).value
    return "midterm/lab?"


def keyword_analysis(subject: str, hints: frozenset[FormType], has_form: bool) -> Analysis:
    """Without Gemini: the category from the keywords, and the subject as the phone message."""
    category = Category.OTHER
    if has_form:
        category = {FormType.LAB: Category.LAB_FORM, FormType.MIDTERM: Category.MIDTERM_FORM}.get(
            next(iter(hints)) if len(hints) == 1 else None, Category.OTHER_FORM
        )
    return Analysis(category, " ".join(without_course(subject).split())[:200], source="keywords")


def course_code(subject: str) -> str:
    """The course's short name from a Moodle subject, without the school year: "Аипс". "" if there is none."""
    m = _COURSE_RE.match(subject)
    return m.group(1) if m and m.group(1).casefold() not in _NOT_COURSES else ""


def without_course(subject: str) -> str:
    """The subject without the course's short name (that is shown on its own)."""
    return _COURSE_RE.sub("", subject, count=1) if course_code(subject) else subject


def options_shape(options: Iterable[Option]) -> FormType | None:
    """LAB if the options read as day+time slots, MIDTERM if they are yes/no answers."""
    options = list(options)
    if not options:
        return None
    slots = sum(parse_slot(o.label) is not None for o in options)
    if slots and slots * 2 >= len(options):
        return FormType.LAB
    if not slots and any(is_yes(o.label) for o in options):
        return FormType.MIDTERM
    return None


def confirm_type(hints: frozenset[FormType], options: Iterable[Option]) -> tuple[FormType | None, str]:
    """Combine the email hint with the option shape. None means: don't touch it."""
    shape = options_shape(options)
    if shape is None:
        return None, "options are neither day/time slots nor yes/no answers"
    if hints and shape not in hints:
        return None, f"email looks like {describe_hints(hints)} but the options look like {shape.value}"
    return shape, ""
