# AI risk map

EU AI Act style risk mapping. No compliance claim. No certification.

This table maps common obligation themes to what this repository does. It shows gaps in plain words. It is a working aid for the author. It is not legal advice.

| Theme | What the repo does | Where | Gap |
|---|---|---|---|
| Risk management | A written threat model and an authority file list the risks and limits | `docs/engagement/THREAT-MODEL.md`, `docs/engagement/AUTHORITY.md` | No formal risk review cycle. No owner sign-off process |
| Data governance | Tenant keys isolate data. Redaction masks personal data in audit and case rows | `src/aip/redaction.py`, `src/aip/audit.py` | No data quality checks. No retention schedule |
| Logging | Every case, decision and tool call writes an audit row. Audit rows cannot be changed by the app | `src/aip/audit.py`, migration 0003 | No export. A database owner can drop the trigger |
| Transparency | A case row stores the proposal, the decision and the action result. The authority file is readable text | `src/aip/graph/service.py`, `docs/engagement/AUTHORITY.md` | Customers are not told that an automated step took part |
| Human oversight | A team lead must approve any amount above the limit. Approvals expire to rejected | `src/aip/graph/build.py`, `POST /v1/cases/{id}/decision` | No second approver. No review of the team lead decisions |
| Accuracy and robustness | 62 replay evals, injection tests and budgets | `evals/cases.yaml`, `tests/test_injection.py` | Replay uses synthetic cassettes. No hosted model quality is measured |
| Cybersecurity | Signed webhooks, key hashing, tool allow list, one write tool, static write-path test | `tests/test_graph_write_paths.py`, `SECURITY.md` | No penetration test. Webhook secrets are stored in clear text |
