"""main.Runner end to end in headless Edge, with a fake Outlook, a fake Moodle and a fake Gemini.
Notifications are recorded instead of shown or sent; preferences are the made-up example ones."""
import json
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error, sync_playwright

import main
from autoform.config import EXAMPLE_DIR, load_config, load_profile, load_schedule
from autoform.gemini import Gemini
from test_moodle_form import choice_page

OUTLOOK = "https://outlook.example.test"
MOODLE = "https://courses.example.edu"
FORM = f"{MOODLE}/mod/choice/view.php?id=7"
# The example schedule is busy on Monday 08-12, so 101 clashes and 102 is free.
OPTIONS = [("Понеделник 08:00-10:00", False, False), ("Вторник 08:00-10:00", False, False)]

LAB_EMAIL = ("lab", "Вп-2026/2027/Z-49_1: Анкета за термин за лабораториски вежби",
             f'Изберете термин: <a href="{FORM}">анкета</a>')
EVENT_EMAIL = ("event", "Предавање за вештачка интелигенција", "Бесплатно предавање на 18-ти во 17 часот.")


def outlook_page(*emails):
    """emails: (conv id, subject, body[, sender name]); the sender name is "Teacher (via COURSES)" if not given."""
    rows = "".join(
        f'<div role="option" data-convid="{conv}" style="display:block;padding:20px 20px 20px 60px">'
        f'<span title="noreply@courses.example.edu">{name[0] if name else "Teacher (via COURSES)"}</span> {subject}'
        f'<span title="Mon 10/26/2026 10:20 AM">10:20</span></div>'
        for conv, subject, _, *name in emails
    )
    bodies = json.dumps({conv: [subject, body] for conv, subject, body, *_ in emails})
    return f"""<html><body><div role="listbox">{rows}</div><div id="pane"></div><script>
    const bodies = {bodies};
    document.querySelectorAll('[role=option]').forEach(row => row.addEventListener('click', () => {{
      const [subject, body] = bodies[row.dataset.convid];
      history.pushState({{}}, '', '/mail/inbox/id/' + row.dataset.convid);
      document.querySelector('#pane').innerHTML = `<div role="heading" id="m_SUBJECT" title="${{subject}}"></div>`
        + `<div id="UniqueMessageBody_1">${{body}}</div>`;
    }}));
    </script></body></html>"""


