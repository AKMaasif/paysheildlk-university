"""Stored secrets: derived keys, encrypted personal fields, one security-answer scheme, and a pool of eight questions."""
import hashlib
import random

import pytest
from helpers import live_selfie
from sqlalchemy import text

from app import answers, crypto
from app.agents import tools
from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.knowledge import draw_round, grade_round
from app.models import Case, SecurityAnswer, User
from app.runtime import init_runtime, runtime
from app.seed import CUSTOMERS, PILOT_ANSWERS

KEY = "phase-three-test-key-0123456789-0123456789"
OTHER = "a-different-key-0123456789-0123456789-xyz"


# ------------------------------------------------------------------ keys

def test_each_purpose_gets_its_own_key_and_a_wrong_purpose_cannot_open_the_data():
    assert crypto.derive(KEY, "totp") != crypto.derive(KEY, "face") and len(crypto.derive(KEY, "totp")) == 32
    blob = crypto.encrypt("secret", KEY, "totp")
    assert crypto.decrypt(blob, KEY, "totp") == "secret"
    with pytest.raises(Exception):
        crypto.decrypt(blob, KEY, "face")
    with pytest.raises(Exception):
        crypto.decrypt(blob, OTHER, "totp")


def test_there_is_no_fallback_key_and_a_short_key_is_refused():
    for bad in ("", None, "short", "x" * 31):
        with pytest.raises(crypto.WeakKeyError):
            crypto.encrypt("x", bad)
        with pytest.raises(crypto.WeakKeyError):
            crypto.check_key(bad)
    crypto.check_key("x" * 32)
    crypto.check_key("ab" * 32)  # 64 hex characters are used as raw key bytes
    assert crypto.derive("ab" * 32, "p") != crypto.derive("ab" * 32 + "0", "p")


def test_ciphertext_written_by_the_old_scheme_still_reads():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = b"\x01" * 12
    legacy = "gcm$" + nonce.hex() + "$" + AESGCM(hashlib.sha256(KEY.encode()).digest()).encrypt(nonce, b"old secret", b"payshield").hex()
    assert crypto.decrypt(legacy, KEY) == "old secret"
    assert crypto.decrypt("plain old text", KEY) == "plain old text"


