import pytest

from autoform.config import EXAMPLE_DIR, SetupError, check_not_example, load_config, load_profile, load_schedule


def test_config_yaml_loads():
    config = load_config()
    assert config.moodle_host == "courses.finki.ukim.mk"
    assert "колоквиум" in config.keywords["midterm"]


def test_example_preferences_load():
    profile = load_profile(EXAMPLE_DIR)
    assert profile.program_aliases
    assert len(load_schedule(EXAMPLE_DIR)) >= 3


def test_example_preferences_are_refused_for_real_runs():
    with pytest.raises(SetupError, match="example"):
        check_not_example(EXAMPLE_DIR, EXAMPLE_DIR)


def test_missing_preferences(tmp_path):
    with pytest.raises(SetupError, match="copy preferences.example"):
        load_profile(tmp_path)


def test_course_senders_and_gemini_settings():
    config = load_config()
    assert config.sender_domain_prefix == "courses."
    assert config.keep_unread is True
    assert config.gemini_models and all(m.startswith("gemini-") for m in config.gemini_models)


def test_example_private_words_are_made_up():
    assert any("example.edu" in word for word in load_profile(EXAMPLE_DIR).private_words)
