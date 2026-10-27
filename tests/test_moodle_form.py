"""moodle_form against pages shaped like Moodle 4.5's Choice activity, in headless Edge."""
import pytest
from playwright.sync_api import Error, sync_playwright

from autoform.browser.moodle_form import read_form, submit_choice
from autoform.models import Option, PageStatus

BASE = "https://courses.example.edu"
URL = f"{BASE}/mod/choice/view.php?id=7"


def choice_page(options=(), *, submit=True, selection=None, not_open=False, multiple=False):
    """options: (label, full, checked) tuples."""
    name, kind = ("answer[]", "checkbox") if multiple else ("answer", "radio")
    items = ""
    for i, (label, full, checked) in enumerate(options, start=1):
        attrs = (" disabled" if full else "") + (" checked" if checked else "")
        items += (
            f'<li class="option mr-3"><input type="{kind}" name="{name}" value="{100 + i}" id="choice_{i}" class="mx-1"{attrs}>'
            f'<label for="choice_{i}">{label}{" (Full)" if full else ""}<br>Responses: 3<br>Limit: 20</label></li>'
        )
    button = '<input type="submit" value="Save my choice" class="btn btn-primary">' if submit else ""
    form = (
        f'<form method="POST" action="{BASE}/mod/choice/view.php" class="vertical">'
        f'<ul class="choices list-unstyled unstyled">{items}</ul>'
        f'<input type="hidden" name="action" value="makechoice"><input type="hidden" name="id" value="7">{button}</form>'
        if options else ""
    )
    selected = f'<div id="yourselection" class="box py-3 generalbox">Your selection: {selection}</div>' if selection else ""
    notice = '<div class="box py-3 generalbox notopenyet">This activity is not open yet.</div>' if not_open else ""
    return (
        '<html><body><h1>Веб програмирање</h1><div id="region-main">'
        '<h2>Анкета за избор на термин</h2><div class="activity-description"><p>Изберете термин за лаб. вежби.</p></div>'
        f'<div role="main">{notice}{selected}{form}</div></div>'
        '<nav><a href="/mod/choice/view.php?id=99">Друга анкета</a></nav></body></html>'
    )


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
def page(browser):
    page = browser.new_page()
    yield page
    page.close()


def test_open_form(page):
    page.set_content(choice_page([("Понеделник 08:00-10:00", False, False), ("Вторник 10-12", True, False), ("Среда 12-14", False, False)]))
    form = read_form(page)
    assert form.status is PageStatus.OPEN
    assert form.title == "Анкета за избор на термин"
    assert "лаб. вежби" in form.intro
    assert [o.label for o in form.options] == ["Понеделник 08:00-10:00", "Вторник 10-12 (Full)", "Среда 12-14"]
    assert [o.disabled for o in form.options] == [False, True, False]
    assert form.options[0] == Option(value="101", label="Понеделник 08:00-10:00", input_name="answer")
    assert not form.already_answered


def test_full_label_without_disabled_attribute_counts_as_full(page):
    page.set_content(choice_page([("Пон 08-10", False, False)]).replace("08-10<br>", "08-10 (Полно)<br>"))
    assert read_form(page).options[0].disabled


def test_multiple_answers_use_checkboxes(page):
    page.set_content(choice_page([("Пон 08-10", False, False), ("Сре 10-12", False, False)], multiple=True))
    assert {o.input_name for o in read_form(page).options} == {"answer[]"}


def test_already_answered(page):
    page.set_content(choice_page(selection="Среда, 11:00-12:30 во Лаб 200аб"))
    form = read_form(page)
    assert form.already_answered
    assert form.selection == "Среда, 11:00-12:30 во Лаб 200аб"
    assert form.status is PageStatus.CLOSED


def test_answered_but_changeable(page):
    page.set_content(choice_page([("Пон 08-10", False, True), ("Сре 10-12", False, False)]))
    assert read_form(page).already_answered


def test_not_open_yet(page):
    page.set_content(choice_page(not_open=True))
    assert read_form(page).status is PageStatus.NOT_OPEN_YET


def test_preview_without_save_button_is_not_open_yet(page):
    page.set_content(choice_page([("Пон 08-10", True, False)], submit=False))
    assert read_form(page).status is PageStatus.NOT_OPEN_YET


def test_closed_without_answer(page):
    page.set_content(choice_page())
    form = read_form(page)
    assert form.status is PageStatus.CLOSED
    assert not form.already_answered


def fake_moodle(page, options, accept=True):
    """Serve the choice page; a POST saves the answer the way Moodle would."""
    saved = {}

    def handle(route):
        request = route.request
        if request.method == "POST" and accept:
            value = dict(p.split("=") for p in request.post_data.split("&"))["answer"]
            saved["label"] = next(label for i, (label, _, _) in enumerate(options, start=101) if str(i) == value)
        body = choice_page(selection=saved["label"]) if saved else choice_page(options)
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    page.route(f"{BASE}/**", handle)
    return saved


def test_submit_choice_selects_saves_and_confirms(page):
    options = [("Понеделник 08:00-10:00", False, False), ("Среда 12-14", False, False)]
    saved = fake_moodle(page, options)
    page.goto(URL)
    target = next(o for o in read_form(page).options if o.label == "Среда 12-14")
    assert submit_choice(page, URL, target)
    assert saved == {"label": "Среда 12-14"}


def test_submit_choice_reports_when_moodle_did_not_save(page):
    options = [("Понеделник 08:00-10:00", False, False)]
    fake_moodle(page, options, accept=False)
    page.goto(URL)
    assert not submit_choice(page, URL, read_form(page).options[0])
