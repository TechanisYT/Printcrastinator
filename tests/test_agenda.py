from datetime import date, timedelta

from printcrastinator import agenda
from printcrastinator.models import TaskItem

DAY = date(2026, 9, 22)


def t(uid, title, due, src="tasks", list_id="personal", board=None, stack=None):
    return TaskItem(uid, src, title, due, list_id, list_id.title(), board_id=board, stack_id=stack)


def test_rules_and_sorting():
    tasks = [
        t("a", "Zeta today", DAY),
        t("b", "Alpha today", DAY),
        t("c", "Old", DAY - timedelta(days=5)),
        t("d", "Older", DAY - timedelta(days=9)),
        t("e", "Future no rule", DAY + timedelta(days=2)),
        t("f", "No due, always list", None, list_id="always"),
        t("g", "No due, normal list", None),
    ]
    cards = [
        t("deck:1", "Card in always stack", None, "deck", "3/10", 3, 10),
        t("deck:2", "Card in other stack", None, "deck", "3/11", 3, 11),
        t("deck:3", "Card overdue other stack", DAY - timedelta(days=1), "deck", "3/11", 3, 11),
    ]
    ag = agenda.build(
        DAY,
        tasks,
        cards,
        [],
        suppressed=set(),
        always_lists={"always"},
        always_stacks={(3, 10)},
    )
    assert [x.uid for x in ag.overdue] == ["d", "c", "deck:3"]
    assert [x.uid for x in ag.due_today] == ["b", "a"]
    assert [g.title for g in ag.always] == ["3/10", "Always"]
    assert [x.uid for g in ag.always for x in g.items] == ["deck:1", "f"]
    assert ag.has_tasks


def test_suppression_is_per_occurrence():
    tasks = [t("r", "Recurring", DAY)]
    ag = agenda.build(
        DAY,
        tasks,
        [],
        [],
        suppressed={("r", DAY.isoformat())},
        always_lists=set(),
        always_stacks=set(),
    )
    assert not ag.has_tasks
    ag = agenda.build(
        DAY,
        tasks,
        [],
        [],
        suppressed={("r", "2026-09-15")},
        always_lists=set(),
        always_stacks=set(),
    )
    assert ag.has_tasks


def test_overdue_filters_combine():
    tasks = [t(f"o{i}", f"Old {i}", DAY - timedelta(days=i)) for i in (1, 5, 20, 60, 400)]
    kw = dict(suppressed=set(), always_lists=set(), always_stacks=set())
    ag = agenda.build(DAY, tasks, [], [], **kw)
    assert len(ag.overdue) == 5 and ag.overdue_hidden == 0
    ag = agenda.build(DAY, tasks, [], [], overdue_max_days=30, **kw)
    assert [x.uid for x in ag.overdue] == ["o20", "o5", "o1"] and ag.overdue_hidden == 2
    ag = agenda.build(DAY, tasks, [], [], overdue_max_days=30, overdue_max_count=2, **kw)
    assert [x.uid for x in ag.overdue] == ["o5", "o1"] and ag.overdue_hidden == 3


def test_always_groups_follow_rank_then_alpha():
    cards = [
        t("deck:1", "A", None, "deck", "3/10", 3, 10),
        t("deck:2", "B", None, "deck", "3/11", 3, 11),
        t("deck:3", "C", None, "deck", "3/12", 3, 12),
    ]
    kw = dict(suppressed=set(), always_lists=set(), always_stacks={(3, 10), (3, 11), (3, 12)})
    ag = agenda.build(DAY, [], cards, [], group_rank={"3/12": 0, "3/10": 1}, **kw)
    assert [g.title for g in ag.always] == ["3/12", "3/10", "3/11"]
