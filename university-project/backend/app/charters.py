from dataclasses import dataclass


@dataclass(frozen=True)
class Charter:
    role: str
    task: str
    tools: tuple[str, ...]
    scope: str
    forbidden: tuple[str, ...]
    autonomy: str


CHARTERS: dict[str, Charter] = {
    "supervisor": Charter(
        "Supervisor",
        "Open the case and assign the two feeds. Do not move money.",
        ("create_case",),
        "Case id and which agents to wake.",
        ("block_transaction", "lock_account", "generate_report", "release_transaction"),
        "Route only. A crash goes to the safe path and the transfer stays held.",
    ),
    "telecom": Charter(
        "Telecom",
        "Read SIM, handset, location, and port-out, and look further when the first reads call for it. Do not decide.",
        ("get_sim_swap_status", "get_device_change", "get_location_events", "get_port_out_status", "get_sim_history"),
        "The phone on the case.",
        ("block_transaction", "lock_account", "release_transaction", "generate_report"),
        "Read only. Extra lookups are capped at three per case.",
    ),
    "banking": Charter(
        "Banking",
        "Read recent account activity, and look further when the first read calls for it. Do not decide.",
        ("get_recent_transactions", "get_customer_profile", "get_payee_history", "get_credential_events"),
        "The customer ledger.",
        ("block_transaction", "lock_account", "release_transaction", "generate_report"),
        "Read only. Extra lookups are capped at three per case.",
    ),
    "risk": Charter(
        "Risk",
        "Score the signals. Do not hold or release.",
        ("compute_risk_score",),
        "Signals already collected. No raw answers.",
        ("hold_transaction", "block_transaction", "release_transaction", "lock_account"),
        "Score only. The policy layer decides.",
    ),
    "policy": Charter(
        "Policy",
        "Deterministic hold, block, or release. Models do not vote.",
        ("compute_risk_score", "hold_transaction", "release_transaction"),
        "Score and the rule table.",
        ("lock_account", "generate_report", "send_sms"),
        "May hold, and may release a low-risk payment. May not lock the account.",
    ),
    "alert": Charter(
        "Alert",
        "Tell the customer. No links and no codes.",
        ("send_push", "send_email", "send_sms"),
        "Amount, payee, and the registered channels.",
        ("block_transaction", "release_transaction", "lock_account"),
        "Notify only.",
    ),
    "verification": Charter(
        "Verification",
        "Check a live face capture, match it to the face on file, then the knowledge rounds, and release if they pass.",
        ("liveness_check", "face_match", "ask_security_questions", "update_face_template", "release_transaction"),
        "Hashes, the match score and the verdict. Never the raw answers or the image.",
        ("block_transaction", "lock_account", "generate_report"),
        "Release only after a pass. A face that does not match, or a fake, twice goes to Response. An unsure face goes to a person. A very confident, fully confirmed match may refresh the stored templates.",
    ),
    "response": Charter(
        "Response",
        "The only agent that may block, freeze the SIM, and lock the account.",
        ("block_transaction", "request_sim_freeze", "lock_account"),
        "The case reason and the phone.",
        ("release_transaction", "generate_report"),
        "Fail closed. Does not release.",
    ),
    "analytics": Charter(
        "Analytics",
        "Count payments by district, province and day, and write a short brief for the dashboard.",
        ("aggregate_payments", "write_brief", "propose_threshold"),
        "Counts, amounts and district names. No customer names, phones or accounts.",
        ("hold_transaction", "block_transaction", "release_transaction", "lock_account", "generate_report", "send_sms", "send_email", "send_push"),
        "Read and summarise, and propose a rule-threshold change. A proposal changes nothing until an analyst approves it. A model may reword the brief, and the wording is dropped if it adds a number.",
    ),
    "case": Charter(
        "Report",
        "The only agent that files the incident report and sends the customer receipt. Before filing, checks each agent note against its facts.",
        ("review_notes", "generate_report", "deliver_report"),
        "The case timeline. No secrets.",
        ("block_transaction", "release_transaction", "lock_account"),
        "Check the notes, write the report. Do not move money. A note that fails the check is replaced by the rules text.",
    ),
}


class CharterViolation(PermissionError):
    pass


def enforce(agent: str, tool: str) -> None:
    charter = CHARTERS.get(agent)
    if charter is None or tool not in charter.tools:
        raise CharterViolation(f"{agent} may not call {tool}")
