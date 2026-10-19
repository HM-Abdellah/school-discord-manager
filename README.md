# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Architecture

Each configured stream is represented by a real Discord category. The stream name is therefore the category title, not a fake title channel.

```text
📘・TC・🔬 TCS
├── 📌-TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
└── 📚-TCS・Mathématiques / PC / SVT / ...
```

The design uses shared academic spaces. Classes are represented as metadata and roles where needed; the bot does not create a Discord channel or role per class.

The builder is idempotent and protected by a per-guild mutation lock. Builds and stream additions use a transactional boundary, managed identities are validated before mutation, unmanaged canonical collisions are rejected, and resources created by a failed build are rolled back on a best-effort basis.

## 👥 Roles and permissions

Core roles:

- `Administration`
- `Prof`
- `Prof (F)`
- `Élève`

Management commands use the server owner or the configured `Administration` role. Destructive maintenance such as `/resetserver` remains owner-only.

Managed stream roles are persisted by Discord ID. Destructive operations resolve targets from the persisted managed-resource registry instead of discovering resources by name.

## 📅 Academic years and student history

Logical academic data is stored in SQLite at `data/school.db`. The JSON file is a cache/export layer and is refreshed from SQLite.

Before `/resetserver` deletes the managed School Manager state, the bot creates a standalone SQLite archive of the current database under `data/archives/<academic-year>.db`. The archive is written atomically and integrity-checked; if archive creation fails, the destructive reset is refused. Existing archive names are never overwritten: a UTC timestamp suffix is used when needed.

Academic years are **logical state and history**, not Discord deployments. The `academic_years` table is authoritative for the active year. The Discord configuration and managed-resource registry represent the **currently deployed Discord structure**. Changing or rolling back the active academic year does not delete, rebuild, rename, or otherwise mutate Discord channels or roles.

Student enrollment is idempotent. Reassigning the same active stream does not create a duplicate active enrollment, while moving a student records the previous enrollment as transferred.

Example yearly workflow:

```text
/newyear 2027/2028
/setup
/build
```

`/newyear` creates and activates a new logical year while keeping the existing Discord structure unchanged. `/rollbackyear` switches the logical active year only; it is not a Discord rollback.

Previous academic years remain available for history and controlled rollback.

## ⚙️ Commands

### Setup and server maintenance

```text
/setup
/build
/addstream
/removestream
/status
/years
/newyear 2027/2028
/rollbackyear
```

### Teacher and student administration

```text
/assignteacher
/assignteacherfull
/assignsubjectteachers
/assignstudent
/studenthistory
/leave_school
/reportabsence
```

### Scheduling and exams

```text
/set_timetable
/setexam
```

Sections are metadata inside shared stream channels. Valid section values are `1..8`; section values do not create additional Discord channels.

### Student class-transfer QR

```text
/createclasstransferqr
/cancelclasstransferqr
```

Administrators use `/createclasstransferqr` to prepare a one-student, one-use transfer request. The bot sends a private Discord message to the selected student and returns a QR image that opens that exact DM message. The student must be signed in to Discord and press the confirmation button; scanning the QR alone never changes roles. The request expires after 30 minutes, is restricted to the selected Discord user, and is persisted so its button can be restored after a bot restart. `/cancelclasstransferqr` invalidates a pending request. On confirmation, the bot replaces managed student stream/class roles and records the new section while retaining enrollment history. Keep the QR private and test the flow in a dedicated test server before production use.

### Administration, usage reports, and destructive maintenance

```text
/adminpanel
/serverhealth
/usagereport days:30
/resetserver RESET SCHOOL MANAGER
```

`/usagereport` is restricted to the configured server administrator or owner. It exports daily aggregate command-use counts and selected action names from existing audited workflows as a CSV attachment, and stores a local copy under `data/reports/`. The optional `days` value supports `1..400` days. The analytics tables store only server ID, command/action name, outcome, and UTC timestamp; they do not store user IDs, names, command arguments, message contents, or targets. Existing security/audit logs remain separate. Records older than 400 days are pruned as new analytics events are recorded. The report distinguishes completed command callbacks from uncaught application-command errors and from actions recorded by audited workflows; a callback completing is **not** proof that its business operation succeeded, and these metrics do not automatically measure time saved.

`/resetserver` is scoped to persisted School Manager resource IDs. Unmanaged Discord resources are outside its deletion scope.

## 🧱 Code organization

