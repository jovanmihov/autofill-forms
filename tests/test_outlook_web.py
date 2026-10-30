from datetime import datetime

import pytest
from playwright.sync_api import Error, sync_playwright

from autoform.browser import outlook_web
from autoform.browser.outlook_web import is_course_sender, list_course_emails, parse_list_date, person, read_email


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


@pytest.mark.parametrize(
    "address, expected",
    [
        ("noreply@courses.example.edu", True),
        ("Teacher.Name@COURSES.example.edu", True),
        ("anyone@courses.other.org", True),
        ("noreply@example.edu", False),
        ("noreply@mycourses.example.edu", False),
        ("noreply@courses", False),
        ("write to noreply@courses.example.edu", False),  # a subject or preview, not an address
        ("Mark as read", False),
    ],
)
def test_is_course_sender(address, expected):
    assert is_course_sender(address, "courses.") is expected


@pytest.mark.parametrize(
    "shown, expected",
    [
        ("Georgiev Pece (via COURSES)", "Georgiev Pece"),
        ("Михова  Марија (преку КУРСЕВИ)", "Михова Марија"),
        ("Do not reply to this email (via COURSES)", ""),  # Moodle's own notices
        ("noreply", ""),
        ("Teacher", "Teacher"),
        ("", ""),
    ],
)
def test_person(shown, expected):
    assert person(shown) == expected


# --- a page shaped like Outlook on the web's message list ----------------------

BASE = "https://outlook.example.test"


def row(conv_id, sender, unread, subject="Предмет", name="Sender"):
    toggle = "Mark as read" if unread else "Mark as unread"
    return (
        f'<div role="option" data-convid="{conv_id}" aria-label="{subject}" '
        'style="position:relative;display:block;padding:20px 20px 20px 60px;width:600px">'
        f'<button title="{toggle}" style="position:absolute;left:4px;top:4px;width:8px;height:8px"></button>'
        f'<span title="{sender}">{name}</span> <span title="{subject}">{subject}</span>'
        '<span title="Mon 10/26/2026 10:20 AM">10:20</span></div>'
    )


# Opening a row shows the email and, a moment later, marks it read; the toggle flips read/unread.
OUTLOOK_JS = """<script>
const clicks = window.toggleClicks = [];
document.querySelectorAll('[role=option]').forEach(row => {
  const toggle = row.querySelector('button[title]');
  toggle.addEventListener('click', e => {
    e.stopPropagation();
    clicks.push(row.dataset.convid);
    toggle.title = toggle.title === 'Mark as read' ? 'Mark as unread' : 'Mark as read';
  });
  row.addEventListener('click', () => {
    history.pushState({}, '', '/mail/inbox/id/' + row.dataset.convid);
    document.querySelector('#pane').innerHTML = '<div role="heading" id="m_SUBJECT" title="Предмет"></div>'
      + '<div id="UniqueMessageBody_1">Текст <a href="https://courses.example.edu/mod/choice/view.php?id=7">анкета</a></div>';
    setTimeout(() => { toggle.title = 'Mark as unread'; }, 200);
  });
});
</script>"""


def outlook_page(*rows):
    return f'<html><body><div role="listbox">{"".join(rows)}</div><div id="pane"></div>{OUTLOOK_JS}</body></html>'


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            edge = p.chromium.launch(channel="msedge")
        except Error:
            pytest.skip("Microsoft Edge is not available")
        yield edge
        edge.close()


@pytest.fixture
def outlook(browser):
    page = browser.new_page()

    def serve(*rows):
        page.route(f"{BASE}/**", lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=outlook_page(*rows)))
        page.goto(f"{BASE}/mail/")
        return page

    yield serve
    page.close()


def toggle_title(page, conv_id):
    return page.locator(f'[data-convid="{conv_id}"] button[title]').get_attribute("title")


def test_lists_every_courses_sender(outlook):
    page = outlook(
        row("a", "noreply@courses.example.edu", True),
        row("b", "teacher@courses.other.org", False),
        row("c", "news@example.edu", True, subject="see noreply@courses.example.edu"),
    )
    found = list_course_emails(page, "courses.")
    assert [(e.conv_id, e.sender) for e in found] == [("a", "noreply@courses.example.edu"), ("b", "teacher@courses.other.org")]


def test_read_email_says_who_sent_it(outlook):
    page = outlook(row("a", "noreply@courses.example.edu", False, name="Georgiev Pece (via COURSES)"))
    [item] = list_course_emails(page, "courses.")
    assert item.sender_name == "Georgiev Pece (via COURSES)"
    assert read_email(page, item, 5000).sender == "Georgiev Pece"


def test_unread_email_is_marked_unread_again(outlook):
    page = outlook(row("a", "noreply@courses.example.edu", unread=True))
    [item] = list_course_emails(page, "courses.")
    email = read_email(page, item, 5000, keep_unread=True)
    assert email.links == (("https://courses.example.edu/mod/choice/view.php?id=7", "анкета"),)
    assert toggle_title(page, "a") == "Mark as read"
    assert page.evaluate("window.toggleClicks") == ["a"]


def test_read_email_is_left_alone(outlook, monkeypatch):
    monkeypatch.setattr(outlook_web, "MARK_READ_WAIT_MS", 400)
    page = outlook(row("b", "noreply@courses.example.edu", unread=False))
    [item] = list_course_emails(page, "courses.")
    read_email(page, item, 5000, keep_unread=True)
    assert toggle_title(page, "b") == "Mark as unread"
    assert page.evaluate("window.toggleClicks") == []


def test_without_keep_unread_the_email_stays_read(outlook):
    page = outlook(row("a", "noreply@courses.example.edu", unread=True))
    [item] = list_course_emails(page, "courses.")
    read_email(page, item, 5000, keep_unread=False)
    page.wait_for_timeout(400)
    assert toggle_title(page, "a") == "Mark as unread"
    assert page.evaluate("window.toggleClicks") == []


# After the first email is opened, a card (contact or attachment preview) covers the list, as Outlook
# does when the mouse ends up over a name or attachment. sticky: only Escape closes it.
CARD_JS = """sticky => document.querySelector('[role=option]').addEventListener('click', () =>
  setTimeout(() => {
    const card = document.createElement('div');
    card.id = 'card';
    card.style.cssText = 'position:fixed;inset:0;z-index:9';
    document.body.appendChild(card);
    if (sticky) document.addEventListener('keydown', e => { if (e.key === 'Escape') card.remove(); });
    else document.addEventListener('mousemove', e => { if (e.clientX < 5 && e.clientY < 5) card.remove(); });
  }, 50))"""


@pytest.mark.parametrize("sticky", [False, True])
def test_a_card_over_the_list_doesnt_block_the_next_email(outlook, monkeypatch, sticky):
    monkeypatch.setattr(outlook_web, "CLICK_TIMEOUT_MS", 500)
    page = outlook(row("a", "noreply@courses.example.edu", False), row("b", "noreply@courses.example.edu", False))
    page.evaluate(CARD_JS, sticky)
    first, second = list_course_emails(page, "courses.")
    read_email(page, first, 5000)
    assert page.locator("#card").count() == 1
    read_email(page, second, 5000)
    assert page.url.endswith("/mail/inbox/id/b")
    assert page.locator("#card").count() == 0
