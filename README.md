# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Current Discord architecture

The server uses a **real Discord category for every stream**. This makes the stream name a true title instead of creating a fake writable/readonly title channel.

```text
📘・TC・🔬 TCS
├── 📌-TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
└── 📚-TCS・Math / PC / SVT / ...

📘・TC・📩 TCL
├── 📌-TCL・informations
├── 🗓️-TCL・emploi-du-temps
├── 📝-TCL・examens
└── 📚-TCL・...

1️⃣・1BAC・🧪 1BACSE
└── ...
```

Each stream has one shared academic space for all of its classes/groups. No Discord channel or role is created per class.

The builder is idempotent: running `/build` again reconciles only resources whose state actually differs. Discord operations are bounded and logged so a stalled API call is visible instead of looking like a silent freeze.

## 👥 Roles and permissions

Main roles:

- `Administration`
- `Prof`
- `Prof (F)`
- `Élève`

For each stream, the bot creates separate roles for teachers and students:

- `Filière - 1BACSE` → teacher stream role
- `Élèves - 1BACSE` → student stream role
- `Matière - 1BACSE - Math` → teacher role scoped to one subject and one stream

The management role is authorized by its **Discord role ID stored in the guild configuration**, not by role name alone. The server owner remains an emergency management path.

## 📅 Academic years and student history

School data is separated from Discord channels. The bot stores academic years, students and enrollment history in local SQLite at `data/school.db`.

Enrollment operations are idempotent: assigning a student to the same active stream again does not create a duplicate history record. SQLite also enforces one active enrollment per student.

At the start of a new school year:

```text
/newyear 2027/2028
/setup
/build
```

Previous years remain stored for archive/history features.

## ⚙️ Setup and maintenance commands

```text
/setup
```
Configure the levels and only the streams actually present in the school.

```text
/build
```
Reconcile the current configuration without formatting the server.

```text
/addstream
```
Add one stream using the same locked/idempotent build pipeline.

```text
/removestream
```
Remove only one configured stream, its dedicated category, voice room and managed roles.

```text
/status
/years
/newyear 2027/2028
```
Inspect configuration and manage academic years.

Teacher/student administration:

```text
/assignteacher
/assignsubjectteachers
/assignstudent
/studenthistory
/leave_school
/reportabsence
```

Dangerous maintenance:

```text
/resetserver RESET SCHOOL MANAGER
```
This is restricted to the Discord server owner and targets only resources recorded as School Manager managed resources. It does **not** format unrelated server channels/categories.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py`. The project currently includes only the configured streams in that catalogue; unsupported/unused streams such as `TCA` and Arts Appliqués are intentionally omitted for now.

## 🧪 CI and tests

GitHub Actions runs one test workflow on pushes and pull requests. Tests cover builder capacity, stream-category naming, permission authorization, configuration consistency, storage durability, enrollment idempotency and duplicate cleanup.

## 📁 Project structure

```text
school-discord-manager/
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── config/
│   ├── __init__.py
│   └── curriculum.py
├── cogs/
│   ├── __init__.py
│   ├── setup.py
│   ├── server_v3.py
│   ├── students.py
│   └── teachers.py
├── services/
│   ├── __init__.py
│   ├── audit.py
│   ├── build_guard.py
│   ├── permissions.py
│   ├── server_builder.py
│   └── storage.py
├── tests/
└── data/
    └── .gitkeep
```

Local `.env` and SQLite/JSON data are ignored by Git and must never contain secrets in commits.

## 🔐 Token security

Never commit your real Discord token.

Create `.env` from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
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

The bot uses `discord.py` application commands and disables the message-content intent because the current command architecture does not need it.
