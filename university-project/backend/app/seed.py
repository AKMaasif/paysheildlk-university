from datetime import timedelta

from app.models import (
    AccessRequest,
    AuditLog,
    Case,
    CaseEvent,
    Device,
    Invitation,
    Report,
    SimEvent,
    Transaction,
    User,
)
from app.reports import render_report
from app.security import hash_answer, hash_password, seal_totp
from app.util import nid, utcnow

# Fictional people and answers for the pilot. Demo control shows them when DEMO_ASSIST is on.
CUSTOMERS = [
    {
        "id": "cus_nimal",
        "name": "Nimal Perera",
        "email": "nimal.perera@mail.lk",
        "password": "pilot-nimal",
        "phone": "+94772451183",
        "bank": "Commercial Bank",
        "province": "Western",
        "district": "Colombo",
        "branch": "Kollupitiya",
        "account": "8200451190",
        "lat": 6.9147,
        "lng": 79.8489,
        "totp": "57DNTC7YHTX72K5OESQCRL6OXOJBXNKX",
        "school": "Royal College",
        "city": "Galle",
    },
    {
        "id": "cus_kumari",
        "name": "Kumari Silva",
        "email": "kumari.silva@mail.lk",
        "password": "pilot-kumari",
        "phone": "+94718832201",
        "bank": "HNB",
        "province": "Central",
        "district": "Kandy",
        "branch": "Kandy City",
        "account": "003451982201",
        "lat": 7.2935,
        "lng": 80.6350,
        "totp": "YMLHKZRKAI4MFYE2YSHOPRCQOUNZKM4L",
        "school": "Mahamaya",
        "city": "Kandy",
    },
    {
        "id": "cus_fathima",
        "name": "Fathima Nazik",
        "email": "fathima.nazik@mail.lk",
        "password": "pilot-fathima",
        "phone": "+94754409182",
        "bank": "Sampath Bank",
        "province": "Eastern",
        "district": "Batticaloa",
        "branch": "Batticaloa",
        "account": "109238441",
        "lat": 7.7172,
        "lng": 81.7004,
        "totp": "ERI3MCQCBEEWR5CHQMXWDCNINHEVLC5H",
        "school": "Zahira",
        "city": "Batticaloa",
    },
    {
        "id": "cus_ravi",
        "name": "Ravi Fernando",
        "email": "ravi.fernando@mail.lk",
        "password": "pilot-ravi",
        "phone": "+94763017740",
        "bank": "Bank of Ceylon",
        "province": "Southern",
        "district": "Galle",
        "branch": "Galle Fort",
        "account": "712209334",
        "lat": 6.0329,
        "lng": 80.2168,
        "totp": "KQE2MSYI2OG5JJS5QYTAVTUAUQT6TWU3",
        "school": "Richmond",
        "city": "Galle",
    },
    {
        "id": "cus_anjali",
        "name": "Anjali Rajapaksa",
        "email": "anjali.rajapaksa@mail.lk",
        "password": "pilot-anjali",
        "phone": "+94779124408",
        "bank": "People's Bank",
        "province": "Northern",
        "district": "Jaffna",
        "branch": "Jaffna",
        "account": "204118763",
        "lat": 9.6685,
        "lng": 80.0074,
        "totp": "FQKMFRG4R2CIL5BDZAFXMWLAXKLBRBEI",
        "school": "Vembadi",
        "city": "Jaffna",
    },
    {
        "id": "cus_irfan",
        "name": "Mohamed Irfan",
        "email": "mohamed.irfan@mail.lk",
        "password": "pilot-irfan",
        "phone": "+94702286533",
        "bank": "Commercial Bank",
        "province": "North Western",
        "district": "Kurunegala",
        "branch": "Kurunegala",
        "account": "8201983341",
        "lat": 7.4863,
        "lng": 80.3647,
        "totp": "SCFBWSTTN6OR3NFIPAKQSDIBJEIHFR5W",
        "school": "Maliyadeva",
        "city": "Kurunegala",
    },
    {
        "id": "cus_dilani",
        "name": "Dilani Wijesinghe",
        "email": "dilani.wijesinghe@mail.lk",
        "password": "pilot-dilani",
        "phone": "+94746153904",
        "bank": "NSB",
        "province": "Uva",
        "district": "Badulla",
        "branch": "Badulla",
        "account": "100228761",
        "lat": 6.9934,
        "lng": 81.0550,
        "totp": "Q6SVFZJUC3WBAR5S3UHM2TC25FD3N4DE",
        "school": "Uva College",
        "city": "Badulla",
    },
    {
        "id": "cus_suresh",
        "name": "Suresh Nadarajah",
        "email": "suresh.nadarajah@mail.lk",
        "password": "pilot-suresh",
        "phone": "+94771046621",
        "bank": "HNB",
        "province": "Northern",
        "district": "Jaffna",
        "branch": "Jaffna",
        "account": "0031192044",
        "lat": 9.6615,
        "lng": 80.0255,
        "totp": "PAWWA436JSS7SYTB2OCTMNUB6M6ZZCEG",
        "school": "St Johns",
        "city": "Jaffna",
    },
    {
        "id": "cus_harshani",
        "name": "Harshani Peiris",
        "email": "harshani.peiris@mail.lk",
        "password": "pilot-harshani",
        "phone": "+94761239880",
        "bank": "Sampath Bank",
        "province": "Western",
        "district": "Gampaha",
        "branch": "Gampaha",
        "account": "109552810",
        "lat": 7.0917,
        "lng": 79.9994,
        "totp": "MKXGHE4F2AQG7NKRP7N5CP3HKBVAMQDH",
        "school": "Bandaranayake",
        "city": "Gampaha",
    },
    {
        "id": "cus_lasantha",
        "name": "Lasantha Abeysekera",
        "email": "lasantha.abeysekera@mail.lk",
        "password": "pilot-lasantha",
        "phone": "+94713345092",
        "bank": "Bank of Ceylon",
        "province": "Southern",
        "district": "Matara",
        "branch": "Matara",
        "account": "756610293",
        "lat": 5.9549,
        "lng": 80.5550,
        "totp": "QMBK3EWWKOLF6O5YBWADZ2W5EZG6WENV",
        "school": "Rahula",
        "city": "Matara",
    },
]

