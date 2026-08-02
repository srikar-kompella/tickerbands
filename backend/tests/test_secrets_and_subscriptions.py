"""get_secret resolution order + the SUBSCRIPTIONS registry contract.

No test here may touch a real OS secret store: keyring is faked via
sys.modules and from_keychain is monkeypatched to a no-op (no subprocess).
"""
import sys
import types

import providers.base as base


def _fake_keyring(calls, value="from-keyring"):
    """A stand-in keyring module that records calls and returns `value`."""
    mod = types.ModuleType("keyring")

    def get_password(service, name):
        calls.append((service, name))
        return value

    mod.get_password = get_password
    return mod


def test_keyring_wins_over_env(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(calls))
    monkeypatch.delenv("TICKERLENS_NO_KEYRING", raising=False)
    monkeypatch.setattr(base, "from_keychain", lambda service: None)
    monkeypatch.setenv("FAKE_SECRET_ENV", "from-env")

    assert base.get_secret("fake_secret", "FAKE_SECRET_ENV") == "from-keyring"
    assert calls == [("tickerlens", "fake_secret")]


def test_kill_switch_skips_keyring_env_wins(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(calls))
    monkeypatch.setenv("TICKERLENS_NO_KEYRING", "1")
    monkeypatch.setattr(base, "from_keychain", lambda service: None)
    monkeypatch.setenv("FAKE_SECRET_ENV", "from-env")

    assert base.get_secret("fake_secret", "FAKE_SECRET_ENV") == "from-env"
    assert calls == []  # fake keyring never consulted


def test_legacy_keychain_between_keyring_and_env(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(calls, value=None))
    monkeypatch.delenv("TICKERLENS_NO_KEYRING", raising=False)
    monkeypatch.setattr(base, "from_keychain", lambda service: "from-legacy")
    monkeypatch.setenv("FAKE_SECRET_ENV", "from-env")

    assert base.get_secret("fake_secret", "FAKE_SECRET_ENV") == "from-legacy"
    assert calls == [("tickerlens", "fake_secret")]  # tried first, empty


def test_nothing_set_returns_none(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "keyring", _fake_keyring(calls, value=None))
    monkeypatch.delenv("TICKERLENS_NO_KEYRING", raising=False)
    monkeypatch.setattr(base, "from_keychain", lambda service: None)
    monkeypatch.delenv("FAKE_SECRET_ENV", raising=False)

    assert base.get_secret("fake_secret", "FAKE_SECRET_ENV") is None


# ─── SUBSCRIPTIONS registry contract ──────────────────────────────────────────

def test_finnhub_pair_is_exact():
    # historical pair — NOT a case transform of the secret name
    assert base.SUBSCRIPTIONS["finnhub"]["secrets"] == [
        ("finnhub_api_key", "FINNHUB_KEY")]


def test_schwab_has_both_pairs():
    assert base.SUBSCRIPTIONS["schwab"]["secrets"] == [
        ("schwab_app_key", "SCHWAB_APP_KEY"),
        ("schwab_app_secret", "SCHWAB_APP_SECRET")]


def test_keyless_providers_registered():
    for prov in ("yfinance", "edgar", "finra", "stocktwits"):
        assert base.SUBSCRIPTIONS[prov]["secrets"] == []


def test_provider_info_keys_derived_from_subscriptions():
    """setup.py derives its key lists from SUBSCRIPTIONS — shapes must match
    what test_setup_wizard/the wizard UI have always expected."""
    from api.setup import PROVIDER_INFO

    assert PROVIDER_INFO["schwab"]["keys"] == ["SCHWAB_APP_KEY", "SCHWAB_APP_SECRET"]
    assert PROVIDER_INFO["finnhub"]["keys"] == ["FINNHUB_KEY"]
    for prov in ("yfinance", "edgar", "finra", "stocktwits", "none"):
        assert PROVIDER_INFO[prov]["keys"] == []
    for info in PROVIDER_INFO.values():
        assert set(info) == {"label", "keys", "note"}


def test_kill_switch_off_values_reenable_keyring(monkeypatch):
    """"0" and "false" must mean OFF (keyring active) — regression for the
    truthy check accepting any non-empty string."""
    import sys
    from providers import base

    class FakeKeyring:
        @staticmethod
        def get_password(service, name):
            assert service == "tickerlens"
            return "from-keyring"

    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring())
    monkeypatch.setattr(base, "from_keychain", lambda name: None)
    monkeypatch.setenv("SOME_ENV_KEY", "from-env")
    for off in ("0", "false", "FALSE", " 0 "):
        monkeypatch.setenv("TICKERLENS_NO_KEYRING", off)
        assert base.get_secret("some_key", "SOME_ENV_KEY") == "from-keyring", off
    monkeypatch.setenv("TICKERLENS_NO_KEYRING", "1")
    assert base.get_secret("some_key", "SOME_ENV_KEY") == "from-env"
