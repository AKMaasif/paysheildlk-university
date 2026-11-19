import hashlib

from app.db import session_scope
from app.models import Case, CaseEvent, Report, Transaction, User
from app.util import colombo_stamp, lkr, nid, utcnow


def render_report(case: Case, user: User, tx: Transaction | None, events: list[CaseEvent]) -> str:
    decision = case.policy_decision or "n/a"
    status_line = {
        "closed_blocked": "Transfer blocked",
        "closed_released": "Transfer released after the customer confirmed it",
        "closed_cleared": "Transfer released by the rule layer",
        "escalated": "Transfer still held, escalated to a human analyst",
        "open": "Open with the desk",
        "waiting_customer": "Waiting for the customer",
        "waiting_verification": "Waiting for security answers",
    }.get(case.status, case.status)
    factors = case.risk_factors or []
    factor_lines = "\n".join(f"- {item.get('label')} (+{item.get('points')})" for item in factors) or "- None recorded"
    counts = case.provider_counts or {}
    if counts:
        served = ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))
    else:
        served = "No model call on this case"
    timeline = []
    for event in events:
        stamp = colombo_stamp(event.created_at)
        label = event.tool_name or event.state
        text = event.result_summary or event.message or ""
        text = " ".join(text.split())
        if len(text) > 220:
            text = text[:217] + "..."
        timeline.append(f"- {stamp} {event.agent} / {label}: {text}")
    timeline_block = "\n".join(timeline) or "- No events recorded"
    customer_answer = case.customer_decision or "no answer recorded"
    verification = case.verification_detail or "not reached"
    freeze = case.sim_freeze_ref or "not requested"
    amount = lkr(case.amount_lkr)
    payee = case.payee or (tx.payee if tx else "")
    tx_status = tx.status if tx else "n/a"
    tx_id = tx.id if tx else case.transaction_id or ""
    lines = [
        f"# PayShield incident report {case.id}",
        "",
        f"- Reference: {case.id}",
        f"- Generated: {colombo_stamp(case.updated_at)} Asia/Colombo",
        "- Classification: Confidential, fraud operations (demonstration)",
        f"- Disposition: {status_line}",
        "",
        "## 1. Purpose",
        "",
        "This note records a suspected SIM-swap case for the bank fraud team.",
        "It is a demonstration document. It is not a Central Bank of Sri Lanka return",
        "and not a TRCSL complaint. It is arranged so those teams can see the facts",
        "they usually ask for: the SIM event, the payment, the customer answer, and the action taken.",
        "",
        "## 2. Customer",
        "",
        f"- Name: {user.full_name}",
        f"- Bank: {user.bank}",
        f"- Account: {user.account_number}",
        f"- Mobile: {user.phone}",
        f"- District: {user.district}",
        f"- Province: {user.province}",
        f"- Branch: {user.branch}",
        "",
        "## 3. Payment",
        "",
        f"- Transaction: {tx_id}",
        f"- Amount: {amount}",
        f"- Payee: {payee}",
        f"- Ledger status: {tx_status}",
        "",
        "## 4. Telecom note",
        "",
        case.telecom_summary or "No telecom note recorded.",
        "",
        "## 5. Banking note",
        "",
        case.banking_summary or "No banking note recorded.",
        "",
        "## 6. Risk score and rule decision",
        "",
        f"- Score: {case.risk_score if case.risk_score is not None else 'n/a'} / 100",
        f"- Rule decision: {decision}",
        "",
        case.policy_reason or "",
        "",
        "Factors:",
        "",
        factor_lines,
        "",
        "The score is an input. The decision to hold, block, or release is made by",
        "the rule layer, not by the language model.",
        "",
        "## 7. Customer",
        "",
        f"- Answer: {customer_answer}",
        f"- Verification: {verification}",
        "- Channel: PayShield on the registered device. There is no SMS one-time code on this path.",
        "",
        "## 8. Actions",
        "",
        f"- Alerts sent before escalation or closure: {case.alert_count}",
        f"- SIM freeze reference: {freeze}",
        f"- Account limited: {'yes' if user.restricted else 'no'}",
        "",
        "## 9. Timeline",
        "",
        timeline_block,
        "",
        "## 10. Regulatory context",
        "",
        "CBSL expects banks to apply risk-based controls on electronic payments and to",
        "keep an audit trail of fraud decisions. TRCSL and the operators control SIM",
        "lifecycle actions such as a freeze. This pilot keeps those responsibilities",
        "separate: the bank holds or blocks the money, the operator is asked to freeze",
        "the SIM, and the customer is contacted on the device that was registered before the swap.",
        "",
        "## 11. Model note",
        "",
        f"Language model calls on this case: {served}.",
        "The model wrote the short analyst notes. It did not choose the disposition.",
        "",
        "Risk summary:",
        "",
        case.risk_summary or "No risk note recorded.",
        "",
    ]
    return "\n".join(lines).strip() + "\n"


def save_report(case_id: str) -> dict:
    with session_scope() as session:
        case = session.get(Case, case_id)
        if case is None:
            raise LookupError(case_id)
        user = session.get(User, case.customer_id)
        tx = session.get(Transaction, case.transaction_id) if case.transaction_id else None
        events = (
            session.query(CaseEvent)
            .filter(CaseEvent.case_id == case_id)
            .order_by(CaseEvent.created_at.asc())
            .all()
        )
        draft = render_report(case, user, tx, events)
        body_hash = hashlib.sha256(draft.encode()).hexdigest()
        markdown = draft + f"\n## Fingerprint\n\nSHA-256 {body_hash}\n"
        pdf_hash = hashlib.sha256(render_pdf(markdown)).hexdigest()
        title = f"Incident report {case.id}"
        existing = session.query(Report).filter(Report.case_id == case_id).one_or_none()
        if existing is None:
            report = Report(
                id=nid("rpt"),
                case_id=case_id,
                title=title,
                markdown=markdown,
                sha256=pdf_hash,
                created_at=utcnow(),
            )
            session.add(report)
        else:
            report = existing
            report.title = title
            report.markdown = markdown
            report.sha256 = pdf_hash
            report.created_at = utcnow()
        session.flush()
        return {"report_id": report.id, "title": report.title, "case_id": case_id}


def render_pdf(markdown: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)

    def write(text: str, height: float) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(pdf.epw, height, text)

    for raw in markdown.splitlines():
        line = raw.encode("latin-1", "replace").decode("latin-1")
        if line.startswith("# "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 14)
            write(line[2:], 7)
            pdf.set_font("Helvetica", size=11)
        elif line.startswith("## "):
            pdf.ln(1)
            pdf.set_font("Helvetica", "B", 12)
            write(line[3:], 6)
            pdf.set_font("Helvetica", size=11)
        elif line == "":
            pdf.ln(2)
        else:
            write(line, 5)
    output = pdf.output()
    if isinstance(output, str):
        return output.encode("latin-1")
    return bytes(output)
