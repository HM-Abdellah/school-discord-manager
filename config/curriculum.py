"""Academic curriculum and display names for the stream-based school Discord structure."""

from __future__ import annotations

GENERAL_SCIENCES = [
    "Mathématiques", "Physique et Chimie", "Sciences de la Vie et de la Terre (SVT)",
    "Arabe", "Français", "Anglais", "Histoire Géographie", "Education Islamique", "Philosophie",
]
GENERAL_SCIENCES_NO_HISTORY = [
    "Mathématiques", "Physique et Chimie", "Sciences de la Vie et de la Terre (SVT)",
    "Arabe", "Français", "Anglais", "Education Islamique", "Philosophie",
]
TECHNOLOGY = [
    "Mathématiques", "Physique et Chimie", "Sciences de l'ingénieur", "Arabe", "Français",
    "Anglais", "Histoire Géographie", "Education Islamique", "Philosophie", "Informatique",
]
TECH_BAC = [
    "Mathématiques", "Physique et Chimie", "Sciences de l'ingénieur", "Arabe", "Français",
    "Anglais", "Education Islamique", "Philosophie",
]
LETTERS = [
    "Mathématiques", "Arabe", "Français", "Anglais", "Histoire Géographie", "Education Islamique", "Philosophie",
]
LETTERS_SCIENCES_HUMAINES = [
    "Mathématiques", "Sciences de la Vie et de la Terre (SVT)", "Arabe", "Français", "Anglais",
    "Histoire Géographie", "Education Islamique", "Philosophie",
]
ECONOMICS = [
    "Mathématiques", "Arabe", "Français", "Anglais", "Histoire Géographie", "Education Islamique", "Philosophie",
    "Économie et Organisation Administrative des Entreprises", "Comptabilité et Mathématiques financières",
    "Économie générale et Statistiques", "Droit", "Informatique de gestion",
]

# Canonical subject names are kept complete internally. These are display-only names for Discord.
SUBJECT_DISPLAY_NAMES = {
    "Mathématiques": "Math",
    "Physique et Chimie": "PC",
    "Sciences de la Vie et de la Terre (SVT)": "SVT",
    "Arabe": "العربية",
    "Français": "Français",
    "Anglais": "English",
    "Histoire Géographie": "الاجتماعيات",
    "Education Islamique": "التربية الإسلامية",
    "Philosophie": "الفلسفة",
    "Sciences de l'ingénieur": "SI",
    "Informatique": "Info",
    "Économie et Organisation Administrative des Entreprises": "Économie & Organisation",
    "Comptabilité et Mathématiques financières": "Comptabilité & Maths Fi.",
    "Économie générale et Statistiques": "Économie & Stats",
    "Droit": "Droit",
    "Informatique de gestion": "Info Gestion",
    "Sciences Végétales et Animales (SVA)": "SVA",
}

# Compact internal subject codes used by teacher roles and automation.
SUBJECT_INTERNAL_CODES = {
    "Mathématiques": "Math",
    "Physique et Chimie": "PC",
    "Sciences de la Vie et de la Terre (SVT)": "SVT",
    "Arabe": "Arabe",
    "Français": "Francais",
    "Anglais": "English",
    "Histoire Géographie": "Sociales",
    "Education Islamique": "Islamique",
    "Philosophie": "Philo",
    "Sciences de l'ingénieur": "SI",
    "Informatique": "Info",
    "Économie et Organisation Administrative des Entreprises": "ECO-Organisation",
    "Comptabilité et Mathématiques financières": "Compta-MathsFi",
    "Économie générale et Statistiques": "ECO-Stats",
    "Droit": "Droit",
    "Informatique de gestion": "InfoGestion",
    "Sciences Végétales et Animales (SVA)": "SVA",
}

# Official stream abbreviations supplied for this project.
STREAM_ABBREVIATIONS = {
    "Tronc Commun Scientifique": "TCS",
    "Tronc Commun Lettres": "TCL",
    "Tronc Commun Technologique": "TCT",
    "1ère Année Bac Sciences Expérimentales": "1BACSE",
    "1ère Année Bac Sciences Mathématiques": "1BACSM",
    "1ère Année Bac Lettres et Sciences Humaines": "1BACSH",
    "1ère Année Bac Sciences Économiques et Gestion": "1BACECO",
    "1ère Année Bac Sciences et Technologies Électriques": "1BACSTE",
    "1ère Année Bac Sciences et Technologies Mécaniques": "1BACSTM",
    "2ème Année Bac Sciences Physiques": "2BACPC",
    "2ème Année Bac Sciences de la Vie et de la Terre": "2BACSVT",
    "2ème Année Bac Sciences Mathématiques A": "2BACSMA",
    "2ème Année Bac Sciences Mathématiques B": "2BACSMB",
    "2ème Année Bac Lettres": "2BACL",
    "2ème Année Bac Sciences Humaines": "2BACSH",
    "2ème Année Bac Sciences Économiques": "2BACSE",
    "2ème Année Bac Sciences de Gestion Comptable": "2BACSGC",
}

