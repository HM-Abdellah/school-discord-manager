# 🏫 School Discord Manager

An open-source Discord bot for generating and managing a structured school server.

The project is designed around a configurable Moroccan secondary-school structure with:

- Tronc Commun
- 1ère Année Bac
- 2ème Année Bac
- Selectable streams (filières)
- Configurable number of classes per stream
- Subject forums with educational tags
- Private class areas
- Teacher-only private area
- Regional and national exam channels
- Virtual classroom voice channels
- Student and teacher role management

## ✨ Main idea

The school structure is **configured interactively inside Discord** instead of hard-coding the number of classes.

An administrator runs:

```text
/setup
```

The wizard lets the administrator choose the level, select only the streams available at the school, and choose the number of classes for every selected stream.

The configuration is then saved locally and can be built with:

```text
/build
```

## 📁 Project structure

```text
school-discord-manager/
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
│
├── config/
│   ├── __init__.py
│   └── curriculum.py
│
├── cogs/
│   ├── __init__.py
│   ├── setup.py
│   ├── server.py
│   ├── students.py
│   └── teachers.py
│
├── services/
│   ├── __init__.py
│   ├── permissions.py
│   ├── server_builder.py
│   └── storage.py
│
└── data/
    └── (generated local JSON files)
```

## 🔐 Token security

Never put your real Discord bot token in GitHub.

Create a local `.env` file from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

`.env` is ignored by Git through `.gitignore`.

`DISCORD_GUILD_ID` is optional. During development, setting it makes slash-command synchronization happen on that server immediately. Leave it empty when you want global synchronization.

## 🛠️ Local installation

Python 3.8+ is required. The project is intended to run with current supported Python 3 versions, including Python 3.13.

### 1. Clone the repository

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure the bot token

```powershell
Copy-Item .env.example .env
```

Open `.env` and set your own Discord bot token. Do not commit the `.env` file.

### 5. Discord Developer Portal

Enable these privileged gateway intents for the bot:

- Server Members Intent
- Message Content Intent

Invite the bot to your test server with the permissions required to create and manage the configured roles and channels. For initial development, Administrator is the simplest option.

Forum channels require the Discord server configuration needed for community/forum functionality.

### 6. Run

```bash
python bot.py
```

## 🤖 Commands

### `/setup`

Interactive setup wizard:

1. Select a school level.
2. Select the streams available in your school.
3. Choose the number of classes for each selected stream.
4. Review the configuration.
5. Confirm and build the server.

### `/build`

Builds or reconciles the saved configuration on the current server. Existing matching resources are reused when possible.

### `/status`

Shows the saved configuration and estimated class/forum counts.

### `/assignstudent`

Assigns a student to one of the generated class roles and removes any previous School Discord Manager class role from that student.

### `/assignteacher`

Assigns the `Professeur` role to a member.

### `/reportabsence`

Publishes a teacher-absence announcement in the configured institution channel.

## 📚 Curriculum catalogue

The stream and subject catalogue is stored in `config/curriculum.py`.

The catalogue defines **what exists academically**. It intentionally does not define the number of classes: class counts are selected through `/setup`.

## 🧭 Roadmap

- Persistent database instead of JSON storage
- Teacher-to-class assignment
- Automatic absence notifications to affected classes
- Timetable management
- More granular subject permissions
- Audit logs
- Web dashboard
- Automated tests and CI

## 📜 License

This project is open source. Add the license that matches how you want the project to be reused.
