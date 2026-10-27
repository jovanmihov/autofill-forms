from autoform.models import Category
from autoform.state import State


def test_email_waiting_for_a_form_that_isnt_open(tmp_path):
    state = State(tmp_path / "state.json")
    url = "https://courses.example.edu/mod/choice/view.php?id=7"
    state.record_retry(url, title="Лаб")
    state.record_email("c1", "retry", subject="Лаб", url=url)
    assert state.email_waiting("c1")
    assert not state.email_done("c1")

    state.activities[url]["next_check"] = "2000-01-01T00:00:00"  # due again
    assert not state.email_waiting("c1")


def test_informed_emails_are_done(tmp_path):
    state = State(tmp_path / "state.json")
    state.record_email("c2", "informed", subject="Предавање")
    state.save()
    assert State(tmp_path / "state.json").email_done("c2")
    assert not state.email_waiting("c2")


def test_email_summary_is_kept_for_the_next_look(tmp_path):
    state = State(tmp_path / "state.json")
    assert state.email_summary("c3") is None
    state.record_email("c3", "retry", category="lab_form", message="Lab term survey", source="gemini")
    state.save()
    summary = State(tmp_path / "state.json").email_summary("c3")
    assert (summary.category, summary.message, summary.source) == (Category.LAB_FORM, "Lab term survey", "gemini")
