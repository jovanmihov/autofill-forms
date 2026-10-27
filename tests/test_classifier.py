import pytest

from autoform.classifier import confirm_type, email_hints, find_phrases, is_yes, keyword_analysis, options_shape
from autoform.models import Category, FormType, Option

KEYWORDS = {
    "midterm": ["колоквиум", "kolokvium", "midterm", "парцијал", "parcijal"],
    "lab": ["лабораториск", "лаб. вежби", "термин", "lab", "laboratorisk", "termin"],
}


def opts(*labels):
    return [Option(value=str(i), label=label) for i, label in enumerate(labels)]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Анкета за присуство на прв колоквиум", {FormType.MIDTERM}),
        ("Prijava za kolokviumot", {FormType.MIDTERM}),
        ("Пријава за лабораториските вежби", {FormType.LAB}),
        ("Лаб. вежби - избор на група", {FormType.LAB}),
        ("Lab exercises: pick a group", {FormType.LAB}),
        ("Термини за колоквиум", {FormType.MIDTERM, FormType.LAB}),
        ("Нов пост во форумот: материјали за предавање 3", set()),
        ("Collaboration guidelines", set()),  # "lab" only at a word start
    ],
)
def test_email_hints(text, expected):
    assert email_hints(text, KEYWORDS) == expected


@pytest.mark.parametrize(
    "label, expected",
    [
        ("Да, ќе присуствувам", True),
        ("Да", True),
        ("Yes", True),
        ("Ќе присуствувам", True),
        ("Не", False),
        ("Не, нема да присуствувам", False),
        ("Не ќе присуствувам", False),
        ("Дали", False),
        ("Понеделник 08:00-10:00", False),
    ],
)
def test_is_yes(label, expected):
    assert is_yes(label) is expected


def test_find_phrases_is_whole_word_and_case_insensitive():
    assert find_phrases("Колоквиум за студентите од СИИС и ПИТ", ["сиис", "ИТ", "ПИТ"]) == ["сиис", "ПИТ"]


def test_options_shape():
    assert options_shape(opts("Понеделник 08:00-10:00 Лаб 215", "Среда 10-12 Лаб 215")) is FormType.LAB
    assert options_shape(opts("Пон 08-10", "Сре 10-12", "Не можам во ниеден термин")) is FormType.LAB
    assert options_shape(opts("Да, ќе присуствувам", "Не")) is FormType.MIDTERM
    assert options_shape(opts("СИИС", "ПИТ")) is None
    assert options_shape([]) is None


def test_confirm_type_agrees():
    assert confirm_type(frozenset({FormType.LAB}), opts("Пон 08-10", "Сре 10-12")) == (FormType.LAB, "")
    assert confirm_type(frozenset({FormType.MIDTERM}), opts("Да", "Не")) == (FormType.MIDTERM, "")


def test_confirm_type_both_hints_lets_options_decide():
    both = frozenset({FormType.MIDTERM, FormType.LAB})
    assert confirm_type(both, opts("Пон 08-10", "Сре 10-12"))[0] is FormType.LAB


def test_confirm_type_disagreement_is_refused():
    form_type, reason = confirm_type(frozenset({FormType.MIDTERM}), opts("Пон 08-10", "Сре 10-12"))
    assert form_type is None
    assert "midterm" in reason and "lab" in reason


def test_confirm_type_without_email_trusts_options():
    assert confirm_type(frozenset(), opts("Да", "Не"))[0] is FormType.MIDTERM


# --- keyword fallback when Gemini isn't available ----------------------------

def test_keyword_analysis():
    lab = keyword_analysis("Анкета  за термин\nза лаб. вежби", frozenset({FormType.LAB}), has_form=True)
    assert (lab.category, lab.message, lab.source) == (Category.LAB_FORM, "Анкета за термин за лаб. вежби", "keywords")
    both = keyword_analysis("x", frozenset({FormType.LAB, FormType.MIDTERM}), has_form=True)
    assert both.category is Category.OTHER_FORM
    assert keyword_analysis("Предавање", frozenset({FormType.LAB}), has_form=False).category is Category.OTHER
