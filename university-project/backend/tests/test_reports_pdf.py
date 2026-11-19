import hashlib

import pytest
from fastapi.testclient import TestClient

from app.agents.runner import begin_case, resume_case
from app.db import session_scope
from app.demo import trigger
from app.main import app
from app.models import Notification, Report
from app.report_pdf import build_case_pdf
from app.runtime import runtime
from app.security import create_token


def _blocked_case():
    case_id = trigger("cus_nimal", "sim_swap_transfer")
    begin_case(case_id)
    assert resume_case(case_id, {"decision": "no"}, "waiting_customer")
    return case_id


def _auth(user_id, role="customer"):
    return {"Authorization": "Bearer " + create_token(runtime.settings, user_id, role)}


def test_report_step_files_a_fingerprinted_pdf(boot):
    case_id = _blocked_case()
    with session_scope() as session:
        report = session.query(Report).filter(Report.case_id == case_id).one()
        stored = report.sha256
    first = build_case_pdf(case_id, "analyst")
    assert first.startswith(b"%PDF")
    # The same case always renders to the same bytes, and they match the stored fingerprint.
    assert first == build_case_pdf(case_id, "analyst")
    assert hashlib.sha256(first).hexdigest() == stored


def test_customer_receipt_masks_identifiers_and_analyst_report_does_not(boot):
    case_id = _blocked_case()
    customer = build_case_pdf(case_id, "customer")
    analyst = build_case_pdf(case_id, "analyst")
    assert customer != analyst
    # Full identifiers appear only in the analyst report. The receipt keeps the last digits.
    assert b"8200451190" in analyst and b"+94772451183" in analyst
    assert b"8200451190" not in customer and b"+94772451183" not in customer
    assert b"1190" in customer
    # The receipt carries the exact time and the plain-language outcome.
    assert b"Asia/Colombo" in customer and b"PAYMENT BLOCKED" in customer


def test_customer_gets_the_receipt_by_email_sms_and_push(boot):
    case_id = _blocked_case()
    with session_scope() as session:
        rows = session.query(Notification).filter(Notification.case_id == case_id).all()
        email = next(row for row in rows if row.channel == "email" and "receipt" in row.title.lower())
        sms = [row for row in rows if row.channel == "sms" and "blocked" in row.body.lower() and "receipt" in row.body.lower()]
        assert email.data["attachments"][0]["filename"].endswith(".pdf")
        assert email.data["attachments"][0]["bytes"] > 1000
        assert sms, "a receipt SMS was sent"
        # No link and no code in the SMS.
        assert "http" not in sms[0].body.lower()
        assert any(row.channel == "push" and "receipt" in row.title.lower() for row in rows)


def test_receipt_endpoint_is_private_to_the_customer(boot):
    case_id = _blocked_case()
    with TestClient(app) as client:
        mine = client.get(f"/api/customer/cases/{case_id}/receipt", headers=_auth("cus_nimal"))
        assert mine.status_code == 200
        assert mine.headers["content-type"] == "application/pdf"
        assert mine.content.startswith(b"%PDF")
        other = client.get(f"/api/customer/cases/{case_id}/receipt", headers=_auth("cus_kumari"))
        assert other.status_code == 404
        anonymous = client.get(f"/api/customer/cases/{case_id}/receipt")
        assert anonymous.status_code in {401, 403}


def test_analyst_pdf_download_matches_the_stored_fingerprint(boot):
    case_id = _blocked_case()
    with session_scope() as session:
        report_id = session.query(Report).filter(Report.case_id == case_id).one().id
    with TestClient(app) as client:
        reply = client.get(f"/api/reports/{report_id}/pdf", headers=_auth("usr_analyst", "fraud_analyst"))
    assert reply.status_code == 200
    assert reply.headers["x-integrity"] == "match"


def test_unknown_audience_is_refused(boot):
    case_id = _blocked_case()
    with pytest.raises(ValueError):
        build_case_pdf(case_id, "public")