OPERATORS = [
    ("usr_analyst", "Ishara Gunawardena", "analyst@payshield.lk", "pilot-analyst", "fraud_analyst", None),
    ("usr_telco", "Nuwan Jayasekara", "telco@payshield.lk", "pilot-telco", "telecom_operator", None),
    ("usr_supervisor", "Malini Fernando", "supervisor@payshield.lk", "pilot-supervisor", "bank_supervisor", "Commercial Bank"),
    ("usr_auditor", "Chathura Bandara", "auditor@payshield.lk", "pilot-auditor", "auditor", None),
    ("usr_admin", "Sanduni de Silva", "admin@payshield.lk", "pilot-admin", "super_admin", None),
]

PILOT_ANSWERS = {
    row["id"]: {"school": row["school"], "city": row["city"]} for row in CUSTOMERS
}

SCHOOL_PROMPT = "What was the name of your first school?"
CITY_PROMPT = "In which Sri Lankan city were you born?"

# days_ago, customer, amount, status, score, decision, payee, new_payee, tx_status
HISTORY = [
    (76, "cus_suresh", 18000, "closed_cleared", 6, "release", "Dialog Axiata", False, "released"),
    (68, "cus_anjali", 240000, "closed_blocked", 74, "hold", "Northern Traders", True, "blocked"),
    (60, "cus_harshani", 12500, "closed_cleared", 4, "release", "CEB Electricity", False, "released"),
    (54, "cus_lasantha", 96000, "closed_released", 48, "hold", "St Thomas College", False, "released"),
    (47, "cus_kumari", 310000, "closed_blocked", 81, "hold", "Kandy Motors", True, "blocked"),
    (40, "cus_ravi", 22000, "closed_cleared", 9, "release", "CEB Electricity", False, "released"),
    (33, "cus_irfan", 540000, "closed_blocked", 88, "block", "Port Imports", True, "blocked"),
    (27, "cus_fathima", 76000, "closed_released", 52, "hold", "Family Transfer", False, "released"),
    (20, "cus_nimal", 15000, "closed_cleared", 5, "release", "Keells", False, "released"),
    (13, "cus_dilani", 640000, "closed_blocked", 79, "hold", "Uva Supplies", True, "blocked"),
    (6, "cus_kumari", 450000, "closed_blocked", 78, "hold", "Hill Country Traders", True, "blocked"),
    (4, "cus_ravi", 80000, "closed_released", 55, "hold", "Galle Tuition Centre", False, "released"),
    (2, "cus_dilani", 3200, "closed_cleared", 0, "release", "CEB Electricity", False, "released"),
    (1, "cus_irfan", 1250000, "escalated", 86, "hold", "Kurunegala Imports", True, "held"),
    (0.4, "cus_fathima", 125000, "open", 62, "hold", "Eastern Wholesale", True, "held"),
]


