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

### Administration and destructive maintenance

```text
/adminpanel
/serverhealth
/resetserver RESET SCHOOL MANAGER
```

`/resetserver` is scoped to persisted School Manager resource IDs. Unmanaged Discord resources are outside its deletion scope.

## 🧱 Code organization

```text
bot.py
  └── runtime entry point and cog loading

cogs/
  setup.py                    interactive setup wizard
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

e2e/
  runner.py                   live Discord observer
  state.py                    deterministic guild snapshots and diffs
  matrix/                     Phase 3 scenario manifests
```

The runtime has one application-command owner per critical command. Shared logic belongs in `services/`; command cogs do not import other command cogs.

## 🧪 Tests and CI

GitHub Actions runs the Python test suite on Python 3.12 and 3.13. The CI job also builds the Docker image and runs a smoke test against the resulting runtime image.

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
python -m pip install -r requirements.txt
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

Build and run with Docker Compose:

```bash
docker compose build
docker compose up -d
docker compose logs -f bot
```

The Compose service uses a named volume called `school_manager_data` mounted at `/app/data`, so the database survives container recreation.

Stop the service with:

```bash
docker compose down
```

For a one-off image build:

```bash
docker build -t school-discord-manager:latest .
docker run --rm --env-file .env -v school_manager_data:/app/data school-discord-manager:latest
```

The image does not include the tests or E2E observer harness. Those remain development and verification tooling.

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

### Build fails with permissions errors

Verify that the bot has `Manage Channels` and `Manage Roles`, and that the bot role is above the School Manager managed roles.

### A managed resource was deleted manually

`/build` can reconcile missing managed resources. `/removestream` uses persisted IDs and will treat an already-deleted exact ID as already deleted; it will not adopt a different same-name resource.

### A destructive command refuses to continue

That behavior is intentional when the persisted managed identity conflicts with another live resource. Reconciliation should be explicit rather than based on name matching.

### An interrupted stream removal is pending

A crash-safe removal journal is stored under `pending_removal`. While that journal exists, the bot blocks other state-changing management commands so a second mutation cannot race with recovery. `/removestream` for the same pending stream remains the recovery path; read-only diagnostics such as `/status`, `/years`, `/adminpanel`, `/serverhealth`, and `/studenthistory` remain available.

### Operational backup

The authoritative runtime state is the SQLite database in `data/school.db`. In Docker, `/app/data` is backed by the named `school_manager_data` volume. Back up that volume before major maintenance or destructive server operations, and keep the backup outside the running container.

## 🚦 Release gate

A release is considered operationally accepted only when all of the following are true:

- GitHub CI is green on Python 3.12 and 3.13, including Docker build and runtime smoke tests.
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

## 📄 License

No license file is currently declared in the repository.
