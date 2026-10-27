"""Pick the option to submit for a midterm or lab form. Pure: works on plain dataclasses."""
from __future__ import annotations

from collections.abc import Iterable

from .classifier import confirm_type, find_phrases, is_yes, mentions
from .models import Action, Decision, FormPage, FormType, Option, PageStatus, Profile, Slot
from .schedule import clashing, fmt_time, normalize, parse_slot

# The faculty's own name contains "компјутерско инженерство", which is also a study program.
_FACULTY_NAMES = ("информатички науки и компјутерско инженерство", "informatički nauki i kompjutersko inženerstvo")


def _strip_faculty_name(text: str) -> str:
    text = normalize(text)
    for name in _FACULTY_NAMES:
        text = text.replace(normalize(name), " ")
    return text


def decide_midterm(options: Iterable[Option], context: str, profile: Profile) -> Decision:
    options = list(options)
    labels = " \n".join(o.label for o in options)
    text = _strip_faculty_name(f"{context}\n{labels}")
    mine = find_phrases(text, profile.program_aliases)
    others = find_phrases(text, profile.other_programs)
    if others and not mine:
        return Decision(Action.SKIP, FormType.MIDTERM, reason=f"for other program(s): {', '.join(others)}")

    yes = [o for o in options if not o.disabled and is_yes(o.label)]
    programs = profile.program_aliases + profile.other_programs
    if any(mentions(o.label, programs) for o in options):
        # Options are per program: take the yes for mine, else a yes that names no program.
        picks = [o for o in yes if mentions(o.label, profile.program_aliases)]
        if not picks:
            picks = [o for o in yes if not mentions(o.label, programs)]
    else:
        picks = yes

    if len(picks) == 1:
        return Decision(Action.SUBMIT, FormType.MIDTERM, picks[0], reason="yes")
    if not picks:
        return Decision(Action.NEEDS_USER, FormType.MIDTERM, reason="no yes option for your program")
    return Decision(
        Action.NEEDS_USER, FormType.MIDTERM,
        reason="several yes options: " + " | ".join(o.label for o in picks),
    )


def _describe(slot: Slot) -> str:
    return f"{slot.label} ({fmt_time(slot.start)}-{fmt_time(slot.end)})"


def decide_lab(options: Iterable[Option], busy: list[Slot], profile: Profile) -> Decision:
    notes = []
    candidates: list[tuple[Slot, Option]] = []
    for option in options:
        if option.disabled:
            continue
        slot = parse_slot(option.label)
        if slot is None:
            notes.append(f"can't read a day/time in {option.label!r}")
            continue
        clash = clashing(slot, busy, profile.min_gap_minutes)
        if clash:
            notes.append(f"{option.label!r} clashes with {clash[0].label!r}")
            continue
        candidates.append((slot, option))

    if not candidates:
        return Decision(Action.NEEDS_USER, FormType.LAB, reason="no free slot", notes=tuple(notes))

    if all(slot.day for slot, _ in candidates):
        candidates.sort(key=lambda c: (c[0].day, c[0].start))
    else:
        candidates.sort(key=lambda c: (c[0].weekday, c[0].start))
    if profile.prefer == "latest":
        candidates.reverse()
    slot, option = candidates[0]
    return Decision(Action.SUBMIT, FormType.LAB, option, reason=f"free: {_describe(slot)}", notes=tuple(notes))


def decide_form(
    hints: frozenset[FormType],
    page: FormPage,
    email_text: str,
    profile: Profile,
    busy: list[Slot],
) -> Decision:
    """Everything after the page has been read: status checks, type check, then the pick."""
    if page.already_answered:
        return Decision(Action.SKIP, reason="already answered")
    if page.status is PageStatus.NOT_OPEN_YET:
        return Decision(Action.RETRY, reason="form is not open yet")
    if page.status is PageStatus.CLOSED:
        return Decision(Action.SKIP, reason="form is closed")

    form_type, why = confirm_type(hints, page.options)
    if form_type is None:
        return Decision(Action.NEEDS_USER, reason=why)
    if form_type is FormType.MIDTERM:
        return decide_midterm(page.options, f"{page.title}\n{page.intro}\n{email_text}", profile)
    return decide_lab(page.options, busy, profile)