class FakeModels:
    """Replies in order; records which request (email or form) each one answered."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked = []

    def generate_content(self, *, model, contents, config):
        self.asked.append("form" if contents.startswith("<form>") else "email")
        answer = self.replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(text=json.dumps(answer))


def gemini(*replies):
    return Gemini("test-key", ["model-a"], client=SimpleNamespace(models=FakeModels(*replies)))


class Posts(list):
    """The option values posted to Moodle; .visited has every Moodle URL requested."""

    def __init__(self):
        super().__init__()
        self.visited = []


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
def run(browser, tmp_path, monkeypatch):
    """run(emails, gemini[, form]) checks the fake Inbox once; returns (notifications, Moodle POSTs, state).
    form is the Moodle page to show instead of an open form with OPTIONS; setup(runner) runs before the check.
    The Moodle URLs that were opened are in posts.visited."""
    monkeypatch.setattr(main, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(main, "FORMS_DIR", tmp_path / "forms")
    monkeypatch.setattr(main, "load_profile", lambda: load_profile(EXAMPLE_DIR))
    monkeypatch.setattr(main, "load_schedule", lambda: load_schedule(EXAMPLE_DIR))
    monkeypatch.setattr(main, "check_not_example", lambda: None)
    notes = []
    monkeypatch.setattr(main, "notify", lambda title, message, link=None: notes.append((title, message, link)))
    config = replace(load_config(), moodle_url=MOODLE, outlook_url=f"{OUTLOOK}/mail/", keep_unread=False)

    def check(emails, ai, form=None, setup=None):
        monkeypatch.setattr(main, "make_gemini", lambda config: ai)
        posts = Posts()

        def moodle(route):
            posts.visited.append(route.request.url)
            if route.request.method == "POST":
                value = dict(p.split("=") for p in route.request.post_data.split("&"))["answer"]
                posts.append(value)
            label = next((label for i, (label, _, _) in enumerate(OPTIONS, 101) if posts and str(i) == posts[-1]), None)
            body = choice_page(selection=label) if label else form or choice_page(OPTIONS)
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

        context = browser.new_context()
        context.route(f"{OUTLOOK}/**", lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=outlook_page(*emails)))
        context.route(f"{MOODLE}/**", moodle)
        try:
            runner = main.Runner(config, context, dry_run=False)
            if setup:
                setup(runner)
            runner.open_outlook()
            runner.check_mail(datetime.now() - timedelta(days=36500), max_scrolls=0)
        finally:
            context.close()
        return notes, posts, runner.state

    return check


LAB_SUMMARY = {"category": "lab_form", "message": "Lab term survey", "reason": "asks for a term"}


def test_gemini_pick_that_passes_the_checks_is_submitted(run):
    ai = gemini(LAB_SUMMARY, {"form": "lab_form", "option": "102", "reason": "free",
                              "message": "Lab term survey for Web Programming"})
    notes, posts, state = run([LAB_EMAIL], ai)
    assert ai.client().models.asked == ["email", "form"]
    assert posts == ["102"]
    assert notes == [("Lab · Вп · Teacher", "Lab term survey for Web Programming. I chose Вторник 08:00-10:00 for you.", FORM)]
    assert state.email_status("lab") == "submitted" and state.activity_done(FORM)


def test_gemini_pick_that_clashes_is_not_submitted(run):
    ai = gemini(LAB_SUMMARY, {"form": "lab_form", "option": "101", "reason": "earliest", "message": "Lab survey"})
    notes, posts, state = run([LAB_EMAIL], ai)
    assert posts == []
    [(title, message, link)] = notes
    assert title == "Needs you · Вп · Teacher" and "clashes" in message and link == FORM
    assert state.email_status("lab") == "needs_user"


def test_gemini_failure_means_no_submit(run):
    ai = gemini(RuntimeError("429 RESOURCE_EXHAUSTED"), RuntimeError("429 RESOURCE_EXHAUSTED"))
    notes, posts, _ = run([LAB_EMAIL], ai)
    assert posts == []
    [(title, message, _)] = notes
    assert title == "Needs you · Вп · Teacher" and "Gemini" in message
    assert message.startswith("Анкета за термин")  # the keyword fallback named the email, without the course code


def test_email_without_a_form_is_summed_up(run):
    ai = gemini({"category": "event", "message": "Free AI talk on the 18th at 17:00", "reason": "talk"})
    notes, posts, state = run([EVENT_EMAIL], ai)
    assert ai.client().models.asked == ["email"]
    assert notes == [("Event · Teacher", "Free AI talk on the 18th at 17:00", None)]
    assert posts == [] and state.email_done("event")


def test_without_a_gemini_key_the_keywords_and_rules_decide(run):
    notes, posts, _ = run([LAB_EMAIL, EVENT_EMAIL], None)
    assert posts == ["102"]
    assert ("Course email · Teacher", "Предавање за вештачка интелигенција", None) in notes
    assert any(title == "Lab · Вп · Teacher" and "Вторник 08:00-10:00" in message for title, message, _ in notes)


def test_a_form_that_isnt_open_yet_is_not_asked_about_again(run):
    ai = gemini(LAB_SUMMARY)
    notes, _, state = run([LAB_EMAIL], ai, form=choice_page(not_open=True))
    assert notes[0][0] == "Waiting for a form · Вп · Teacher"
    state.activities[FORM]["next_check"] = "2000-01-01T00:00:00"
    state.save()
    _, _, state = run([LAB_EMAIL], ai, form=choice_page(not_open=True))
    assert ai.client().models.asked == ["email"]  # the form was looked at again, the email not re-asked
    assert state.email_status("lab") == "retry"


def test_a_closed_moodle_tab_is_opened_again(run):
    ai = gemini(LAB_SUMMARY, {"form": "lab_form", "option": "102", "reason": "free", "message": "Lab survey"})
    _, posts, _ = run([LAB_EMAIL], ai, setup=lambda runner: runner.moodle.close())
    assert posts == ["102"]


def test_a_forum_post_that_only_mentions_a_midterm_is_not_opened(run):
    notice = ("notice", "SIC: Одлука", '... за време на испит и колоквиум. '
              f'<a href="{MOODLE}/mod/forum/discuss.php?d=5">See this post in context</a>')
    ai = gemini({"category": "announcement", "message": "SIC announcement about a decision", "reason": "news"})
    notes, posts, _ = run([notice], ai)
    assert posts.visited == []
    assert notes == [("Announcement · SIC · Teacher", "SIC announcement about a decision", f"{MOODLE}/mod/forum/discuss.php?d=5")]


def test_moodles_own_notices_have_no_sender_in_the_title(run):
    notice = ("signin", "New sign in to your account", "Hi [student], your account was just signed in to.",
              "Do not reply to this email (via COURSES)")
    ai = gemini({"category": "other", "message": "New login to your Moodle account", "reason": "security notice"})
    notes, _, _ = run([notice], ai)
    assert notes == [("Course email", "New login to your Moodle account", None)]