```text
bot.py
  └── runtime entry point and cog loading

cogs/
  setup.py                    interactive setup wizard
  analytics.py                administrator-only usage report export
  server_v3.py                build/add/remove/status/year commands
  students.py                 student assignment and history
  teachers.py                  teacher/subject/absence commands
  command_fixes.py            assignteacherfull
  removestream_fix.py         fail-closed stream removal
  section_aware_exam.py       section-aware exam workflow
  section_aware_timetable.py  section-aware timetable workflow
  security_hardening_v3.py    scoped owner-only reset
  year_rollback.py            academic-year rollback

services/
  permissions.py              authorization and permission helpers
  server_builder.py           idempotent Discord reconciliation
  build_transaction.py        transactional build and rollback
  build_guard.py              per-guild mutation lock
  discord_ownership.py        managed-ID ownership validation
  discord_registry.py         managed-resource registry helpers
  removestream_transaction.py crash-safe removal journal
  role_conflicts.py           student/teacher role conflict checks
  storage.py                  SQLite authority and JSON cache
  storage_recovery.py         startup cache recovery
  audit.py                    persistent audit events
  analytics.py                privacy-minimizing usage metrics and CSV exports

docs/
  impact-measurement.md       baseline/post-rollout measurement guide for the school pilot

e2e/
  runner.py                   live Discord observer
  state.py                    deterministic guild snapshots and diffs
  matrix/                     Phase 3 scenario manifests
```

The runtime has one application-command owner per critical command. Shared logic belongs in `services/`; command cogs do not import other command cogs.

## 🧪 Tests and CI

GitHub Actions tests the suite on Python 3.12, exact Python 3.13.2, the latest available Python 3.13 patch release, and Python 3.14. Docker image build, runtime smoke testing, and Docker Compose validation run once per workflow. The Docker image follows the maintained `python:3.13-slim` line.

The test suite covers command registration, permissions, role conflicts, managed-resource ownership, transactional build rollback, persistence recovery, enrollment idempotency, destructive boundaries, section handling, failure recovery, and concurrency semantics.

The live E2E gate is intentionally separate from CI. Discord slash-command execution uses a normal test user account, while the E2E bot is an observer. The repository does not use self-bots or user-token automation.

## 🔐 Configuration and secrets

### Discord Developer Portal prerequisites

The bot enables Discord's **Guild Members privileged intent** because the application uses live guild member state for role-based administration and member discovery. Before starting the bot, open **Developer Portal → Bot → Privileged Gateway Intents** and enable **Server Members Intent**. Discord requires privileged intents to be enabled in the application's settings; verified apps may have additional access requirements. See Discord's current [Privileged Intents documentation](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents) and the [2026 server-data access requirements](https://discord.com/blog/updated-requirements-to-how-apps-access-data-in-servers).

The bot does **not** enable Message Content Intent in code.

Never commit a real Discord bot token. Start from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

`DISCORD_GUILD_ID` is optional. When set, application commands are synchronized to that guild for fast development feedback. When empty, commands are synchronized globally.

Local `.env` files and runtime SQLite/JSON data are ignored by Git.

The live E2E observer uses separate environment variables:

```text
E2E_BOT_TOKEN=<observer bot token>
E2E_GUILD_ID=<dedicated E2E guild id>
```

