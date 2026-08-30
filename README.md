# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Current Discord architecture

The server is organized by **level**, then streams are grouped visually inside that level. Discord does not support categories inside categories, so each stream uses compact channel names rather than a nested category.

```text
📘・TRONC COMMUN
├── 📌🔬・TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
└── 📚-TCS・Math / PC / SVT / ...

1️⃣・1BAC
├── 📌🧪・1BACSE・informations
├── 🗓️-1BACSE・emploi-du-temps
├── 📝-1BACSE・examens
└── 📚-1BACSE・Math / PC / SVT / ...

2️⃣・2BAC
└── ...
```

Each stream has one shared academic space for all of its classes/groups. No Discord channel or role is created per class.

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

This separation is important: a teacher can see all subject channels, but can publish in a subject only when they have that subject role. A teacher may teach several levels/streams/subjects at the same time.

Teachers can publish in organizational channels such as information, exams and schedules, but they cannot manage channels or permissions. Administration manages the school structure and schedules. `/resetserver` is restricted to the Discord server owner.

## 📅 Academic years and student history

School data is separated from Discord channels. The bot stores academic years, students and enrollment history in local SQLite at `data/school.db`.

At the start of a new school year:

```text
/newyear 2027/2028
/setup
/build
```

Previous years remain stored for future archive/history features.

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
Add one stream without rebuilding the whole server from scratch.

```text
/removestream
```
Remove one stream and its resources without touching unrelated streams.

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
/resetserver RESET
```
This performs a full server format and is restricted to the server owner.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py`. The project currently includes only the configured streams in that catalogue; unsupported/unused streams such as `TCA` and Arts Appliqués are intentionally omitted for now.

Subject display names are compact for Discord, while full canonical names remain available in the curriculum data.

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
│   ├── server_v2.py
│   ├── students.py
│   └── teachers.py
├── services/
│   ├── __init__.py
│   ├── permissions.py
│   ├── server_builder.py
│   └── storage.py
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
