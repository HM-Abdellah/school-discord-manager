---
name: sdm-production-release-gate
description: Use before declaring School Discord Manager complete, production-ready, or ready for school rollout. Audits automated CI, live Discord E2E, data recovery, security boundaries, deployment, and operator readiness.
---

# School Discord Manager — Production Release Gate

Use this skill for final delivery, a production rollout decision, or a broad readiness review. This bot is intended for real school operations; “the code compiles” or “the unit tests pass” is not a release decision.

## Principles

- Read the current root `AGENTS.md`, current README, deployment workflows, E2E guide and matrix index, data/persistence code, and operational documentation.
- Assess the actual current commit and the exact version proposed for deployment. Do not infer the deployed version from a moving tag such as `latest`.
- Use evidence for every gate. Unknown means `NOT VERIFIED`, not PASS.
- Do not run a destructive command, manipulate real school records, or test against production as part of a release review.
- Keep product roadmap completion, automated CI, live Discord acceptance, deployment checks, and operational acceptance as separate dimensions.

## Gate 1 — Source and change integrity

Confirm:
- reviewed branch/commit and proposed PR are identifiable;
- the final diff is scoped, reviewed, and contains no accidental changes, secrets, runtime data, or unreviewed generated files;
- no open critical regression or unresolved destructive-boundary/security concern remains;
- the README and operational behavior are not contradictory on important contracts.

If there is a recent incident or behavioral regression (including `/resetserver`), require the applicable forensic skill and evidence-backed resolution before accepting the affected behavior.

## Gate 2 — Automated verification

Read `.github/workflows/tests.yml` for the current canonical gate. At the time this skill was written, it ran `python -m pytest -q` on:
- Python 3.12;
- Python 3.13.2;
- latest available Python 3.13 patch release;
- Python 3.14.

The Python 3.14 job also builds the Docker image, checks the container runs as user `app`, smoke-tests a key dependency import, and validates Docker Compose using `.env.example`.

Inspect actual CI runs for the candidate commit. Do not substitute a local run, a green run for another commit, or a prior PR's result. Mark each missing or stale run `NOT VERIFIED`.

## Gate 3 — Live Discord acceptance

Read `e2e/README.md`, `e2e/matrix/index.json`, and the current matrix files. Verify matrix completeness/format using the repository's tests and confirm which live scenarios were actually performed.

The intended model is:
1. A normal Discord test user manually performs the slash-command action.
2. A separate observer bot with read access captures before/after guild state.
3. Snapshots/diffs compare Discord IDs and relevant permission-overwrite states.

Requirements:
- Use a dedicated, verified test guild with no real school data.
- Keep tokens out of Git, logs, screenshots, and reports.
- Never use a self-bot or normal-user token automation.
- Use before/after captures and explicit expected-state assertions for each scenario.
- Test failure/recovery paths in isolation where a scenario is destructive; take and validate a recovery point before such a scenario.
- Record the scenario, exact candidate build/commit, preconditions, expected result, observed result, snapshot/diff evidence, and pass/fail status.

CI/unit tests do not prove Discord actually performed the real operation. If the live scenario was not carried out, state that delivery remains blocked or partially unverified according to the repository release contract.

## Gate 4 — Persistence, backup and recovery

Inspect the live design and tests for:
- SQLite as the authoritative database and JSON as a cache/export layer;
- startup cache/database recovery behavior;
- guild-scoped archive integrity and boundaries;
- interrupted destructive workflow behavior;
- consistency between Discord managed-resource IDs and persisted records;
- recovery from a partial deployment, process restart, invalid/missing resource, operator error, or host/container failure.

Require evidence that backups are independent of the bot process and that restore steps are documented and tested in an appropriate non-production environment. A backup file existing is not proof that it can be restored. Never expose database content in diagnostics.

For destructive commands, verify authorization, confirmation, exact-ID scope, preconditions, partial-failure handling, and postconditions with both tests and appropriately isolated live evidence.

## Gate 5 — Security, deployment and operator readiness

Inspect current settings and docs for:
- secret handling, least-privilege bot permissions, Discord privileged intents, and role hierarchy;
- immutable/reproducible deployment reference (image digest or commit), restart/health checks, log access, and rollback steps;
- persistent data-volume configuration and a defined backup/restore procedure;
- account/token recovery and rotation;
- safe school-year rollover, human-error recovery, and relevant incident/runbook steps;
- an explicit school owner/operator who can recover access if the primary technical operator is unavailable.

Do not claim a gate is satisfied merely because a configuration file exists; verify how it is used in the actual deployment. Do not request or output real secrets.

## Gate 6 — Release decision

Produce a compact table with these columns:
- Gate
- Status: `PASS`, `FAIL`, `BLOCKED`, or `NOT VERIFIED`
- Evidence (commit, CI run, test name, matrix scenario, document, or observed result)
- Remaining risk / next action

The release decision must be explicit:
- **Ready:** every mandatory gate passes with evidence.
- **Not ready:** one or more mandatory gates fail or are blocked.
- **Not fully verified:** material gates have not been observed and cannot be treated as passed.

Separate completed source changes from operational acceptance. Never state that the project is complete or production-ready while mandatory live acceptance, recovery, or deployment gates remain unverified.
