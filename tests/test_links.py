from autoform.links import activity_links, is_choice, pick_target, unwrap_safelink

BASE = "https://courses.example.edu"
LAB_KEYWORDS = ["лабораториск", "термин", "lab"]


def test_pick_activity_over_forum():
    links = [
        (f"{BASE}/mod/forum/discuss.php?d=45#p7", "See this post in context"),
        (f"{BASE}/mod/choice/view.php?forceview=1&id=123", "анкета"),
    ]
    assert pick_target(links, BASE) == ("activity", f"{BASE}/mod/choice/view.php?id=123")


def test_same_activity_twice_is_one_target():
    links = [(f"{BASE}/mod/choice/view.php?id=5", "анкета"), (f"{BASE}/mod/choice/view.php?id=5", "survey")]
    assert pick_target(links, BASE)[0] == "activity"


def test_several_activities_picks_the_one_named_like_the_form():
    links = [
        (f"{BASE}/mod/choice/view.php?id=10", "анкетата за избор на термин за лабораториски вежби"),
        (f"{BASE}/mod/choice/view.php?id=11", "анкетата за избор на проект"),
    ]
    assert pick_target(links, BASE, LAB_KEYWORDS) == ("activity", f"{BASE}/mod/choice/view.php?id=10")


def test_several_activities_without_a_clear_one():
    links = [(f"{BASE}/mod/choice/view.php?id=10", "прва анкета"), (f"{BASE}/mod/feedback/view.php?id=11", "втора анкета")]
    assert pick_target(links, BASE, LAB_KEYWORDS) == ("several", None)


def test_pick_forum_when_no_activity():
    links = [(f"{BASE}/course/view.php?id=1", "Курс"), (f"{BASE}/mod/forum/discuss.php?d=45#p7", "Пост")]
    assert pick_target(links, BASE) == ("forum", f"{BASE}/mod/forum/discuss.php?d=45")


def test_other_sites_and_plain_pages_are_not_targets():
    links = [("https://other.example/mod/choice/view.php?id=9", "x"), (f"{BASE}/course/view.php?id=1", "Курс")]
    assert pick_target(links, BASE) == ("none", None)


def test_safelinks_are_unwrapped():
    wrapped = "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fcourses.example.edu%2Fmod%2Fchoice%2Fview.php%3Fid%3D7&data=x"
    assert unwrap_safelink(wrapped) == f"{BASE}/mod/choice/view.php?id=7"
    assert list(activity_links([(wrapped, "анкета")], BASE)) == [f"{BASE}/mod/choice/view.php?id=7"]


def test_is_choice():
    assert is_choice(f"{BASE}/mod/choice/view.php?id=7")
    assert not is_choice(f"{BASE}/mod/feedback/view.php?id=7")
