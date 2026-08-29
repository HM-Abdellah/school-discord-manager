# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Current Discord architecture

The bot builds one **category per level**. Streams are grouped visually inside that level with locked, non-joinable voice headers; Discord does not support nested categories. Each stream has its own announcement/timetable/exam channels and subject channels.

```text
📘・TRONC COMMUN
├── 🔹・🔬・TCS        ← locked visual stream header
├── 📌-TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
├── 📚-TCS・math
├── 📚-TCS・pc
└── ...

1️⃣・1BAC
├── 🔹・🧪・1BACSE
├── 📌-1BACSE・informations
├── 🗓️-1BACSE・emploi-du-temps
├── 📝-1BACSE・examens
├── 📚-1BACSE・math
└── ...

2️⃣・2BAC
└── ...

🔊・SALLES VIRTUELLES
├── 🔊-TCS-à-distance
├── 🔊-1bacse-à-distance
└── 🔊-2bacpc-à-distance
```

No Discord channels or roles are created for individual classes/groups.

## 👨‍🏫 Teacher roles

Teachers use compact roles:

```text
Prof
Prof (F)
Filière - 1BACSE
Matière - 1BACSE - Math
```

A teacher can be assigned to multiple levels, filières and subjects. Subject roles are stream-specific so a teacher assigned to `1BACSE / Math` cannot publish in `1BACSE / PC` or `2BAC / Math` unless separately assigned.

Teachers can publish in organizational channels such as information, timetable and exams. In subject channels, publishing is restricted to the matching stream-subject role. Teachers cannot manage channels or permissions.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py` under `CURRICULUM["niveaux"]`.

Current active catalogue:

```text
Tronc Commun: TCS, TCL, TCT
1ère Année Bac: 1BACSE, 1BACSM, 1BACSH, 1BACECO, 1BACSTE, 1BACSTM
2ème Année Bac: 2BACPC, 2BACSVT, 2BACSMA, 2BACSMB, 2BACL, 2BACSH, 2BACSE, 2BACSGC
```

`Tronc Commun Originel`, `1ère Année Bac Arts Appliqués`, and `2ème Année Bac Arts Appliqués` are intentionally not active yet.

Display names for subjects stay short on Discord (`Math`, `PC`, `SVT`, `العربية`, `الاجتماعيات`, `التربية الإسلامية`, `الفلسفة`, etc.) while the full canonical names remain in the curriculum code.

## 🛠️ Commands

```text
/setup
/build
/addstream
/removestream
/status
/newyear 2027/2028
/years
/assignstudent
/studenthistory
/leave_school
/assignteacher
/assignsubjectteachers
/reportabsence
/resetserver   ← server owner only
```

`/build` is non-destructive: it reconciles the configured structure without formatting the whole server.

`/addstream` adds one configured stream without resetting the server.

`/removestream` removes one configured stream and its managed resources.

`/resetserver` is a development/emergency command reserved for the Discord server owner.

## 📅 Academic years and student history

School data is separated from Discord channels. The bot stores configuration and academic history locally in SQLite at `data/school.db`.

Students keep historical enrollments when transferred or marked as having left the school.

## 🔐 Security

Never commit `.env` or real school data.

The repository ignores `.env`, local JSON configuration and SQLite/database files. The reusable `Administration` role is intentionally not granted Discord's full `Administrator` permission.

## 🛠️ Local installation

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

For development, put your bot token and test server ID in `.env`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

After updates:

```bash
git pull origin main
python bot.py
```