def seed(session, settings) -> bool:
    existing = session.query(User).count()
    if existing:
        return False
    now = utcnow()
    by_id = {}
    for row in CUSTOMERS:
        user = User(
            id=row["id"],
            email=row["email"],
            full_name=row["name"],
            password_hash=hash_password(row["password"]),
            role="customer",
            phone=row["phone"],
            bank=row["bank"],
            province=row["province"],
            district=row["district"],
            branch=row["branch"],
            account_number=row["account"],
            lat=row["lat"],
            lng=row["lng"],
            totp_secret=seal_totp(row["totp"]),
            totp_enrolled=True,
            status="active",
            restricted=False,
            security={
                "school": {"prompt": SCHOOL_PROMPT, "answer_hash": hash_answer(row["school"], settings.answer_pepper)},
                "city": {"prompt": CITY_PROMPT, "answer_hash": hash_answer(row["city"], settings.answer_pepper)},
            },
            created_at=now - timedelta(days=120),
        )
        session.add(user)
        by_id[row["id"]] = user
        session.add(
            Device(
                id=nid("dev"),
                user_id=row["id"],
                label=f"{row['name'].split()[0]}'s phone",
                platform="Android Chrome",
                trusted=True,
                last_seen=now - timedelta(hours=5),
                created_at=now - timedelta(days=40),
            )
        )
        _everyday_activity(session, row["id"], now)

    for op in OPERATORS:
        session.add(
            User(
                id=op[0],
                email=op[2],
                full_name=op[1],
                password_hash=hash_password(op[3]),
                role=op[4],
                bank=op[5],
                status="active",
                totp_enrolled=False,
                restricted=False,
                created_at=now - timedelta(days=90),
            )
        )
        session.add(
            Device(
                id=nid("dev"),
                user_id=op[0],
                label="Desk browser",
                platform="Browser",
                trusted=True,
                last_seen=now - timedelta(hours=1),
                created_at=now - timedelta(days=30),
            )
        )

    session.add(
        Invitation(
            token="PS-INVITE-7K2M",
            email="priya.fernando@mail.lk",
            full_name="Priya Fernando",
            role="fraud_analyst",
            bank="Commercial Bank",
            used=False,
        )
    )
    session.add(
        AccessRequest(
            id="acr_pending",
            name="Shani Perera",
            email="shani.perera@combank.example",
            organisation="Commercial Bank",
            role_asked="fraud_analyst",
            scope_asked="Western province",
            reason="Cover the Colombo high-value queue during the pilot.",
            state="pending",
            created_at=now - timedelta(hours=6),
        )
    )
    session.add(
        AccessRequest(
            id="acr_approved",
            name="Nuwan Jayasekara",
            email="telco@payshield.lk",
            organisation="Pilot operator desk",
            role_asked="telecom_operator",
            scope_asked="All provinces",
            reason="SIM freeze liaison for the pilot.",
            state="approved",
            decided_by="admin@payshield.lk",
            created_at=now - timedelta(days=12),
        )
    )
    session.add(
        AccessRequest(
            id="acr_refused",
            name="External Contractor",
            email="contractor@example.com",
            organisation="Outside firm",
            role_asked="super_admin",
            scope_asked="All banks",
            reason="Requested full admin for a vendor demo.",
            state="refused",
            decided_by="admin@payshield.lk",
            created_at=now - timedelta(days=9),
        )
    )

    number = 31
    for days, customer_id, amount, status, score, decision, payee, new_payee, tx_status in HISTORY:
        when = now - timedelta(days=days)
        user = by_id[customer_id]
        case_id = f"PS-26-{number:05d}"
        tx_id = f"TX-2{number:04d}"
        number += 1
        tx = Transaction(
            id=tx_id,
            customer_id=customer_id,
            kind="transfer",
            amount_lkr=amount,
            payee=payee,
            payee_bank="Sampath Bank" if new_payee else user.bank,
            new_payee=new_payee,
            status=tx_status,
            occurred_at=when,
            case_id=case_id,
            origin="seed",
            detail={"seed": True},
        )
        session.add(tx)
        factors = _factors_for(score, decision, new_payee)
        case = Case(
            id=case_id,
            customer_id=customer_id,
            transaction_id=tx_id,
            status=status,
            risk_score=score,
            risk_factors=factors,
            policy_decision=decision,
            policy_reason=_reason(decision),
            telecom_summary=_telecom_note(decision, days),
            banking_summary=f"Ledger shows {payee} for Rs. {amount:,}.",
            risk_summary="Score taken from the factor table. The rule layer chose the disposition.",
            customer_decision=_customer_decision(status),
            verification_passed=True if status == "closed_released" else None,
            verification_detail="2 of 2 answers matched." if status == "closed_released" else None,
            sim_freeze_ref=f"FRZ-SEED{number:02d}" if status == "closed_blocked" else None,
            provider_counts={"mock": 2, "rules": 1} if decision != "release" else {"rules": 1},
            created_at=when,
            updated_at=when + timedelta(minutes=18),
            waiting_since=None,
            last_alert_at=None,
            alert_count=1 if decision == "hold" else 0,
            origin="seed",
            scenario="historical",
            amount_lkr=amount,
            payee=payee,
            bank=user.bank,
            province=user.province,
            district=user.district,
        )
        session.add(case)
        if decision != "release":
            session.add(
                SimEvent(
                    id=nid("sim"),
                    phone=user.phone,
                    event_type="sim_swap",
                    occurred_at=when - timedelta(hours=3),
                    detail={"channel": "ussd", "source": "seed"},
                    origin="seed",
                    case_id=case_id,
                )
            )
        events = _history_events(session, case, when)
        if status == "closed_blocked":
            user.restricted = False
        session.flush()
        markdown = render_report(case, user, tx, events)
        session.add(
            Report(
                id=nid("rpt"),
                case_id=case_id,
                title=f"Incident report {case_id}",
                markdown=markdown,
                created_at=when + timedelta(minutes=20),
            )
        )

    session.add(
        SimEvent(
            id=nid("sim"),
            phone=by_id["cus_suresh"].phone,
            event_type="port_out",
            occurred_at=now - timedelta(days=40),
            detail={"status": "cancelled", "cancelled": True, "note": "Customer withdrew the request"},
            origin="seed",
        )
    )
    _audits(session, now)
    return True


