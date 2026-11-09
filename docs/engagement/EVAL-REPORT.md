# Case eval report

Label: VERIFIED LOCALLY (replay mode). Measured on one laptop. No hosted model was called.

## Suite design

The suite is `evals/cases.yaml`. It holds 62 support tickets for the fictional customer OutdoorCo. Each case runs through the real case graph on a fresh in-memory database. The graph uses the in-process action port and the policy files in `evals/kb-outdoorco/`.

A case states the expected final status. It can also state the expected action and the approval flag. A case with `approve: true` or `approve: false` gets a team lead decision after the graph pauses.

| Category | Cases | What it checks |
|---|---|---|
| answer | 22 | Small refunds, replacements and escalations finish without a pause |
| refusal | 12 | Forbidden intents, a missing order id and amounts over the hard cap end as refused |
| injection | 12 | Ticket text tries to skip approval or call other tools. It is refused, or it still waits for approval |
| approval | 10 | Amounts above the limit pause. Approve gives completed. Reject gives rejected |
| budget | 6 | A step or token limit ends the run as budget_exceeded with no action |

## Result

Pass rate: 62 of 62 (1.00). The gate is 0.95. The raw output is in `docs/engagement/evidence/eval-cases-replay.json` and `docs/engagement/evidence/eval-cases-replay.txt`.

| Item | Value |
|---|---|
| Eval set SHA-256 (`sha256sum evals/cases.yaml`) | `b90e02151d8d0b16a3f1c36f439ddd8a3ccc2125cd64c5d8d5d877c4b785abd7` |
| Cassette set hash (`sha256sum evals/cassettes/*.json \| sha256sum`) | `8cd8771a8cd3d83f1ec784ab778b4b5e410aba174f0eae8a9a6434a756ba3e55` |
| Cassette files | 118 |

## The gate rule

`aip eval run --suite cases --replay --min-pass-rate 0.95` exits 1 when:

- one cassette file is damaged or its digest does not match its name,
- a cassette is missing for a request, which fails that case,
- the pass rate is below 0.95.

CI runs the same command. The test `test_corrupted_cassette_fails_gate` proves the first rule.

## What replay proves

- Graph routing is correct for each category.
- The authority guards refuse what the file forbids.
- Approval cannot be skipped by text in a ticket.
- Budgets stop a run before any action.
- The gate fails closed when evidence is damaged.

## What replay does not prove

- It does not prove hosted model quality. The cassettes are synthetic. The fake provider made them, in the hosted wire format. No hosted response was recorded.
- It does not prove that a real model follows the prompts. A real model can produce other proposals.
- The cases are written by the author. They are not a sample of real customer tickets.

If the prompt, the graph or the policy files change, the cassette keys change. Run `scripts/make_synthetic_cassettes.py` again and review the diff.
