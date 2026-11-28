# Samples: worked inputs and outputs

Four real runs of the workflow, each with the **input** (the telecom feed, the bank feed, the payment and what the
customer did) and the **output** (risk score and factors, the rule decision, the case and payment status, the messages
sent, the timeline of agent steps) as JSON, plus the **incident report PDF** the Report agent filed for that case.

They were produced by the real code in a throwaway database, in deterministic mode: no real message was sent and no
model was called. All people, numbers and payments are fictional.

| Sample | Input | Rule decision | Outcome | Files |
|---|---|---|---|---|
| 1. Hold, then verified release | SIM swap 25 min earlier, new handset, location change, new payee, Rs. 200,000. Customer says yes, passes the face scan and the questions | **hold** (score 100) | case closed, payment **released** | [JSON](01-hold-then-verified-release.json) · [PDF](01-hold-then-verified-release-incident-report.pdf) |
| 2. Hold, then customer denies | Same payment. Customer says "No, this was not me" | **hold** (score 100) | case closed, payment **blocked** | [JSON](02-hold-then-customer-denies-block.json) · [PDF](02-hold-then-customer-denies-block-incident-report.pdf) |
| 3. Port-out, blocked at once | SIM swap, open port-out, password reset, new payee, Rs. 800,000 | **block** (score 100) | payment **blocked**, no customer input needed | [JSON](03-port-out-critical-block.json) · [PDF](03-port-out-critical-block-incident-report.pdf) |
| 4. Routine payment | Rs. 4,850 bill, no SIM change | **release** | payment **released** by the rules | [JSON](04-routine-payment-release.json) · [PDF](04-routine-payment-release-incident-report.pdf) |

Each JSON file has `input` and `output`. The phone number is masked. The security answers are not stored in the file.

Regenerate them with:

```bash
cd backend
python -m qa.make_samples
```

The report's "Generated" time and the case and payment numbers change on each run.
