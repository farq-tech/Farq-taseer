"""The offers design fixture (web/app.js «?fixture=offers») is a local tool: it is never a
static file of a deployment, a server on Vercel never hands it out, and only a caller on this
machine or its private network gets it."""
from pathlib import Path

from fastapi.testclient import TestClient

from farq.api import _private_peer, create_app
from farq.config import SearchConfig
from farq.corpus import MemoryCorpus, default_sample_path
from farq.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _api(tmp_path, client=("127.0.0.1", 5000)):
    store = Store(tmp_path / "farq.sqlite3", tmp_path / "uploads")
    app = create_app(store, MemoryCorpus.from_json(default_sample_path()), None, SearchConfig(enable_live=False))
    return TestClient(app, client=client)


def test_fixture_lives_outside_web():
    assert not (ROOT / "web" / "dev").exists()
    assert (ROOT / "data" / "dev" / "fixture-thread.json").is_file()


def test_local_caller_gets_the_fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    response = _api(tmp_path).get("/dev/fixture-thread.json")
    assert response.status_code == 200
    assert response.json()["fixture"] is True


def test_public_caller_and_vercel_get_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    assert _api(tmp_path, client=("8.8.8.8", 5000)).get("/dev/fixture-thread.json").status_code == 404
    # a forwarded header does not make a public caller local
    public = _api(tmp_path, client=("8.8.8.8", 5000))
    assert public.get("/dev/fixture-thread.json", headers={"x-real-ip": "127.0.0.1"}).status_code == 404
    monkeypatch.setenv("VERCEL", "1")
    assert _api(tmp_path).get("/dev/fixture-thread.json").status_code == 404


def test_private_peer():
    assert _private_peer("127.0.0.1") and _private_peer("192.168.1.20") and _private_peer("10.0.0.4")
    assert _private_peer("localhost")
    assert not _private_peer("8.8.8.8") and not _private_peer("")