def test_the_service_refuses_to_start_on_a_short_key(boot, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", "too-short")
    with pytest.raises(crypto.WeakKeyError):
        init_runtime()
    monkeypatch.delenv("ENCRYPTION_KEY")
    init_runtime()  # and starts again with the key the data was written with


def test_a_different_key_than_the_one_the_data_was_written_with_stops_the_start_with_a_clear_reason(boot, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", KEY)
    with pytest.raises(crypto.WeakKeyError, match="does not match the key"):
        init_runtime()
    monkeypatch.delenv("ENCRYPTION_KEY")
    init_runtime()


# ------------------------------------------------------------------ personal fields

def test_a_personal_field_is_deterministic_bound_to_its_field_and_tamper_evident():
    a = crypto.seal_field("+94771234567", KEY, "phone")
    assert a == crypto.seal_field("+94771234567", KEY, "phone") and a.startswith("siv1$") and "9477" not in a
    assert a != crypto.seal_field("+94771234567", KEY, "nic")  # the same text in another field looks different
    assert crypto.open_field(a, KEY, "phone") == "+94771234567"
    with pytest.raises(Exception):
        crypto.open_field(a, KEY, "nic")
    with pytest.raises(Exception):
        crypto.open_field(a[:-2] + ("00" if a[-2:] != "00" else "11"), KEY, "phone")


def test_the_database_holds_only_ciphertext_for_email_phone_nic_and_account_number(boot):
    with session_scope() as session:
        raw = session.execute(text("SELECT email, phone, nic, account_number FROM users")).all()
    assert raw
    for row in raw:
        for value in row:
            assert value is None or value.startswith("siv1$"), value
        assert "@" not in row[0]
    with session_scope() as session:
        nimal = session.get(User, "cus_nimal")
        assert nimal.email == "nimal.perera@mail.lk" and nimal.phone.startswith("+94") and nimal.account_number


def test_lookups_by_email_phone_and_account_still_work_through_the_encryption(boot):
    from helpers import record

    row = record("cus_nimal")
    with session_scope() as session:
        assert session.query(User).filter(User.email == row["email"]).one().id == "cus_nimal"
        assert session.query(User).filter(User.phone == row["phone"]).one().id == "cus_nimal"
        assert session.query(User).filter(User.account_number == row["account"]).one().id == "cus_nimal"
        assert session.query(User).filter(User.email == "nobody@nowhere.lk").first() is None
        assert {u.id for u in session.query(User).filter(User.phone.in_([row["phone"], record("cus_dilani")["phone"]])).all()} == {"cus_nimal", "cus_dilani"}


def test_a_value_changed_through_the_application_is_stored_encrypted(boot):
    with session_scope() as session:
        session.get(User, "cus_nimal").phone = "+94770000001"
    with session_scope() as session:
        stored = session.execute(text("SELECT phone FROM users WHERE id='cus_nimal'")).scalar()
        assert stored.startswith("siv1$") and "770000001" not in stored
        assert session.get(User, "cus_nimal").phone == "+94770000001"


def test_a_row_still_in_plain_text_reads_and_is_encrypted_at_the_next_start(boot):
    with session_scope() as session:
        session.execute(text("UPDATE users SET nic='199012345678', phone='+94770000002' WHERE id='cus_nimal'"))
    with session_scope() as session:
        assert session.get(User, "cus_nimal").nic == "199012345678"  # reads as it is
    init_runtime()
    with session_scope() as session:
        raw = session.execute(text("SELECT nic, phone FROM users WHERE id='cus_nimal'")).one()
        assert raw[0].startswith("siv1$") and raw[1].startswith("siv1$")
        user = session.get(User, "cus_nimal")
        assert user.nic == "199012345678" and user.phone == "+94770000002"


def test_the_demo_assist_and_registration_still_see_the_plain_values(boot):
    from app.demo import assist

    shown = assist("cus_nimal")
    assert shown["nic"] and "siv1$" not in str(shown) and shown["email"].endswith("@mail.lk")


# ------------------------------------------------------------------ security answers

LEGACY_PEPPER = "payshield-pilot-pepper"


def legacy_v0(answer):
    import re

    return hashlib.sha256(f"{LEGACY_PEPPER}:{re.sub(r'[^a-z0-9]', '', answer.lower())}".encode()).hexdigest()


def legacy_v1(answer, code=""):
    import re

    salt = b"\x07" * 16
    clean = re.sub(r"\s+", " ", answer.strip().lower())
    return f"pbkdf2${salt.hex()}${hashlib.pbkdf2_hmac('sha256', clean.encode(), salt, 20_000).hex()}"


def test_answers_use_one_clean_up_and_one_salted_hash():
    a, b = answers.hash_secret("Royal College", "school"), answers.hash_secret("Royal College", "school")
    assert a != b and a.startswith("$argon2id$") and answers.is_current(a)
    for given in ("royal college", "  ROYAL   college ", "Royal College!", "royal-college", "RoyalCollege"):
        assert answers.verify_secret(given, a, "school"), given
    assert not answers.verify_secret("royal", a, "school") and not answers.verify_secret("", a, "school")
    assert answers.normalise_answer("Ünï Çode  顔!") == "ünïçode顔"
    assert answers.verify_secret("04/03/1985", answers.hash_secret("1985-03-04", "dob"), "dob")


def test_answers_stored_by_the_two_older_schemes_still_verify_and_are_not_current():
    v0, v1 = legacy_v0("Royal College"), legacy_v1("Royal College")
    assert answers.verify_secret("Royal College", v0, "school", LEGACY_PEPPER) and not answers.verify_secret("Royal", v0, "school", LEGACY_PEPPER)
    assert answers.verify_secret("royal  college", v1, "school") and not answers.verify_secret("royal", v1, "school")
    assert not answers.is_current(v0) and not answers.is_current(v1)
    for junk in ("", "nonsense", "pbkdf2$zz$zz", "$argon2id$broken"):
        assert answers.verify_secret("x", junk, "school") is False


def test_a_correct_round_replaces_older_answer_hashes_with_the_current_scheme(boot):
    with session_scope() as session:
        for row in session.query(SecurityAnswer).filter(SecurityAnswer.user_id == "cus_nimal").all():
            if row.code == "school":
                row.answer_hash = legacy_v0(PILOT_ANSWERS["cus_nimal"]["school"])
            if row.code == "city":
                row.answer_hash = legacy_v1(PILOT_ANSWERS["cus_nimal"]["city"])
    wrong = tools.ask_security_questions("cus_nimal", {"school": "nope", "city": PILOT_ANSWERS["cus_nimal"]["city"]}, True, only=["school", "city"])
    assert wrong["passed"] is False
    with session_scope() as session:
        stored = {r.code: r.answer_hash for r in session.query(SecurityAnswer).filter(SecurityAnswer.user_id == "cus_nimal")}
    assert not answers.is_current(stored["school"]) and not answers.is_current(stored["city"])  # a failed round changes nothing
    right = tools.ask_security_questions("cus_nimal", {"school": PILOT_ANSWERS["cus_nimal"]["school"], "city": PILOT_ANSWERS["cus_nimal"]["city"]}, True, only=["school", "city"])
    assert right["passed"] is True
    with session_scope() as session:
        stored = {r.code: r.answer_hash for r in session.query(SecurityAnswer).filter(SecurityAnswer.user_id == "cus_nimal")}
    assert answers.is_current(stored["school"]) and answers.is_current(stored["city"])
    assert tools.ask_security_questions("cus_nimal", {"school": PILOT_ANSWERS["cus_nimal"]["school"], "city": PILOT_ANSWERS["cus_nimal"]["city"]}, True, only=["school", "city"])["passed"] is True


def test_a_wrong_or_missing_answer_fails_the_round_and_does_not_say_which(boot):
    good = PILOT_ANSWERS["cus_nimal"]
    details = {
        tools.ask_security_questions("cus_nimal", given, True, only=["school", "city"])["detail"]
        for given in ({"school": "x", "city": good["city"]}, {"school": good["school"], "city": "x"}, {"school": "x", "city": "y"}, {})
    }
    assert len(details) == 1


# ------------------------------------------------------------------ the pool of eight

def test_every_demonstration_customer_has_eight_distinct_questions_and_their_answers_verify(boot):
    codes = {item["code"] for item in answers.QUESTION_BANK}
    assert len(codes) == 8
    with session_scope() as session:
        for row in CUSTOMERS:
            stored = session.query(SecurityAnswer).filter(SecurityAnswer.user_id == row["id"]).all()
            assert {s.code for s in stored} == codes and len(stored) == 8
            assert set(PILOT_ANSWERS[row["id"]]) == codes
            for item in stored:
                assert answers.verify_secret(PILOT_ANSWERS[row["id"]][item.code], item.answer_hash, item.code)
                assert not answers.verify_secret("definitely not it", item.answer_hash, item.code)
    # two customers never share a stored hash, even when the answer text matches
    with session_scope() as session:
        hashes = [r.answer_hash for r in session.query(SecurityAnswer).all()]
    assert len(set(hashes)) == len(hashes)


def test_a_round_is_three_different_questions_in_a_random_order():
    pool = [{"id": c["code"], "prompt": c["prompt"]} for c in answers.QUESTION_BANK]
    orders = set()
    for seed in range(40):
        shown = draw_round(pool, 3, random.Random(seed))
        ids = [q["id"] for q in shown]
        assert len(ids) == 3 and len(set(ids)) == 3
        orders.add(tuple(ids))
    assert len(orders) > 10  # not a fixed set, not a fixed order
    assert [q["id"] for q in draw_round(pool[:2], 3)] and len(draw_round(pool[:2], 3)) == 2 and draw_round([], 3) == []


def test_questions_already_shown_are_used_last():
    pool = [{"id": c["code"], "prompt": c["prompt"]} for c in answers.QUESTION_BANK]
    first = [q["id"] for q in draw_round(pool, 3)]
    second = [q["id"] for q in draw_round(pool, 3, avoid=set(first))]
    assert not set(first) & set(second)
    third = [q["id"] for q in draw_round(pool, 3, avoid=set(first) | set(second))]
    assert len(set(third) - set(first) - set(second)) == 2 and len(set(third)) == 3  # two fresh, one repeat once only two are left


def test_grading_needs_every_answer_and_ignores_extra_ones():
    pool = [{"id": "school", "prompt": "s", "hash": answers.hash_secret("Royal College", "school")}, {"id": "city", "prompt": "c", "hash": answers.hash_secret("Galle", "city")}]
    assert grade_round(pool, {"school": "royal college", "city": "GALLE", "pet": "whatever"})
    assert not grade_round(pool, {"school": "royal college"}) and not grade_round([], {"school": "x"})


def test_the_rounds_of_a_real_case_do_not_repeat_questions_until_the_pool_runs_out(boot):
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "yes"}, "waiting_customer") and resume_case(case_id, live_selfie(), "waiting_liveness")
    seen = []
    for round_no in range(1, 4):
        with session_scope() as session:
            case = session.get(Case, case_id)
            assert case.status == "waiting_verification" and case.knowledge_round == round_no
            asked = [q["id"] for q in case.pending_prompt["questions"]]
        assert len(asked) == 3 and len(set(asked)) == 3
        if round_no < 3:
            assert not set(asked) & set(seen)
        seen += asked
        assert resume_case(case_id, {"answers": {q: "wrong" for q in asked}}, "waiting_verification")
    assert len(set(seen)) == 8  # the third round used the two unused questions and one repeat
    with session_scope() as session:
        assert session.get(Case, case_id).policy_decision is not None
        from app.models import Transaction

        assert session.get(Transaction, session.get(Case, case_id).transaction_id).status != "released"


def test_the_runtime_key_is_the_one_the_fields_are_encrypted_with(boot):
    assert runtime.settings.encryption_key and len(runtime.settings.encryption_key) >= crypto.MIN_KEY_CHARS
