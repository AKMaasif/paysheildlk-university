"""Shared building blocks that every agent leans on: time and phone helpers, encryption, the model client."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import llm as llm_module
from app.crypto import decrypt, encrypt
from app.llm import LLMError, LLMRouter, _mock_text
from app.util import (
    age_hours,
    age_phrase,
    canonical_phone,
    colombo_stamp,
    iso,
    lkr,
    nid,
    scoped_bank,
    severity,
    slt_short,
    valid_e164,
)

NOW = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- phone numbers

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0771234567", "+94771234567"), ("077 123 4567", "+94771234567"), ("077-123-4567", "+94771234567"), ("(077) 1234567", "+94771234567"),
        ("94771234567", "+94771234567"), ("+94771234567", "+94771234567"), ("0094771234567", "+94771234567"), ("  +94771234567  ", "+94771234567"),
    ],
)
def test_phone_numbers_are_put_into_one_form(raw, expected):
    assert canonical_phone(raw) == expected


@pytest.mark.parametrize("phone,ok", [("+94771234567", True), ("+14155550100", True), ("0771234567", False), ("+0771234567", False), ("+9477", False), ("", False), (None, False), ("+94 771234567", False)])
def test_e164_check(phone, ok):
    assert valid_e164(phone) is ok


# ---------------------------------------------------------------- time words

@pytest.mark.parametrize(
    "seconds,words",
    [(0, "just now"), (89, "just now"), (90, "1 min ago"), (3599, "59 min ago"), (3600, "1 h ago"), (7200, "2 h ago"), (86399, "23 h ago"), (86400, "1 day ago"), (172800, "2 days ago"), (-50, "just now")],
)
def test_age_phrase_edges(seconds, words):
    assert age_phrase(NOW - timedelta(seconds=seconds), now=NOW) == words


def test_age_helpers_handle_missing_and_future_times():
    assert age_phrase(None) == "never" and age_hours(None) is None
    assert age_hours(NOW + timedelta(hours=3), now=NOW) == 0.0
    assert age_hours(NOW - timedelta(hours=1.5), now=NOW) == pytest.approx(1.5)
    assert age_hours(datetime(2026, 11, 19, 5, 0), now=NOW) == pytest.approx(1.0)  # a time with no zone is treated as UTC


def test_times_are_shown_in_sri_lanka_time():
    assert colombo_stamp(NOW) == "19 Nov 2026, 11:30"
    assert slt_short(NOW) == "19 Nov, 11:30 SLT"
    assert slt_short("2026-11-19T06:00:00Z") == "19 Nov, 11:30 SLT"
    assert slt_short("2026-11-19 06:00:00") == "19 Nov, 11:30 SLT"
    assert slt_short("not a date") == "not a date" and slt_short(None) == "" and colombo_stamp(None) == ""
    assert iso(None) is None and iso(NOW).endswith("+00:00")


def test_money_ids_and_severity_bands():
    assert lkr(0) == "Rs. 0" and lkr(None) == "Rs. 0" and lkr(1234567) == "Rs. 1,234,567"
    assert nid("tx").startswith("tx_") and nid("tx") != nid("tx") and len(nid("a")) == 12
    assert [severity(s) for s in (None, 0, 39, 40, 69, 70, 100)] == ["none", "low", "low", "medium", "medium", "high", "high"]


def test_a_bank_supervisor_cannot_widen_their_scope():
    assert scoped_bank("bank_supervisor", "BankCylone", "other") == "BankCylone"
    assert scoped_bank("bank_supervisor", "BankCylone", None) == "BankCylone"
    assert scoped_bank("fraud_analyst", None, "all") is None and scoped_bank("fraud_analyst", None, "X") == "X"
    assert scoped_bank("bank_supervisor", None, "X") == "X"


# ---------------------------------------------------------------- encryption

KEY = "unit-test-key-0123456789-0123456789-abcd"
OTHER = "another-key-0123456789-0123456789-wxyz"


def test_encryption_round_trips_and_hides_the_text():
    blob = encrypt("face reference", KEY)
    assert blob.startswith("gcm2$") and "face reference" not in blob
    assert decrypt(blob, KEY) == "face reference"


def test_encryption_never_repeats_and_depends_on_the_key():
    assert encrypt("same", KEY) != encrypt("same", KEY)
    with pytest.raises(Exception):
        decrypt(encrypt("secret", KEY), OTHER)


def test_a_tampered_blob_is_refused_and_edge_values_are_handled():
    blob = encrypt("secret", KEY)
    head, nonce, body = blob.split("$")
    flipped = f"{head}${nonce}${body[:-2]}{'00' if body[-2:] != '00' else '11'}"
    with pytest.raises(Exception):
        decrypt(flipped, KEY)
    assert encrypt(None, KEY) == "" and decrypt("", KEY) == "" and decrypt("plain text", KEY) == "plain text"
    assert decrypt(encrypt("ünïcode 顔", KEY), KEY) == "ünïcode 顔"
    with pytest.raises(Exception, match="at least 32"):
        encrypt("x", "")  # there is no built-in fallback key any more


# ---------------------------------------------------------------- the model client

class Scripted:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def post_json(self, url, headers, json, timeout):
        self.calls.append(url)
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


META_OK = {"choices": [{"message": {"content": "meta says hi"}}]}
GROK_OK = {"choices": [{"message": {"content": "grok says hi"}}]}


def _router(transport, meta="m", grok=""):
    return LLMRouter(meta_api_key=meta, xai_api_key=grok, transport=transport)


def test_meta_answers_first_and_grok_is_not_asked():
    transport = Scripted(META_OK)
    result = _router(transport, grok="x").complete(system="s", user="u", purpose="risk")
    assert (result.provider, result.text, result.fell_back) == ("meta", "meta says hi", False) and len(transport.calls) == 1


@pytest.mark.parametrize("bad", [{"choices": []}, {"choices": [{"message": {"content": "  "}}]}, {}])
def test_an_empty_meta_answer_falls_back_to_grok(bad):
    result = _router(Scripted(bad, GROK_OK), grok="x").complete(system="s", user="u", purpose="risk")
    assert (result.provider, result.fell_back, result.fallback_from) == ("grok", True, "meta")


def test_if_both_fail_the_mock_answers_and_says_it_fell_back():
    result = _router(Scripted(LLMError("http", "x"), LLMError("http", "y")), grok="x").complete(system="s", user="u", purpose="telecom")
    assert (result.provider, result.fell_back, result.fallback_from) == ("mock", True, "meta")


def test_with_no_meta_key_grok_is_the_first_choice_and_with_no_keys_the_mock():
    result = LLMRouter(xai_api_key="x", transport=Scripted(GROK_OK)).complete(system="s", user="u", purpose="risk")
    assert result.provider == "grok"
    assert LLMRouter().complete(system="s", user="u", purpose="risk").provider == "mock"


@pytest.mark.parametrize(
    "content,expected",
    [([{"text": "a"}, {"text": "b"}, "c"], "abc"), ("  plain  ", "plain")],
)
def test_meta_answers_in_pieces_or_as_text(content, expected):
    result = LLMRouter(meta_api_key="m", transport=Scripted({"choices": [{"message": {"content": content}}]})).complete(system="s", user="u", purpose="x")
    assert result.text == expected


@pytest.mark.parametrize("bad", [{"choices": []}, {"choices": [{"message": {"content": ""}}]}])
def test_an_empty_meta_answer_falls_to_the_mock(bad):
    assert LLMRouter(meta_api_key="m", transport=Scripted(bad)).complete(system="s", user="u", purpose="x").provider == "mock"


@pytest.mark.parametrize("status,body", [(429, "slow down"), (503, "down"), (400, "bad request"), (401, "no")])
def test_http_errors_become_model_errors(monkeypatch, status, body):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(status, text=body))
    with pytest.raises(LLMError) as exc:
        llm_module.HttpxTransport().post_json("http://x", {}, {}, 1)
    assert exc.value.status == status


def test_timeouts_and_connection_errors_become_model_errors(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(LLMError, match="timeout"):
        llm_module.HttpxTransport().post_json("http://x", {}, {}, 1)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(LLMError):
        llm_module.HttpxTransport().post_json("http://x", {}, {}, 1)


def test_a_model_never_sees_a_secret_or_a_raw_identifier():
    from app.privacy import PrivacyViolation

    seen = Scripted(META_OK)
    router = _router(seen)
    with pytest.raises(PrivacyViolation):
        router.complete(system="s", user="the customer's password: hunter2", purpose="risk")
    assert seen.calls == []  # nothing was sent anywhere


@pytest.mark.parametrize("purpose", ["telecom", "banking", "risk", "alert", "verification", "response", "case", "analytics", "supervisor", "nonsense"])
def test_the_rules_written_fallback_exists_for_every_step_and_never_decides(purpose):
    text = _mock_text(purpose, "swapped: yes\nnew_payee: yes")
    assert text.strip() and "http" not in text.lower()
    assert not any(w in text.lower() for w in ("you should release", "must block", "i recommend"))


def test_the_rate_limit_scale_widens_only_the_per_address_buckets_and_never_in_production(monkeypatch):
    import importlib

    from app import ratelimit

    base = dict(ratelimit.LIMITS)
    try:
        monkeypatch.setenv("RATE_LIMIT_SCALE", "20")
        monkeypatch.setenv("APP_ENV", "development")
        wide = importlib.reload(ratelimit).LIMITS
        assert wide["login_ip"][0] == 30 * 20 and wide["register_ip"][0] == 12 * 20
        assert wide["login_fail"] == (5, 900) and wide["totp_fail"][0] == 5 and wide["verify"][0] == 12
        monkeypatch.setenv("APP_ENV", "production")
        assert importlib.reload(ratelimit).LIMITS["login_ip"][0] == 30
        monkeypatch.setenv("APP_ENV", "development")
        monkeypatch.setenv("RATE_LIMIT_SCALE", "999999")
        assert importlib.reload(ratelimit).LIMITS["login_ip"][0] == 30 * 50
        monkeypatch.setenv("RATE_LIMIT_SCALE", "junk")
        assert importlib.reload(ratelimit).LIMITS["login_ip"][0] == 30
    finally:
        monkeypatch.delenv("RATE_LIMIT_SCALE", raising=False)
        importlib.reload(ratelimit)
    assert ratelimit.LIMITS == base


def test_redact_drops_secret_keys_masks_identifiers_and_keeps_plain_facts():
    from app.privacy import redact

    out = redact({
        "status": "sent", "to": "+94772451183", "mail": "nimal.perera@mail.lk", "nic": "198524701234", "account": "8200451190",
        "api_key": "abc", "Authorization": "Bearer x", "answers": {"school": "Royal"}, "embedding": [0.1, 0.2], "face_ref": "gcm$x", "signature": "deadbeef",
        "nested": {"password": "p", "count": 3, "items": list(range(40))}, "ok": True, "n": None,
    })
    assert out["status"] == "sent" and out["ok"] is True and out["n"] is None and out["nested"]["count"] == 3
    assert "+94772451183" not in str(out) and "nimal.perera@mail.lk" not in str(out) and "198524701234" not in str(out) and "8200451190" not in str(out)
    for gone in ("api_key", "Authorization", "answers", "embedding", "face_ref", "signature"):
        assert gone not in out
    assert "password" not in out["nested"] and len(out["nested"]["items"]) == 25


def test_redact_survives_odd_values_and_deep_nesting():
    from app.privacy import redact

    deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}
    assert "[deep]" in str(redact(deep))
    assert redact(object())  # an unknown object becomes masked text, not an error
    assert redact("x" * 1000) == "x" * 300
