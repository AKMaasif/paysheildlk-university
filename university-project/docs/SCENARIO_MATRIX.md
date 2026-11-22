# Scenario matrix: expected against observed

Produced by `python -m qa.scenario_matrix`. Each row ran through the real workflow in its own throwaway database, in deterministic mode (no real message sent, no model called). The rules column is recomputed from the stored decision snapshot with the current rules, not typed in. Redacted evidence per scenario is in `docs/evidence/scenario_evidence.json`.

| Scenario | Rules say (score) | Observed decision / score | Case | Payment | Balance change | Channels | Expected met | Note |
|---|---|---|---|---|---|---|---|---|
| sim_swap_transfer (verified) | hold (100) | hold / 100 | closed_released | released | -200,000 | push:inbox_stored, email:logged_only, sms:logged_only | yes | customer said yes, passed the face scan and the questions |
| sim_swap_transfer (customer says no) | hold (100) | hold / 100 | closed_blocked | blocked | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | customer said they did not make it |
| sim_swap_transfer (face scan fails) | hold (100) | hold / 100 | waiting_liveness | held | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | face scan failed |
| port_out_critical | block (100) | block / 100 | closed_blocked | blocked | +0 | none | yes | block rule needs no customer input |
| routine_payment | release (0) | release / 0 | closed_cleared | released | -4,850 | none | yes | low risk, released by rules |
| customer_silent | hold (100) | hold / 100 | escalated | held | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | ACCELERATED: the clock was moved, not waited out |
| models_down | hold (100) | hold / 100 | waiting_customer | held | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | both model providers unreachable; rules decide |
| channel_failure | hold (100) | hold / 100 | waiting_customer | held | +0 | push:inbox_stored, email:failed, sms:failed | yes | SMS and email fail; the in-app inbox still reaches the customer |
| district_spike | release (8) | hold / 8 | waiting_customer | held | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | the normal rules would release Rs. 75,000; emergency mode holds it |
| agent_failure | - (-) | None / None | escalated | held | +0 | none | yes | the risk agent crashes |
| manual_freeze | release (0) | hold / 0 | waiting_customer | held | +0 | push:inbox_stored, email:logged_only, sms:logged_only | yes | an analyst limited transfers on the account first |
| large_payment (no emergency) | release (8) | release / 8 | closed_cleared | released | -75,000 | none | yes | no emergency: the normal rules release |

A difference between the rules column and the observed decision is expected in two rows only: `district_spike` and `manual_freeze`, where a live switch (emergency mode, an account limit) turns a rule release into a hold. That is the design: those switches can only strengthen a decision.
