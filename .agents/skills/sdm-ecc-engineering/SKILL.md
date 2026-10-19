---
name: sdm-ecc-engineering
description: Use for any School Discord Manager bug, regression, refactor, behavior change, persistence or permission change. Enforces evidence-led ECC work, minimal corrective patches, regression tests, and honest verification.
---

# School Discord Manager — ECC Engineering

Use this skill before changing application code or making a root-cause claim about this repository. Follow the project's established ECC methodology; do not invent a new expansion for the acronym. The purpose is to prevent assumption-driven edits and regressions in software used for real school operations.

## 1. Establish the actual state

1. Read the root `AGENTS.md` and the relevant skill(s).
2. Inspect the current branch, base commit, and working tree if a local checkout is available. Record uncommitted changes and leave them untouched.
3. Inspect recent history and the exact files, tests, configuration, and CI workflow involved in the task. Do not rely only on previous conversation summaries.
4. State the user's requested outcome and explicit constraints. Keep diagnosis, fix, and unrelated improvements as separate scopes.

If local status or test execution is unavailable, say so. A remote file read is not evidence that a local checkout is clean.

## 2. Build an evidence-backed diagnosis

Create a compact evidence ledger:

| Item | Required content |
|---|---|
| Observed symptom | Exact error/output or reproducible failing assertion |
| Evidence | File/line, stack trace, test output, relevant commit/diff, or observed state |
| Hypothesis | Explanation that could account for the evidence |
| Discriminating check | Test or inspection that can distinguish this hypothesis from alternatives |
| Confidence | Confirmed, strongly supported, or unconfirmed |

Trace the active code path end to end, including callers, shared services, persistence, permissions, exception handlers, and command registration where applicable. Inspect historical versions when investigating a regression. Do not treat correlation or a plausible Discord error as proof of a specific cause.

Do not edit merely to “try” a hypothesis. First identify the lowest-risk check that can confirm or reject it.

## 3. Choose a controlled correction

Before editing, record:
- the demonstrated root cause (or clearly label diagnosis as incomplete);
- why the proposed edit addresses that cause;
- the exact files and behavior in scope;
- the regression test(s) that should fail before the fix and pass after it;
- any persistence, permission, ordering, rollback, or destructive side effects.

Then make the smallest change that solves the evidenced problem. Preserve existing public behavior and security boundaries unless the user explicitly requests a separate, justified change. No feature creep, blanket exception swallowing, silent scope expansion, permission broadening, or unrelated refactoring.

Prefer a regression test that fails for the old behavior. Do not weaken or delete tests only to make a desired diff pass. When tests encode a behavior that conflicts with an established requirement, surface the conflict and justify the resolution with evidence.

## 4. Verify in layers

1. Run the narrow relevant test(s) first.
2. Run `python -m pytest -q` for the complete suite when the environment permits.
3. Run relevant static/import/compile checks only when supported by project configuration or the changed files.
4. Review the full `git diff --check` and `git diff`; confirm no secrets, generated runtime data, unrelated files, accidental deletions, or unexpected permission/scope changes are present.
5. Check the actual GitHub Actions runs for the proposed commit/PR. The repository CI matrix includes Python 3.12, exact 3.13.2, latest 3.13 patch, and 3.14; Docker build/smoke and Compose validation are also gates.
6. For Discord-facing behavior, keep automated unit tests distinct from live E2E. Only run live tests in a dedicated, verified test guild using the documented execution/observer model and required authorization.

If a check cannot run, label it `NOT RUN` and explain why. Do not substitute an assumed result.

## 5. Historical regression discipline

When behavior regresses:
- identify the last version with positive evidence of the desired behavior (test, CI, review, or explicitly reported live result);
- compare the complete relevant changes between that version and the current code;
- separate the code that introduced the regression from later fixes or mitigations;
- distinguish “restoring source text” from restoring the same runtime behavior and environment;
- avoid a blind full-file rollback if it would reintroduce unrelated defects or security issues.

A commit being older, previously merged, or called “stable” in a conversation is insufficient by itself. Capture why the baseline is believed to be good and which requirements it satisfies.

## 6. Pull request and final report

Work on a dedicated branch; do not commit directly to `main`. Keep the PR description concise but evidence-based: symptom, root cause, minimal fix, regression protection, test results, and remaining risks. Review the diff as if the patch were written by someone else.

Final status must distinguish:
- **Confirmed:** directly supported by inspected code or completed checks.
- **Inferred:** consistent with evidence but not proven.
- **Not verified:** requires unavailable local execution, CI, or dedicated live E2E.

Never say “fixed,” “stable,” or “ready for production” merely because the patch was written or unit tests passed.
