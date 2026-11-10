"""Pick the option to submit for a midterm or lab form. Pure: works on plain dataclasses."""
from __future__ import annotations

from collections.abc import Iterable

from .classifier import confirm_type, find_phrases, is_yes, mentions
from .models import Action, Analysis, Category, Decision, FormPage, FormType, Option, PageStatus, Profile, Slot
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
    programs = profile.program_aliases + profile.other_programs
    per_program = any(mentions(o.label, programs) for o in options)
    labels = " \n".join(o.label for o in options)
    # Another program in an option ("I will take the exam (SEIS)") only says that option isn't mine,
    # so whether the midterm is for other programs alone is read from the title, intro and email.
    mine = find_phrases(_strip_faculty_name(f"{context}\n{labels}"), profile.program_aliases)
    others = find_phrases(_strip_faculty_name(context), profile.other_programs)
    if others and not mine:
        return Decision(Action.SKIP, FormType.MIDTERM, reason=f"for other program(s): {', '.join(others)}")

    yes = [o for o in options if not o.disabled and is_yes(o.label)]
    if per_program:
        # Options are per program: take the yes for mine, else a yes that names no program.
        picks = [o for o in yes if mentions(o.label, profile.program_aliases)]
        if not picks:
            picks = [o for o in yes if not mentions(o.label, programs)]
        if not picks and yes:
            named = find_phrases(" \n".join(o.label for o in yes), profile.other_programs)
            return Decision(Action.SKIP, FormType.MIDTERM, reason=f"for other program(s): {', '.join(named)}")
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


def lab_candidates(
    options: Iterable[Option], busy: list[Slot], profile: Profile,
) -> tuple[list[tuple[Slot, Option]], dict[str, str]]:
    """The free slots, best first by profile.prefer, and why each other option was left out (by value)."""
    rejected = {}
    candidates: list[tuple[Slot, Option]] = []
    for option in options:
        if option.disabled:
            rejected[option.value] = f"{option.label!r} is full or closed"
            continue
        slot = parse_slot(option.label)
        if slot is None:
            rejected[option.value] = f"can't read a day/time in {option.label!r}"
            continue
        clash = clashing(slot, busy, profile.min_gap_minutes)
        if clash:
            rejected[option.value] = f"{option.label!r} clashes with {clash[0].label!r}"
            continue
        candidates.append((slot, option))

    if all(slot.day for slot, _ in candidates):
        candidates.sort(key=lambda c: (c[0].day, c[0].start))
    else:
        candidates.sort(key=lambda c: (c[0].weekday, c[0].start))
    if profile.prefer == "latest":
        candidates.reverse()
    return candidates, rejected


def decide_lab(options: Iterable[Option], busy: list[Slot], profile: Profile) -> Decision:
    options = list(options)
    candidates, rejected = lab_candidates(options, busy, profile)
    notes = tuple(rejected[o.value] for o in options if o.value in rejected and not o.disabled)
    if not candidates:
        return Decision(Action.NEEDS_USER, FormType.LAB, reason="no free slot", notes=notes)
    slot, option = candidates[0]
    return Decision(Action.SUBMIT, FormType.LAB, option, reason=f"free: {_describe(slot)}", notes=notes)


def page_decision(page: FormPage) -> Decision | None:
    """The checks before any pick. None means the form is open and not answered yet."""
    if page.already_answered:
        return Decision(Action.SKIP, reason="already answered")
    if page.status is PageStatus.NOT_OPEN_YET:
        return Decision(Action.RETRY, reason="form is not open yet")
    if page.status is PageStatus.CLOSED:
        return Decision(Action.SKIP, reason="form is closed")
    return None


def decide_form(
    hints: frozenset[FormType],
    page: FormPage,
    email_text: str,
    profile: Profile,
    busy: list[Slot],
) -> Decision:
    """Everything after the page has been read: status checks, type check, then the pick."""
    if before := page_decision(page):
        return before

    form_type, why = confirm_type(hints, page.options)
    if form_type is None:
        return Decision(Action.NEEDS_USER, reason=why)
    if form_type is FormType.MIDTERM:
        return decide_midterm(page.options, f"{page.title}\n{page.intro}\n{email_text}", profile)
    return decide_lab(page.options, busy, profile)


_FORM_TYPES = {Category.LAB_FORM: FormType.LAB, Category.MIDTERM_FORM: FormType.MIDTERM}


def check_suggestion(
    analysis: Analysis,
    page: FormPage,
    email_text: str,
    profile: Profile,
    busy: list[Slot],
) -> Decision:
    """Gemini's suggested option, submitted only if it passes the same checks as decide_form.

    Anything else (another kind of form, an option that isn't on the page, a slot that clashes
    or is full, a midterm answer the program check doesn't give) is left to the user.
    """
    if before := page_decision(page):
        return before
    form_type = _FORM_TYPES.get(analysis.category)
    if form_type is None:
        what = analysis.category.value.replace("_", " ")
        return Decision(Action.NEEDS_USER, reason=f"Gemini says this is {what}, not a lab or midterm form")
    confirmed, why = confirm_type(frozenset({form_type}), page.options)
    if confirmed is None:
        return Decision(Action.NEEDS_USER, form_type, reason=why)

    by_value = {o.value: o for o in page.options}
    suggested = by_value.get(analysis.option_value) if analysis.option_value is not None else None
    if analysis.option_value is not None and suggested is None:
        return Decision(Action.NEEDS_USER, form_type, reason="Gemini picked an option that isn't on the form")
    said = f"Gemini suggests {suggested.label!r}" if suggested else "Gemini suggests not answering"

    if form_type is FormType.MIDTERM:
        rule = decide_midterm(page.options, f"{page.title}\n{page.intro}\n{email_text}", profile)
        if rule.action is Action.SUBMIT and suggested == rule.option:
            return rule
        if rule.action is Action.SKIP and suggested is None:
            return rule
        if rule.action is Action.NEEDS_USER:
            return rule  # the checks can't confirm any answer, so Gemini's alone isn't enough
        checks = f"the checks say {rule.option.label!r}" if rule.option else f"the checks say skip ({rule.reason})"
        return Decision(Action.NEEDS_USER, form_type, reason=f"{said}, {checks}")

    candidates, rejected = lab_candidates(page.options, busy, profile)
    if suggested is None:
        free = ", ".join(repr(o.label) for _, o in candidates) or "none"
        return Decision(Action.NEEDS_USER, form_type, reason=f"{said}; free by your schedule: {free}")
    if suggested.value in rejected:
        return Decision(Action.NEEDS_USER, form_type, reason=f"{said}, but {rejected[suggested.value]}")
    slot = next(s for s, o in candidates if o == suggested)
    notes = ()
    if candidates[0][1] != suggested:
        notes = (f"prefer: {profile.prefer} would pick {candidates[0][1].label!r}",)
    return Decision(Action.SUBMIT, form_type, suggested, reason=f"free: {_describe(slot)}", notes=notes)
