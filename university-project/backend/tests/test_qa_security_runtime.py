"""Password and authenticator helpers, MCP command line, and the runtime's fallbacks when a dependency is missing."""
import hashlib
import json
import sys
import threading

import pyotp
import pytest

from app import mcp_tools, security
from app import runtime as runtime_module
from app.config import Settings

# ------------------------------------------------------------------ passwords

def legacy(password, salt=b"0123456789abcdef"):
    return f"pbkdf2${salt.hex()}${hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 20_000).hex()}"


def test_a_new_password_verifies_and_a_wrong_one_does_not():
    stored = security.hash_password("Harbour-2468")
    assert stored.startswith("$argon2") and security.verify_password("Harbour-2468", stored)
    assert not security.verify_password("Harbour-2469", stored) and not security.verify_password("", stored)


def test_an_older_pbkdf2_password_still_verifies_and_is_flagged_for_an_upgrade():
    stored = legacy("old-password-1")
    assert security.verify_password("old-password-1", stored) and not security.verify_password("old-password-2", stored)
    assert security.password_needs_upgrade(stored)
    assert not security.password_needs_upgrade(security.hash_password("fresh-password-1"))


@pytest.mark.parametrize("stored", [None, "", "plain", "a$b", "scrypt$00$00", "pbkdf2$zz$00", "pbkdf2$00$00$00", "pbkdf2$0$00", "$argon2id$broken", "$argon2"])
def test_a_malformed_stored_hash_never_verifies_and_never_raises(stored):
    assert security.verify_password("anything", stored) is False


def test_a_broken_argon_hash_is_flagged_for_replacement():
    assert security.password_needs_upgrade("$argon2id$not-a-real-hash") is True
    assert security.password_needs_upgrade(None) is True


def test_answers_are_compared_without_case_spaces_or_punctuation():
    assert security.normalise_answer("  Royal  College! ") == "royalcollege" and security.normalise_answer(None) == ""


# ------------------------------------------------------------------ authenticator codes

def test_an_authenticator_code_is_accepted_with_spaces_and_one_step_of_drift_and_nothing_else():
    secret = pyotp.random_base32()
    code = security.totp_now(secret)
    assert security.totp_valid(secret, code) and security.totp_valid(secret, f" {code[:3]} {code[3:]} ")
    for bad in (None, "", "12345", "1234567", "abcdef", "12 34 5a", "٣٣٣٣٣٣"):
        assert not security.totp_valid(secret, bad)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    assert not security.totp_valid(secret, wrong)


def test_the_setup_link_names_the_issuer_and_the_account():
    uri = security.totp_uri(pyotp.random_base32(), "analyst@payshield.lk")
    assert uri.startswith("otpauth://totp/") and "issuer=PayShield" in uri and "analyst%40payshield.lk" in uri


def test_the_time_left_in_a_code_window_is_between_one_and_thirty_seconds():
    assert 1 <= security.seconds_left_in_totp_window() <= 30


# ------------------------------------------------------------------ MCP command line

def test_the_mcp_command_line_lists_one_agents_tools(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["mcp_tools", "list", "telecom"])
    mcp_tools.main()
    out = capsys.readouterr().out.split()
    assert out == mcp_tools.offered("telecom") and out and not {"hold_transaction", "release_transaction", "block_transaction"} & set(out)


def test_the_mcp_command_line_refuses_an_agent_with_no_read_tools(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mcp_tools", "list", "response"])
    with pytest.raises(SystemExit):
        mcp_tools.main()


# ------------------------------------------------------------------ the runtime when a dependency is missing

def settings(**over):
    base = Settings.from_env()
    import dataclasses

    return dataclasses.replace(base, **over)


def test_an_unreachable_redis_is_not_fatal_and_means_in_memory_events():
    assert runtime_module._connect_redis(settings(redis_url="")) is None
    assert runtime_module._connect_redis(settings(redis_url="redis://127.0.0.1:1/0")) is None


def test_a_postgres_checkpointer_that_cannot_connect_falls_back_to_sqlite(tmp_path):
    chosen = settings(checkpointer="postgres", database_url="postgresql+psycopg://nobody@127.0.0.1:1/none", checkpoint_path=str(tmp_path / "cp.sqlite"))
    manager, saver, kind = runtime_module._open_checkpointer(chosen)
    try:
        assert kind == "sqlite" and saver is not None
    finally:
        manager.__exit__(None, None, None)


def test_a_sqlite_checkpointer_is_used_when_asked_for_even_with_a_postgres_url(tmp_path):
    chosen = settings(checkpointer="sqlite", database_url="postgresql+psycopg://x@127.0.0.1:1/x", checkpoint_path=str(tmp_path / "cp.sqlite"))
    manager, _saver, kind = runtime_module._open_checkpointer(chosen)
    manager.__exit__(None, None, None)
    assert kind == "sqlite"


def test_database_urls_are_translated_for_the_postgres_driver():
    assert runtime_module.psycopg_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert runtime_module.psycopg_url("postgresql+psycopg2://u@h/db") == "postgresql://u@h/db"
    assert runtime_module.psycopg_url("postgres+psycopg://u@h/db") == "postgresql://u@h/db"
    assert runtime_module.psycopg_url("postgresql://u@h/db") == "postgresql://u@h/db"


class FakePubSub:
    def __init__(self, messages, stop):
        self.messages, self.stop, self.subscribed = list(messages), stop, []

    def subscribe(self, channel):
        self.subscribed.append(channel)

    def get_message(self, timeout=None):
        if not self.messages:
            self.stop.set()
            return None
        return self.messages.pop(0)


class FakeRedis:
    def __init__(self, messages, stop):
        self.sub = FakePubSub(messages, stop)

    def pubsub(self, **_kw):
        return self.sub


def test_the_redis_listener_passes_real_events_on_and_skips_junk(boot):
    stop, seen = threading.Event(), []
    boot.bus.observe_remote = seen.append
    boot.redis = FakeRedis([
        None, {"type": "subscribe", "data": 1}, {"type": "message", "data": "not json"}, {"type": "message", "data": None},
        {"type": "message", "data": json.dumps({"id": "e1", "case_id": "PS-1"})}, {"type": "message", "data": json.dumps({"id": "e2"})},
    ], stop)
    runtime_module.redis_loop(stop)
    assert [e["id"] for e in seen] == ["e1", "e2"] and boot.redis.sub.subscribed == ["payshield:live"]


def test_without_redis_the_listener_returns_at_once(boot):
    boot.redis = None
    runtime_module.redis_loop(threading.Event())
