"""Academic curriculum for the stream-based school Discord structure."""

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

CURRICULUM = {
    "niveaux": {
        "Tronc Commun": {
            "abbreviation": "TC",
            "filieres": {
                "Sciences": {"code": "TCSF", "matieres": GENERAL_SCIENCES + ["Informatique"]},
                "Technologies": {"code": "TCT", "matieres": TECHNOLOGY},
                "Lettres et Sciences Humaines": {"code": "TCSH", "matieres": LETTERS_SCIENCES_HUMAINES},
            },
        },
        "1ère Année Bac": {
            "abbreviation": "1BAC",
            "filieres": {
                "Sciences Mathématiques": {"code": "1BACSM", "matieres": GENERAL_SCIENCES},
                "Sciences Expérimentales": {"code": "1BACSEF", "matieres": GENERAL_SCIENCES},
                "Sciences et Technologies Électriques": {"code": "1BACSTE", "matieres": TECH_BAC},
                "Sciences et Technologies Mécaniques": {"code": "1BACSTM", "matieres": TECH_BAC},
                "Sciences Économiques et Gestion": {"code": "1BACSEG", "matieres": ECONOMICS},
                "Lettres et Sciences Humaines": {"code": "1BACLH", "matieres": LETTERS_SCIENCES_HUMAINES},
            },
        },
        "2ème Année Bac": {
            "abbreviation": "2BAC",
            "filieres": {
                "Sciences Mathématiques A": {"code": "2BACSMA", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences Mathématiques B": {"code": "2BACSMB", "matieres": TECH_BAC},
                "Sciences Physiques": {"code": "2BACSP", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences de la Vie et de la Terre (SVT)": {"code": "2BACSVT", "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences Agronomiques": {"code": "2BACSA", "matieres": GENERAL_SCIENCES + ["Sciences Végétales et Animales (SVA)"]},
                "Sciences et Technologies Électriques": {"code": "2BACSTE", "matieres": TECH_BAC},
                "Sciences et Technologies Mécaniques": {"code": "2BACSTM", "matieres": TECH_BAC},
                "Sciences Économiques": {"code": "2BACSE", "matieres": ECONOMICS},
                "Sciences de Gestion Comptable (SGC)": {"code": "2BACSGC", "matieres": ECONOMICS},
                "Lettres": {"code": "2BACL", "matieres": LETTERS},
                "Sciences Humaines": {"code": "2BACSH", "matieres": LETTERS},
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

SUBJECT_TAGS = {
    "Mathématiques": "Mathématiques",
    "Physique et Chimie": "Physique-Chimie",
    "Sciences de la Vie et de la Terre (SVT)": "SVT",
    "Sciences de l'ingénieur": "Sc. ingénieur",
    "Arabe": "Arabe",
    "Français": "Français",
    "Anglais": "Anglais",
    "Histoire Géographie": "Histoire-Géo",
    "Education Islamique": "Éducation islamique",
    "Philosophie": "Philosophie",
    "Informatique": "Informatique",
    "Économie et Organisation Administrative des Entreprises": "Économie & Organisation",
    "Comptabilité et Mathématiques financières": "Comptabilité & Maths fi.",
    "Économie générale et Statistiques": "Économie générale & Stats",
    "Droit": "Droit",
    "Informatique de gestion": "Informatique de gestion",
    "Sciences Végétales et Animales (SVA)": "SVA",
}

FORUM_MAX_TAGS = 20


def get_levels() -> list[str]:
    return list(CURRICULUM["niveaux"])


def get_level(level_name: str) -> dict:
    return CURRICULUM["niveaux"][level_name]


def get_streams(level_name: str) -> list[str]:
    return list(get_level(level_name)["filieres"])


def get_stream(stream_name: str, level_name: str) -> dict:
    return get_level(level_name)["filieres"][stream_name]


def get_stream_code(level_name: str, stream_name: str) -> str:
    return str(get_stream(stream_name, level_name)["code"])


def get_stream_subjects(level_name: str, stream_name: str) -> list[str]:
    return list(get_stream(stream_name, level_name)["matieres"])


def get_level_subjects(level_name: str) -> list[str]:
    subjects: list[str] = []
    for stream in get_streams(level_name):
        for subject in get_stream_subjects(level_name, stream):
            if subject not in subjects:
                subjects.append(subject)
    return subjects


def get_subject_tag(subject: str) -> str:
    return SUBJECT_TAGS.get(subject, subject[:50])
