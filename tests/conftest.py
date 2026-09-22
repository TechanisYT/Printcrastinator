import pytest


def pytest_addoption(parser):
    parser.addoption("--snapshot-update", action="store_true", help="rewrite PNG snapshots")


@pytest.fixture
def snapshot_update(request):
    return request.config.getoption("--snapshot-update")


@pytest.fixture(autouse=True)
def no_desktop_side_effects(monkeypatch):
    """Tests must never open terminal windows or send notifications."""
    from printcrastinator.render import notify, screen

    monkeypatch.setattr(screen, "open_terminal", lambda: "terminal suppressed in tests")
    monkeypatch.setattr(notify, "send", lambda *a, **k: True)
    monkeypatch.setattr(notify, "send_test", lambda: "notification suppressed in tests")
