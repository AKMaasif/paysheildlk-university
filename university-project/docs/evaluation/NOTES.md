# Notes on how the evaluation was run

These are the things a reader should know that the numbers alone do not say.

- **First run, then a data change.** The first full run used three earlier payments for the attack and
  adversarial customers. The customer profile's "unusual amount" rule needs at least five, so it never fired
  and recall did not move. The scenario histories were then raised to six earlier payments, which is an
  ordinary ledger for a customer who pays bills. That is a modelling choice made after seeing the first
  result, and it is disclosed here so the second result is not read as an untouched first try.
- **The rule thresholds were not tuned to the scenarios.** The rule table is the one in `backend/app/risk.py`.
  The profile's constants (14 days to be established, 1.25 and 2.0 multiples, 3 and 5 payments) were chosen
  before the first run and not changed since.
- **The red-team scenarios are the same team's idea of an attacker.** They are useful for finding gaps in
  the rules. They say nothing about what real fraudsters do.
- **Labels come from construction.** A scenario is "fraud" because it was built as an attack, not because
  the rules say so. Some scenarios are ambiguous on purpose (a legitimate customer with a new phone and a
  new payee), and the rules are expected to hold some of them.
- **The mock model is the default.** Without a model key the notes are fixed text. The model-only
  single-agent baseline is not run, and the prompt-injection runs test the code around a model, not a
  model.
