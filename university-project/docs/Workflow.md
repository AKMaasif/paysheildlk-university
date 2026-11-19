# Workflow

How a single SIM-swap case moves through PayShield, from the first signal to the case report.

## 1. Signals come in

Two feeds are read in parallel:

- **Telecom feed**: a mock of the GSMA CAMARA SIM Swap API (`check` and `retrieve-date`), so a real operator can replace it later by changing one base URL.
- **Bank feed**: recent account activity such as a new payee, a password reset or a large transfer.

## 2. Score and decide

The telecom agent and the banking agent each call their tools and summarise what they found. The risk agent combines both into a score. A fixed rule layer (`decide_policy`) then chooses one of three outcomes. No language model is involved in this decision.

| Outcome | When | What happens |
| --- | --- | --- |
| Release | Routine activity, no SIM-swap signal | Payment goes through, case report is filed |
| Hold | SIM swap plus a risky payment | Payment is held and the customer is asked to confirm |
| Block | Port-out, password reset or a very large transfer | Payment is blocked, SIM freeze is requested, report is filed |

## 3. Customer confirmation

For a held payment the customer is contacted on the device they registered earlier, not by SMS. They answer a single question: did you make this payment?

- **No**: the payment is blocked and the response agent files the report.
- **Yes**: the customer takes a live camera selfie and answers knowledge questions (up to four rounds of three). A fake selfie twice, or four failed rounds, blocks the payment.
- **No answer**: after 3 minutes the alert is sent again. After 10 minutes the case is assigned to a human analyst and the money stays held.

## 4. Human review

Analysts, telecom operators, bank supervisors and auditors use the operator console. A bank supervisor only sees their own bank. The auditor is read-only.

## 5. Reporting

Every case ends with a report in Markdown and PDF, including the steps taken and the reasons for the decision.

## Orchestration

The supervisor graph is built with LangGraph. While waiting for the customer the graph is paused with `interrupt()` and a database checkpointer, and resumes on the same case when the customer answers.
