import pytest

from tests.acquire.mock_http_server import MockServer


@pytest.fixture
def mock_server(tmp_path):
    root = tmp_path / "origin"
    root.mkdir()
    server = MockServer(root)
    yield server
    server.shutdown()


@pytest.fixture(autouse=True)
def _no_backoff_wait(request, monkeypatch):
    """A mock origin's 503 or timeout costs no wall time and cools nothing
    down for the next test (app/acquire/backoff.py SCALE). The policy's
    own tests run it at full scale."""
    if request.module.__name__.endswith("test_backoff"):
        return
    from app.acquire import backoff
    monkeypatch.setattr(backoff, "SCALE", 0.0)
