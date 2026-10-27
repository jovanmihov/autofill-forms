from datetime import datetime

import pytest

from autoform.browser.outlook_web import parse_list_date


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Mon 10/26/2026 10:20 AM", datetime(2026, 10, 26, 10, 20)),
        ("Sun 10/25/2026 11:51 PM", datetime(2026, 10, 25, 23, 51)),
        ("Fri 10/23/2026 12:05 AM", datetime(2026, 10, 23, 0, 5)),
        ("Fri 10/23/2026 12:05 PM", datetime(2026, 10, 23, 12, 5)),
        ("пон 26/10/2026 10:20", datetime(2026, 10, 26, 10, 20)),
        ("noreply@courses.example.edu", None),
        ("13/13/2026 10:00", None),
    ],
)
def test_parse_list_date(text, expected):
    assert parse_list_date(text) == expected
