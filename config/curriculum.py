"""Academic curriculum used by the compact Discord school server."""

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
                "Sciences": {"classes": [f"Classe {i}" for i in range(1, 8)], "matieres": GENERAL_SCIENCES + ["Informatique"]},
                "Technologies": {"classes": ["Classe 1"], "matieres": TECHNOLOGY},
                "Lettres et Sciences Humaines": {"classes": ["Classe 1", "Classe 2"], "matieres": LETTERS_SCIENCES_HUMAINES},
            },
        },
        "1ère Année Bac": {
            "abbreviation": "1BAC",
            "filieres": {
                "Sciences Mathématiques": {"classes": ["Classe 1"], "matieres": GENERAL_SCIENCES},
                "Sciences Expérimentales": {"classes": [f"Classe {i}" for i in range(1, 7)], "matieres": GENERAL_SCIENCES},
                "Sciences et Technologies Électriques": {"classes": ["Classe 1"], "matieres": TECH_BAC},
                "Sciences et Technologies Mécaniques": {"classes": ["Classe 1"], "matieres": TECH_BAC},
                "Sciences Économiques et Gestion": {"classes": ["Classe 1"], "matieres": ECONOMICS},
                "Lettres et Sciences Humaines": {"classes": ["Classe 1", "Classe 2"], "matieres": LETTERS_SCIENCES_HUMAINES},
            },
        },
        "2ème Année Bac": {
            "abbreviation": "2BAC",
            "filieres": {
                "Sciences Mathématiques A": {"classes": ["Classe 1"], "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences Mathématiques B": {"classes": ["Classe 1"], "matieres": TECH_BAC},
                "Sciences Physiques": {"classes": [f"Classe {i}" for i in range(1, 6)], "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences de la Vie et de la Terre (SVT)": {"classes": ["Classe 1"], "matieres": GENERAL_SCIENCES_NO_HISTORY},
                "Sciences Agronomiques": {"classes": ["Classe 1"], "matieres": GENERAL_SCIENCES + ["Sciences Végétales et Animales (SVA)"]},
                "Sciences et Technologies Électriques": {"classes": ["Classe 1"], "matieres": TECH_BAC},
                "Sciences et Technologies Mécaniques": {"classes": ["Classe 1"], "matieres": TECH_BAC},
                "Sciences Économiques": {"classes": ["Classe 1"], "matieres": ECONOMICS},
                "Sciences de Gestion Comptable (SGC)": {"classes": ["Classe 1"], "matieres": ECONOMICS},
                "Lettres": {"classes": ["Classe 1", "Classe 2"], "matieres": LETTERS},
                "Sciences Humaines": {"classes": ["Classe 1", "Classe 2"], "matieres": LETTERS},
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

# Short Discord-safe labels. Discord Forum tag names cannot exceed 50 characters.
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
MAX_CLASSES_PER_STREAM = 20


def get_levels() -> list[str]:
    return list(CURRICULUM["niveaux"])


def get_level(level_name: str) -> dict:
    return CURRICULUM["niveaux"][level_name]


def get_streams(level_name: str) -> list[str]:
    return list(get_level(level_name)["filieres"])


def get_stream(stream_name: str, level_name: str) -> dict:
    return get_level(level_name)["filieres"][stream_name]


def get_stream_subjects(level_name: str, stream_name: str) -> list[str]:
    return list(get_stream(stream_name, level_name)["matieres"])


def get_stream_class_names(level_name: str, stream_name: str) -> list[str]:
    return list(get_stream(stream_name, level_name)["classes"])


def get_level_subjects(level_name: str) -> list[str]:
    subjects: list[str] = []
    for stream in get_streams(level_name):
        for subject in get_stream_subjects(level_name, stream):
            if subject not in subjects:
                subjects.append(subject)
    return subjects


def get_subject_tag(subject: str) -> str:
    return SUBJECT_TAGS.get(subject, subject[:50])