def _everyday_activity(session, customer_id: str, now) -> None:
    samples = [
        (18, "salary", 185000, "Employer payroll", False),
        (11, "bill", 4850, "CEB Electricity", False),
        (9, "purchase", 6400, "Keells", False),
        (3, "bill", 2390, "Dialog Axiata", False),
    ]
    for days, kind, amount, payee, new_payee in samples:
        session.add(
            Transaction(
                id=nid("tx"),
                customer_id=customer_id,
                kind=kind,
                amount_lkr=amount,
                payee=payee,
                new_payee=new_payee,
                status="completed",
                occurred_at=now - timedelta(days=days, hours=4),
                origin="seed",
            )
        )


def _factors_for(score: int, decision: str, new_payee: bool) -> list[dict]:
    if decision == "release":
        return [{"code": "baseline", "label": "No recent SIM swap in the checked window", "points": score}]
    factors = [{"code": "sim_swap_7d", "label": "SIM swap within 7 days", "points": 25}]
    if new_payee:
        factors.append({"code": "new_payee", "label": "Payee was added recently", "points": 15})
    if score >= 70:
        factors.append({"code": "amount_100k", "label": "Transfer is at least Rs. 100,000", "points": 20})
    return factors


def _reason(decision: str) -> str:
    if decision == "block":
        return "Rule: critical combination of port-out, password reset, new payee, and a large transfer."
    if decision == "hold":
        return "Rule: the transfer was held for a customer answer on the registered device."
    return "Rule: the signals did not meet the hold threshold."


