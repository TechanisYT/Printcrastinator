from concurrent.futures import ThreadPoolExecutor
from datetime import date

from printcrastinator.db import Database


def test_daily_claim_is_atomic(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    day = date(2026, 9, 22)
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda _: db.claim_daily(day), range(32)))
    assert results.count(True) == 1
    db.release_daily(day)
    assert db.claim_daily(day) is True
    db.mark_daily_printed(day, on_paper=True, task_count=3)
    assert db.daily_status(day)["printed_on_paper"] == 1


def test_suppression_per_occurrence(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    db.suppress("abc", "2026-09-22", "Water plants")
    assert ("abc", "2026-09-22") in db.suppressed_keys()
    assert ("abc", "2026-09-29") not in db.suppressed_keys()
    db.unsuppress("abc", "2026-09-22")
    assert not db.suppressed_keys()


def test_seen_and_rules(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    assert db.unseen([("a", "tasks"), ("b", "deck")]) == {("a", "tasks"), ("b", "deck")}
    db.mark_seen([("a", "tasks")])
    assert db.unseen([("a", "tasks"), ("b", "deck")]) == {("b", "deck")}
    db.set_stack_rule(1, 5, True)
    db.set_stack_rule(1, 6, False)
    assert db.always_print_stacks() == {(1, 5)}
    db.set_list_rule("personal", True)
    assert db.always_print_lists() == {"personal"}
    db.set_calendar_enabled("work", False)
    assert db.disabled_calendars() == {"work"}
    db.cache_put("col", "ctag1", {"x": 1})
    assert db.cache_get("col") == ("ctag1", {"x": 1})


def test_custom_lists_roundtrip(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    lid = db.save_custom_list("Shopping list", ["Milk", " Eggs ", ""])
    assert db.custom_list(lid)["items"] == ["Milk", "Eggs"]
    assert db.find_custom_list("shopping")["id"] == lid
    db.save_custom_list("Shopping list", ["Milk", "Eggs", "Bread"], lid)
    db.mark_list_printed(lid)
    row = db.custom_list(lid)
    assert row["items"] == ["Milk", "Eggs", "Bread"] and row["printed_at"]
    db.delete_custom_list(lid)
    assert db.custom_lists() == []


def test_stack_order_roundtrip(tmp_path):
    db = Database(tmp_path / "s.sqlite3")
    assert db.stack_positions() == {}
    db.set_stack_order(4, [12, 9, 13])
    assert db.stack_positions() == {(4, 12): 0, (4, 9): 1, (4, 13): 2}
    db.set_stack_order(4, [9, 12, 13])
    assert db.stack_positions()[(4, 9)] == 0
    db.clear_stack_order(4)
    assert db.stack_positions() == {}
