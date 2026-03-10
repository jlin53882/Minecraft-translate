from app.views.cache_controller import CacheController


def test_begin_action_increments_and_sets_current():
    c = CacheController()

    a1 = c.begin_action()
    a2 = c.begin_action()

    assert a1 == 1
    assert a2 == 2
    assert c.current_action_id == 2


def test_is_current_with_none_and_mismatch():
    c = CacheController()
    c.begin_action()

    assert c.is_current(None) is True
    assert c.is_current(1) is True
    assert c.is_current(999) is False