CURRICULUM = {
    "niveaux": {
        "Tronc Commun": {
            "abbreviation": "TC",
            "filieres": {
                "Tronc Commun Scientifique": {"abbreviation": "TCS", "matieres": GENERAL_SCIENCES + ["Informatique"]},
                "Tronc Commun Lettres": {"abbreviation": "TCL", "matieres": LETTERS_SCIENCES_HUMAINES},
                "Tronc Commun Technologique": {"abbreviation": "TCT", "matieres": TECHNOLOGY},
            },
        },
        "1ère Année Bac": {
            "abbreviation": "1BAC",
            "filieres": {
                "1ère Année Bac Sciences Expérimentales": {"abbreviation": "1BACSE", "matieres": GENERAL_SCIENCES},
                "1ère Année Bac Sciences Mathématiques": {"abbreviation": "1BACSM", "matieres": GENERAL_SCIENCES},
                "1ère Année Bac Lettres et Sciences Humaines": {"abbreviation": "1BACSH", "matieres": LETTERS_SCIENCES_HUMAINES},
                "1ère Année Bac Sciences Économiques et Gestion": {"abbreviation": "1BACECO", "matieres": ECONOMICS},
                "1ère Année Bac Sciences et Technologies Électriques": {"abbreviation": "1BACSTE", "matieres": TECH_BAC},
                "1ère Année Bac Sciences et Technologies Mécaniques": {"abbreviation": "1BACSTM", "matieres": TECH_BAC},
            },
        },
        "2ème Année Bac": {
            "abbreviation": "2BAC",
            "filieres": {
                "2ème Année Bac Sciences Physiques": {"abbreviation": "2BACPC", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "2ème Année Bac Sciences de la Vie et de la Terre": {"abbreviation": "2BACSVT", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "2ème Année Bac Sciences Mathématiques A": {"abbreviation": "2BACSMA", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "2ème Année Bac Sciences Mathématiques B": {"abbreviation": "2BACSMB", "matieres": TECH_BAC},
                "2ème Année Bac Lettres": {"abbreviation": "2BACL", "matieres": LETTERS},
                "2ème Année Bac Sciences Humaines": {"abbreviation": "2BACSH", "matieres": LETTERS},
                "2ème Année Bac Sciences Économiques": {"abbreviation": "2BACSE", "matieres": ECONOMICS},
                "2ème Année Bac Sciences de Gestion Comptable": {"abbreviation": "2BACSGC", "matieres": ECONOMICS},
            },
        },
    }
}

GENERAL_CHANNELS = {
    "actualites": "📢-actualités-institution",
    "absences": "👨‍🏫-absences-professeurs",
    "results": "📊-résultats-et-annonces",
    "post_bac": "🎓-opportunités-post-bac",
    "contests": "🏆-concours-et-activités",
}

PROFESSOR_CHANNELS = {
    "discussion": "💬-discussion-professeurs",
    "meeting": "🔊-réunion-professeurs",
}

EXAM_CHANNELS = {
    "1ère Année Bac": "🇲🇦-préparation-régional",
    "2ème Année Bac": "🇲🇦-préparation-national",
}

SUBJECT_TAGS = dict(SUBJECT_DISPLAY_NAMES)
FORUM_MAX_TAGS = 20


def get_levels() -> list[str]:
    return list(CURRICULUM["niveaux"])


def get_level(level_name: str) -> dict:
    return CURRICULUM["niveaux"][level_name]


def get_streams(level_name: str) -> list[str]:
    return list(get_level(level_name)["filieres"])


def get_stream(stream_name: str, level_name: str) -> dict:
    return get_level(level_name)["filieres"][stream_name]


def get_stream_abbreviation(level_name: str, stream_name: str) -> str:
    return get_stream(stream_name, level_name).get("abbreviation") or STREAM_ABBREVIATIONS.get(stream_name, stream_name[:20])


def get_stream_subjects(level_name: str, stream_name: str) -> list[str]:
    return list(get_stream(stream_name, level_name)["matieres"])


def get_level_subjects(level_name: str) -> list[str]:
    subjects: list[str] = []
    for stream in get_streams(level_name):
        for subject in get_stream_subjects(level_name, stream):
            if subject not in subjects:
                subjects.append(subject)
    return subjects


def get_subject_display_name(subject: str) -> str:
    return SUBJECT_DISPLAY_NAMES.get(subject, subject)


def get_subject_internal_code(subject: str) -> str:
    return SUBJECT_INTERNAL_CODES.get(subject, subject[:30])


def get_subject_tag(subject: str) -> str:
    return get_subject_display_name(subject)