## 🛠️ Local installation

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-runtime.txt
python bot.py
```

Linux/macOS activation can use `source .venv/bin/activate`.

## 🐳 Docker

The production runtime is intentionally small: it contains only the bot application and runtime dependencies, runs as a non-root `app` user, and keeps mutable SQLite/JSON state in `/app/data`. Docker Compose additionally drops Linux capabilities, disables privilege escalation, mounts the application filesystem read-only, and keeps `/app/data` as the writable persistent volume.

Create `.env` first:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

Run the published GHCR image with Docker Compose:

```bash
docker compose pull
docker compose up -d
docker compose logs -f bot
```

The image is published as `ghcr.io/hm-abdellah/school-discord-manager:latest`. The GitHub Actions publish workflow runs only after the `tests` workflow succeeds on `main`.

The Compose service uses a named volume called `school_manager_data` mounted at `/app/data`, so both the live database and `data/archives/` survive container recreation. Runtime state is intentionally excluded from the image.

Stop the service with:

```bash
docker compose down
```

For a one-off local image build (development/verification only):

```bash
docker build -t school-discord-manager:latest .
docker run --rm --env-file .env -v school_manager_data:/app/data school-discord-manager:latest
```

The published deployment path does not require a local Docker build; the host pulls the image from GHCR. The image does not include the tests or E2E observer harness. Those remain development and verification tooling.

## 🧪 Live E2E verification

The E2E harness intentionally separates command execution from observation:

1. A normal Discord test user performs the slash command in a dedicated test guild.
2. The observer bot captures the real Discord state before and after the action.
3. The snapshots are diffed by Discord ID, including channel permission-overwrite allow/deny masks.

Capture before:

```bash
python -m e2e.runner capture --output .e2e/before.json
```

After the real Discord user performs the scenario:

```bash
python -m e2e.runner capture --output .e2e/after.json
python -m e2e.runner diff --before .e2e/before.json --after .e2e/after.json
```

The Phase 3 gate in `e2e/matrix/index.json` requires all matrix files to be valid, CI to be green, and live scenarios to be executed against a dedicated Discord test guild.

## 🩺 Troubleshooting

### Slash commands do not appear immediately

Set `DISCORD_GUILD_ID` during development and restart the bot. Guild synchronization is much faster than global command propagation.

### Build or teacher-thread sync fails with permissions errors

The bot role needs `Manage Channels` and `Manage Roles`, and must be above School Manager managed roles. For teacher subject channels, its server/category-level permissions must also include `View Channel`, `Read Message History`, `Send Messages`, `Create Public Threads`, and `Manage Threads`; the bot's channel overwrite can only allow permissions Discord already makes available to the bot at the server or parent-category level. If Discord returns `50001 Missing Access` while the bot is trying to repair a hidden channel, a server administrator must first grant the bot member access to that channel/category. Then rerun `/sync-teacher-threads`. Do not reset or re-register the teacher; assignments are retained for retry.

### A managed resource was deleted manually

`/build` can reconcile missing managed resources. `/removestream` uses persisted IDs and will treat an already-deleted exact ID as already deleted; it will not adopt a different same-name resource.

### A destructive command refuses to continue

That behavior is intentional when the persisted managed identity conflicts with another live resource. Reconciliation should be explicit rather than based on name matching.

### An interrupted stream removal is pending

A crash-safe removal journal is stored under `pending_removal`. While that journal exists, the bot blocks other state-changing management commands so a second mutation cannot race with recovery. `/removestream` for the same pending stream remains the recovery path; read-only diagnostics such as `/status`, `/years`, `/adminpanel`, `/serverhealth`, and `/studenthistory` remain available.

### Operational backup

The authoritative runtime state is the SQLite database in `data/school.db`. In Docker, `/app/data` is backed by the named `school_manager_data` volume. In addition to normal operational backups, `/resetserver` creates a pre-reset archive under `data/archives/` for the active academic year before deleting managed resources. Keep external backups of the Docker volume as an additional disaster-recovery layer.

## 🚦 Release gate

A release is considered operationally accepted only when all of the following are true:

- GitHub CI must be green on Python 3.12, 3.13.2, the current 3.13 patch release, and 3.14, including Docker build and runtime smoke tests.
- The Phase 3 live E2E matrices have been executed against a dedicated Discord test guild using the documented two-account model.
- The final operational regression pass has been completed after E2E, including restart/recovery and the destructive-resource boundaries.
- The production `.env` contains the real secret only on the deployment host and is not committed to Git.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py`. Only streams present in that catalogue are accepted by the bot.

## ✅ Delivery gate

The project is ready for final delivery only after:

- all automated tests pass;
- the Docker image builds and starts successfully;
- the dedicated Discord E2E scenarios are executed manually using the two-account observer model;
- the final regression pass confirms command, permission, persistence, and destructive-operation behavior.

The repository currently contains the implementation and CI gate for these checks; the live Discord execution remains an operational acceptance step rather than a GitHub Actions test.

## 📄 License and attribution

Required Notice: Copyright (c) 2026 HM-Abdellah — School Discord Manager (https://github.com/HM-Abdellah/school-discord-manager)

The original project source is offered under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0); see `LICENSE` for the full terms. Educational institutions may use the software for permitted purposes under that license. Commercial use is restricted unless separately authorized by the copyright holder. This is a source-available, noncommercial license, not an OSI-approved Open Source license. Third-party dependencies remain under their own licenses.

The copyright notice identifies the original project; it does not claim ownership of third-party dependencies or of school data. Usage metrics remain on the school host; the bot does not send them to the developer. An administrator can export an aggregate CSV with `/usagereport`.
