"""Setup wizard endpoints (Phase C)."""
import os
import pytest


@pytest.fixture()
def wiz_client(tmp_path, tmp_db, mock_mode, tmp_discussions, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg, "TICKERLENS_HOME", str(tmp_path))
    monkeypatch.setattr(cfg, "DISCUSSIONS_DIR", str(tmp_discussions))
    from fastapi.testclient import TestClient
    from app import app
    with TestClient(app) as c:
        yield c, str(tmp_path)


def test_status_unconfigured_then_save_then_configured(wiz_client):
    c, home = wiz_client
    assert c.get("/api/setup/status").json()["configured"] is False
    opts = c.get("/api/setup/options").json()
    assert "quotes" in opts["capabilities"]
    assert "none" in opts["capabilities"]["quotes"]["providers"]
    caps = {k: v["current"] for k, v in opts["capabilities"].items()}
    caps["quotes"] = "yfinance"; caps["account"] = "none"
    r = c.post("/api/setup/save", json={
        "name": "Jane", "contact_email": "jane@example.com",
        "capabilities": caps, "secrets": {"FINNHUB_KEY": "abc123"}})
    assert r.status_code == 200
    # files written where they belong, env perms tight, config live-reloaded
    assert os.path.exists(os.path.join(home, "config.toml"))
    assert oct(os.stat(os.path.join(home, ".env")).st_mode & 0o777) == "0o600"
    import config as cfg
    assert cfg.USER_NAME == "Jane" and cfg.CONTACT_EMAIL == "jane@example.com"
    assert cfg.CAPABILITIES_CONFIG["quotes"] == "yfinance"
    assert os.environ.get("FINNHUB_KEY") == "abc123"
    assert c.get("/api/setup/status").json()["configured"] is True
    # capability gating reflects account=none (mock off for this check is
    # unnecessary — snapshot uses config unless TICKERLENS_MOCK, which maps all
    # to mock; so assert via config directly)
    assert cfg.CAPABILITIES_CONFIG["account"] == "none"


def test_save_validation(wiz_client):
    c, _ = wiz_client
    assert c.post("/api/setup/save", json={
        "name": "", "contact_email": "x@y.z", "capabilities": {}}).status_code == 422
    assert c.post("/api/setup/save", json={
        "name": "J", "contact_email": "nope", "capabilities": {}}).status_code == 422
    assert c.post("/api/setup/save", json={
        "name": "J", "contact_email": "x@y.z",
        "capabilities": {"quotes": "edgar"}}).status_code == 422  # edgar ≠ quotes


def test_provider_test_endpoint(wiz_client):
    c, _ = wiz_client
    assert c.post("/api/setup/test/stocktwits").json()["ok"] is True
    assert c.post("/api/setup/test/nope").status_code == 404
