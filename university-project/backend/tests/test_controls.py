import hashlib

from app.answers import hash_secret, normalise_answer, normalise_dob, verify_secret
from app.charters import CharterViolation, enforce
from app.crypto import decrypt, encrypt
from app.emergency import clear_emergency, force_emergency, must_hold
from app.knowledge import grade_round
from app.liveness import analyse
from app.protocol import sign_envelope, verify_envelope
from app.reports import render_pdf
from app.security import totp_now, totp_valid


def test_answers_are_normalised_and_salted():
    stored = hash_secret("  Royal   College ", "school")
    assert stored.startswith("pbkdf2$")
    assert verify_secret("royal college", stored, "school")
    assert not verify_secret("royal", stored, "school")
    assert normalise_answer("  Royal   College ") == "royal college"
    assert normalise_dob("17/11/1990") == "1990-11-17"


def test_totp_window_accepts_the_current_code():
    secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    assert totp_valid(secret, totp_now(secret))
    assert not totp_valid(secret, "000000")


def test_knowledge_round_does_not_reveal_which_answer_failed():
    questions = [
        {"id": "school", "prompt": "School", "hash": hash_secret("royal college", "school")},
        {"id": "city", "prompt": "Town", "hash": hash_secret("galle", "city")},
    ]
    assert grade_round(questions, {"school": "Royal College", "city": "Galle"}, "pepper")
    assert grade_round(questions, {"school": "nope", "city": "Galle"}, "pepper") is False


def test_liveness_two_strikes_are_distinct_from_a_live_pair():
    fake = analyse([b"same-frame", b"same-frame"])
    assert fake["verdict"] == "fake"
    live = analyse([bytes(range(256)) * 5, bytes(range(255, -1, -1)) * 5])
    assert live["verdict"] == "live"
    explicit = analyse([], "fail")
    assert explicit["verdict"] == "fake"


def test_charter_refuses_a_tool_outside_the_allowlist():
    enforce("telecom", "get_sim_swap_status")
    try:
        enforce("telecom", "block_transaction")
        raised = False
    except CharterViolation:
        raised = True
    assert raised


def test_hmac_envelope_rejects_a_replay():
    envelope = sign_envelope("test-key", "telecom", "risk", "PS-1", {"score": 10}, nonce="n-1", timestamp=1_700_000_000)
    verify_envelope("test-key", envelope)
    try:
        verify_envelope("test-key", envelope)
        replayed = False
    except ValueError:
        replayed = True
    assert replayed


def test_circuit_breaker_holds_a_district(boot):
    clear_emergency("Colombo")
    assert must_hold("Colombo", 80_000) is None
    force_emergency("Colombo", "drill", by="test@payshield.lk")
    assert "drill" in must_hold("Colombo", 80_000)
    assert must_hold("Colombo", 1000) is None
    clear_emergency("Colombo")
    assert must_hold("Colombo", 80_000) is None


def test_pdf_has_a_stable_fingerprint_field():
    blob = render_pdf("# PayShield\n\nSHA-256 abc\n")
    assert blob.startswith(b"%PDF")
    assert len(hashlib.sha256(blob).hexdigest()) == 64


def test_audit_chain_detects_a_tampered_row(boot):
    from app.ledger import audit, verify_audit_chain
    from app.models import AuditLog
    from app.db import session_scope

    audit("desk@payshield.lk", "unit", "case", "first")
    audit("desk@payshield.lk", "unit", "case", "second")
    assert verify_audit_chain()["ok"] is True
    with session_scope() as session:
        row = session.query(AuditLog).order_by(AuditLog.created_at.desc()).first()
        row.detail = "tampered"
    assert verify_audit_chain()["ok"] is False


def test_pii_round_trip():
    blob = encrypt("+94771234567", "unit-key")
    assert "+9477" not in blob
    assert decrypt(blob, "unit-key") == "+94771234567"