def _telecom_note(decision: str, days: float) -> str:
    if decision == "release":
        return "No SIM change was inside the window checked for this payment."
    return f"A SIM change was on the feed about {days:g} days before this record was closed."


def _customer_decision(status: str) -> str | None:
    if status == "closed_blocked":
        return "no"
    if status == "closed_released":
        return "yes"
    if status == "escalated":
        return "timeout"
    return None


def _history_events(session, case: Case, when) -> list[CaseEvent]:
    rows = [
        ("telecom", "done", "get_sim_swap_status", case.telecom_summary, "mock", 180),
        ("banking", "done", "get_recent_transactions", case.banking_summary, "mock", 140),
        ("risk", "done", "compute_risk_score", f"Score {case.risk_score}.", "mock", 90),
        ("policy", "done", None, case.policy_reason, "rules", 12),
        ("case", "done", "generate_report", f"Report filed for {case.id}.", "mock", 70),
    ]
    events = []
    for index, (agent, state, tool, message, provider, latency) in enumerate(rows):
        event = CaseEvent(
            id=nid("evt"),
            case_id=case.id,
            agent=agent,
            state=state,
            tool_name=tool,
            tool_args={"case_id": case.id} if tool else None,
            result_summary=message,
            message=message,
            provider=provider,
            model="mock-deterministic" if provider == "mock" else "deterministic",
            fell_back=False,
            latency_ms=latency,
            handoff_from=agent,
            handoff_to="case" if agent == "policy" else None,
            created_at=when + timedelta(seconds=20 * (index + 1)),
        )
        session.add(event)
        events.append(event)
    return events


def _audits(session, now) -> None:
    rows = [
        ("admin@payshield.lk", "sign_in", "admin@payshield.lk", "Console sign-in", 2),
        ("analyst@payshield.lk", "sign_in", "analyst@payshield.lk", "Console sign-in", 5),
        ("admin@payshield.lk", "access_refused", "acr_refused", "Refused a request for super admin.", 9 * 24),
        ("admin@payshield.lk", "access_approved", "acr_approved", "Approved the telecom liaison.", 12 * 24),
        ("supervisor@payshield.lk", "report_download", "PS-26-00041", "Downloaded an incident report.", 20),
        ("auditor@payshield.lk", "sign_in", "auditor@payshield.lk", "Read-only sign-in.", 8),
    ]
    for actor, action, target, detail, hours in rows:
        session.add(
            AuditLog(
                id=nid("aud"),
                actor_email=actor,
                action=action,
                target=target,
                detail=detail,
                created_at=now - timedelta(hours=hours),
            )
        )
