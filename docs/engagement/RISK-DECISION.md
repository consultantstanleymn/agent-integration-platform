# Risk decision: SQLite checkpoints for local runs

## Risk

The case graph saves its paused state in a checkpointer. The default checkpointer is a SQLite file. SQLite works for one API process. Two or more processes can write to the same file and lock each other. A paused case may then fail to save or to resume.

## Options

1. **Accept.** Keep SQLite as the default for local runs and demos. Document the limit.
2. **Mitigate.** Make Postgres the only checkpointer. This adds a database to every local run.
3. **Transfer.** Use a managed workflow service. This adds a vendor and a cost, and it is out of scope.

## Decision

**Accept.** SQLite is the default checkpointer for local runs. Postgres is required when more than one API process runs. Set `CHECKPOINT_URL` to the Postgres URL in that case.

- Owner: Stanley Sujith Nelavala.
- Decision date: see the commit date of this file.
- Review date: 90 days after the decision date.

## Compensating controls

- The same resume test runs on SQLite and on Postgres (`test_resume_after_restart`, `test_resume_after_restart_postgres`).
- `scripts/locust_run.sh` runs two API workers against Postgres checkpoints, so the multi-process path is exercised.
- The compose file and `README.md` name `CHECKPOINT_URL`.
- A case row in the main database keeps the status, so a lost checkpoint is visible: the case stays `awaiting_approval` with no way to resume, and `aip cases expire` rejects it after the expiry time.

## What would change the decision

A second API process in any shared environment. A report of a locked checkpoint file. A move of the platform to a multi-node deployment.
