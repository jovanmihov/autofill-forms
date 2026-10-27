from autoform.decide import decide_form, decide_lab, decide_midterm
from autoform.models import Action, FormPage, FormType, Option, PageStatus, Profile
from autoform.schedule import parse_schedule

PROFILE = Profile(
    program_aliases=("ПИТ", "Примена на информациски технологии"),
    other_programs=("СИИС", "КН", "ИМБ", "КЕ", "Компјутерско инженерство"),
)
BUSY = parse_schedule(
    """
    Понеделник 08:00-10:00 Веб програмирање
    Понеделник 10:00-12:00 Бази на податоци
    Среда 12:00-14:00 Оперативни системи
    """
)


def opts(*labels, disabled=()):
    return [Option(value=str(i), label=label, disabled=label in disabled) for i, label in enumerate(labels)]


# --- midterm ---------------------------------------------------------------

def test_midterm_picks_yes():
    d = decide_midterm(opts("Да, ќе присуствувам", "Не"), "Прв колоквиум по Веб програмирање", PROFILE)
    assert d.action is Action.SUBMIT
    assert d.option.label == "Да, ќе присуствувам"


def test_midterm_for_other_program_is_skipped():
    d = decide_midterm(opts("Да", "Не"), "Колоквиум за студентите од СИИС", PROFILE)
    assert d.action is Action.SKIP
    assert "СИИС" in d.reason


def test_midterm_naming_my_program_and_others_goes_ahead():
    d = decide_midterm(opts("Да", "Не"), "Колоквиум за студентите од СИИС и ПИТ", PROFILE)
    assert d.action is Action.SUBMIT


def test_midterm_faculty_name_is_not_a_program():
    context = "Факултет за информатички науки и компјутерско инженерство - колоквиум"
    assert decide_midterm(opts("Да", "Не"), context, PROFILE).action is Action.SUBMIT


def test_midterm_per_program_options_picks_mine():
    d = decide_midterm(opts("Да - СИИС", "Да - ПИТ", "Не"), "", PROFILE)
    assert d.action is Action.SUBMIT
    assert d.option.label == "Да - ПИТ"


def test_midterm_per_program_options_without_mine_is_skipped():
    d = decide_midterm(opts("Да - СИИС", "Да - КН", "Не"), "", PROFILE)
    assert d.action is Action.SKIP


def test_midterm_several_yes_needs_user():
    d = decide_midterm(opts("Да, во 10 часот", "Да, во 12 часот", "Не"), "", PROFILE)
    assert d.action is Action.NEEDS_USER


def test_midterm_no_yes_needs_user():
    d = decide_midterm(opts("Не", "Можеби"), "", PROFILE)
    assert d.action is Action.NEEDS_USER


def test_midterm_disabled_yes_is_not_picked():
    d = decide_midterm(opts("Да", "Не", disabled=("Да",)), "", PROFILE)
    assert d.action is Action.NEEDS_USER


# --- lab -------------------------------------------------------------------

def test_lab_picks_earliest_free_slot():
    options = opts(
        "Понеделник 08:00-10:00 Лаб 215",   # clashes with Веб
        "Понеделник 12:00-14:00 Лаб 215",
        "Вторник 08:00-10:00 Лаб 215",
        "Среда 12:00-14:00 Лаб 117",         # clashes with ОС
    )
    d = decide_lab(options, BUSY, PROFILE)
    assert d.action is Action.SUBMIT
    assert d.option.label == "Понеделник 12:00-14:00 Лаб 215"
    assert any("clashes" in note for note in d.notes)


def test_lab_prefer_latest():
    profile = Profile(program_aliases=("ПИТ",), prefer="latest")
    d = decide_lab(opts("Вторник 08-10", "Петок 14-16", "Среда 08-10"), BUSY, profile)
    assert d.option.label == "Петок 14-16"


def test_lab_gap_rules_out_back_to_back():
    profile = Profile(program_aliases=("ПИТ",), min_gap_minutes=15)
    d = decide_lab(opts("Понеделник 12:00-14:00", "Вторник 08:00-10:00"), BUSY, profile)
    assert d.option.label == "Вторник 08:00-10:00"


def test_lab_dated_slots_sorted_by_date():
    d = decide_lab(opts("19.11.2026 08-10", "13.11.2026 14-16", "16.11.2026 12-14"), BUSY, PROFILE)
    assert d.option.label == "13.11.2026 14-16"


def test_lab_skips_disabled_and_unreadable():
    options = opts("Вторник 08-10 (Полно)", "12.11.2026 (Среда) 10-12", "Петок 10-12", disabled=("Вторник 08-10 (Полно)",))
    d = decide_lab(options, BUSY, PROFILE)
    assert d.option.label == "Петок 10-12"
    assert any("can't read" in note for note in d.notes)


def test_lab_no_free_slot_needs_user():
    d = decide_lab(opts("Понеделник 08:00-10:00", "Понеделник 10-12"), BUSY, PROFILE)
    assert d.action is Action.NEEDS_USER
    assert d.option is None


# --- whole form ------------------------------------------------------------

LAB = frozenset({FormType.LAB})
MIDTERM = frozenset({FormType.MIDTERM})


def page(*labels, **kwargs):
    return FormPage(url="https://courses.example/mod/choice/view.php?id=1", options=tuple(opts(*labels)), **kwargs)


def test_form_lab():
    d = decide_form(LAB, page("Понеделник 08-10", "Вторник 08-10"), "", PROFILE, BUSY)
    assert (d.action, d.form_type, d.option.label) == (Action.SUBMIT, FormType.LAB, "Вторник 08-10")


def test_form_midterm_uses_email_text_for_program():
    d = decide_form(MIDTERM, page("Да", "Не"), "Колоквиум за студентите од КН", PROFILE, BUSY)
    assert d.action is Action.SKIP


def test_form_type_mismatch_needs_user():
    d = decide_form(MIDTERM, page("Понеделник 08-10", "Вторник 08-10"), "", PROFILE, BUSY)
    assert d.action is Action.NEEDS_USER
    assert d.option is None


def test_form_already_answered():
    assert decide_form(LAB, page("Вторник 08-10", already_answered=True), "", PROFILE, BUSY).action is Action.SKIP


def test_form_not_open_yet_is_retried():
    d = decide_form(LAB, page(status=PageStatus.NOT_OPEN_YET), "", PROFILE, BUSY)
    assert d.action is Action.RETRY


def test_form_closed_is_skipped():
    assert decide_form(LAB, page(status=PageStatus.CLOSED), "", PROFILE, BUSY).action is Action.SKIP
