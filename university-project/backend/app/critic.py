"""A second look at the notes the agents write, done by the Report agent before it files the case.

A note is only supposed to restate the facts it was given. This check compares each note with those
facts and flags anything else: a number that is not in the facts, a claim that contradicts them, a
decision the note has no business making, a link, instruction-like text, a secret, or a raw identifier.
A flagged note is replaced in the filed case with the rules-written text for that step.

It is deliberately plain: patterns and set comparisons, no model. That makes it predictable and testable.
It will not catch a subtle falsehood written in careful prose, and it is not a substitute for the fact
that no note can change a decision.
"""

import re
from dataclasses import dataclass, field

from app.privacy import _SECRET_LABELS, mask

DECIDING_AGENTS = {"telecom", "banking", "risk"}  # analysis notes: they must not state a disposition
MAX_CHARS = 700

_NUMBER = re.compile(r"\d[\d,]*\.?\d*")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_INSTRUCTION = re.compile(
    r"ignore (all |the |any )?(previous|prior|above)|system prompt|as an ai\b|(assistant|system|developer)\s*:|\"tool\"\s*:|call (the )?\w+_transaction",
    re.IGNORECASE,
)
_DISPOSITION = re.compile(
    r"\b(should|must|recommend(?:ed)?|advis(?:e|ed)|will be|has been|have been)\s+(?:be\s+)?(held|blocked|released|hold|block|release)\b"
    r"|\b(hold|block|release)\s+(this|the)\s+(payment|transfer|money|transaction)\b",
    re.IGNORECASE,
)
_NO_SWAP = re.compile(r"\bno (recent |further )?(sim )?(change|swap)|\bnot (been )?swapped\b|\bno sim (change|swap)", re.IGNORECASE)
_SWAP_CLAIM = re.compile(r"\bsim (was |has been )?(swapped|change|swap)|\bswapped sim|\bsim (change|swap)s? (close|recent|within)", re.IGNORECASE)
_NO_NEW_PAYEE = re.compile(r"\bno new payee|\bnot a new payee|\bpayee (is|was) not new|\bnot (recently )?added", re.IGNORECASE)
_NEW_PAYEE_CLAIM = re.compile(r"\bpayee (that )?(was )?added recently|\bnew payee|\bnewly added payee", re.IGNORECASE)


@dataclass
class Verdict:
    ok: bool
    problems: list = field(default_factory=list)


def _numbers(text: str) -> set[str]:
    return {item.replace(",", "").rstrip(".") for item in _NUMBER.findall(text or "")}


def check(agent: str, text: str, facts: str) -> Verdict:
    problems: list[str] = []
    note = text or ""
    low = note.lower()
    facts_low = (facts or "").lower()
    if not note.strip():
        problems.append("the note is empty")
    if len(note) > MAX_CHARS:
        problems.append("the note is far longer than two sentences")
    invented = sorted(_numbers(note) - _numbers(facts or ""))
    if invented:
        problems.append("numbers that are not in the facts: " + ", ".join(invented[:4]))
    if _URL.search(note):
        problems.append("contains a link")
    if _INSTRUCTION.search(note):
        problems.append("contains instruction-like text")
    if _SECRET_LABELS.search(note):
        problems.append("contains a secret field")
    if mask(note) != note:
        problems.append("contains a raw identifier")
    if agent in DECIDING_AGENTS and _DISPOSITION.search(note):
        problems.append("states a hold, block or release decision")
    if agent == "telecom":
        if "swapped: yes" in facts_low and _NO_SWAP.search(note):
            problems.append("says there was no SIM change, but the feed shows one")
        if "swapped: no" in facts_low and _SWAP_CLAIM.search(note) and not _NO_SWAP.search(note):
            problems.append("claims a SIM change, but the feed shows none")
    if agent == "banking":
        if "new_payee: yes" in facts_low and _NO_NEW_PAYEE.search(note):
            problems.append("says the payee is not new, but the ledger shows it is")
        if "new_payee: no" in facts_low and _NEW_PAYEE_CLAIM.search(note) and not _NO_NEW_PAYEE.search(note):
            problems.append("claims a new payee, but the ledger shows none")
    return Verdict(ok=not problems, problems=problems)
