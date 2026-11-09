# Security gate

This file describes the security checks of the repository. The workflow is `.github/workflows/security.yml`. Action versions are unverified until a run: nobody started or checked a GitHub Actions run for this work.

## Jobs

| Job | What it checks | Fail rule | Local command |
|---|---|---|---|
| sast | Bandit (medium and high), Semgrep local rules in `.semgrep.yml`, CodeQL for Python | Any error-level result | `bandit -r src -ll` and `semgrep --config .semgrep.yml --metrics=off --error src` |
| secrets | Gitleaks over the full history, then the exceptions file check | Any finding | `gitleaks git --config .gitleaks.toml --redact --no-banner --exit-code 1 .` |
| deps | `pip-audit` over the pinned dependency list | HIGH or CRITICAL with a fix | `pip freeze --exclude-editable > req.txt && pip-audit -r req.txt --no-deps --disable-pip` |
| image | Trivy scan of the Docker image | HIGH or CRITICAL with a fix (`--ignore-unfixed`) | `trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 <image>` |
| sbom | CycloneDX SBOM of the source dependencies | None. It makes an artifact | See the SBOM note below |

## Accepted findings

`security/exceptions.json` lists accepted findings. Each item has `id`, `tool`, `reason`, `owner`, `expires` and `review_note`. An item expires after at most 90 days. `scripts/check_security_exceptions.py` fails when a field is missing or a date is past. The list is empty today.

The workflow checks the file. It does not feed the list into each scanner. Today there is nothing to feed. If a finding must be accepted, add it to the file and add the matching ignore entry in the scanner config in the same commit.

## SBOM note

The project has no lock file. Trivy finds nothing in the repository root. The SBOM command therefore scans a folder that holds the output of `pip freeze --exclude-editable` as `requirements.txt`. The result is `docs/engagement/evidence/sbom-source.cdx.json`.

## Honest status

| Check | Status | Evidence |
|---|---|---|
| Gitleaks, full history and tree | Verified locally. One public demo token is allowed with a comment | `docs/engagement/evidence/security-gate-local.txt` |
| Bandit | Verified locally. No medium or high result. One low result is not a failure | same file |
| Semgrep local rules | Verified locally. The five rules also fire on a bad sample file | same file |
| pip-audit | Verified locally against the PyPI advisory data on the run date | same file |
| SBOM of source dependencies | Verified locally | `docs/engagement/evidence/sbom-source.cdx.json` |
| Workflow syntax | actionlint passes. Authored, not run on GitHub | same file |
| CodeQL job | Authored, not run: needs GitHub Actions | none |
| Trivy image scan | Authored, not run locally yet. The local image check waits for the image build packet | none |
| Exceptions check | Verified locally with three unit tests | `tests/test_security_exceptions.py` |

Authored, not run: `.github/workflows/security.yml` is written and syntax-checked with actionlint 1.7.12. It was not executed on GitHub. Reason: needs GitHub Actions.
