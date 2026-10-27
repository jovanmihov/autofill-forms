from datetime import date

import pytest

from autoform.models import Slot
from autoform.schedule import clashes, clashing, parse_schedule, parse_slot

MON, TUE, WED, THU, FRI = range(5)


def hm(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


@pytest.mark.parametrize(
    "label, weekday, start, end",
    [
        ("Понеделник 08:00-10:00 Лаб 215", MON, "08:00", "10:00"),
        ("12.11.2026 (Четврток) 10-12", THU, "10:00", "12:00"),
        ("12.11.2026 10-12", THU, "10:00", "12:00"),
        ("Среда 10:15–12:00", WED, "10:15", "12:00"),
        ("Термин 3: сре. 14-16, лаб. 117", WED, "14:00", "16:00"),
        ("Во средата од 12 до 14 часот", WED, "12:00", "14:00"),
        ("Petok 08.00 - 10.00", FRI, "08:00", "10:00"),
        ("Tue 16:00-18:00 (group B)", TUE, "16:00", "18:00"),
        ("Група 3 - Вторник 10-12", TUE, "10:00", "12:00"),
        ("Група 1-2, Вторник 10:00-12:00", TUE, "10:00", "12:00"),
        ("2026-11-12 13:00-15:00", THU, "13:00", "15:00"),
    ],
)
def test_parse_slot(label, weekday, start, end):
    slot = parse_slot(label)
    assert slot is not None, label
    assert (slot.weekday, slot.start, slot.end) == (weekday, hm(start), hm(end))


def test_parse_slot_keeps_date():
    assert parse_slot("12.11.2026 10-12").day == date(2026, 11, 12)


@pytest.mark.parametrize(
    "label",
    [
        "12.11.2026 (Среда) 10-12",       # 12.11.2026 is a Thursday: don't guess
        "Понеделник или Среда 10-12",      # two days
        "Понеделник 10-12 или 14-16",      # two time ranges
        "Понеделник",                      # no time
        "10:00-12:00",                     # no day
        "Да, ќе присуствувам",
        "Не",
        "Лаб 215",
        "Понеделник 25:00-26:00",
        "Понеделник 12-10",
    ],
)
def test_parse_slot_rejects_unclear_labels(label):
    assert parse_slot(label) is None


def test_parse_schedule_skips_comments_and_blank_lines():
    text = """
    # my week
    Понеделник 08:00-10:00 Веб програмирање
    Среда 12:00-14:00 Оперативни системи   # room 315

    """
    busy = parse_schedule(text)
    assert [(s.weekday, s.start, s.end) for s in busy] == [(MON, hm("08:00"), hm("10:00")), (WED, hm("12:00"), hm("14:00"))]
    assert busy[0].label == "Понеделник 08:00-10:00 Веб програмирање"


def test_parse_schedule_reports_bad_line():
    with pytest.raises(ValueError, match="line 2"):
        parse_schedule("Понеделник 08:00-10:00 Веб\nПонеделник осум до десет")


def slot(weekday, start, end, day=None):
    return Slot(weekday, hm(start), hm(end), day)


def test_clashes_overlap_and_touching():
    lecture = slot(MON, "10:00", "12:00")
    assert clashes(slot(MON, "11:00", "13:00"), lecture)
    assert clashes(slot(MON, "09:00", "10:30"), lecture)
    assert not clashes(slot(MON, "12:00", "14:00"), lecture)  # back to back is fine
    assert not clashes(slot(TUE, "10:00", "12:00"), lecture)


def test_clashes_with_gap():
    lecture = slot(MON, "10:00", "12:00")
    assert clashes(slot(MON, "12:00", "14:00"), lecture, gap_minutes=15)
    assert not clashes(slot(MON, "12:15", "14:00"), lecture, gap_minutes=15)
    assert clashes(slot(MON, "08:00", "09:50"), lecture, gap_minutes=15)


def test_clashes_dated_entries_only_on_that_date():
    one_off = slot(THU, "10:00", "12:00", date(2026, 11, 12))
    assert clashes(slot(THU, "10:00", "12:00", date(2026, 11, 12)), one_off)
    assert not clashes(slot(THU, "10:00", "12:00", date(2026, 11, 19)), one_off)
    assert clashes(slot(THU, "10:00", "12:00"), one_off)  # weekly slot also hits that Thursday


def test_clashing_lists_the_busy_entries():
    busy = parse_schedule("Понеделник 08:00-10:00 Веб\nПонеделник 09:00-11:00 ОС")
    assert [b.label for b in clashing(slot(MON, "09:30", "10:30"), busy)] == [
        "Понеделник 08:00-10:00 Веб",
        "Понеделник 09:00-11:00 ОС",
    ]
