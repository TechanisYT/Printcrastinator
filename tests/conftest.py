import pytest


def pytest_addoption(parser):
    parser.addoption("--snapshot-update", action="store_true", help="rewrite PNG snapshots")


@pytest.fixture
def snapshot_update(request):
    return request.config.getoption("--snapshot-update")
