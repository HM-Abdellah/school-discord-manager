"""Academic catalogue used by the interactive Discord setup wizard.

The catalogue contains the streams and subjects that can be selected for a
school. The number of classes is intentionally NOT stored here: it is chosen
by an administrator during /setup.
"""

from __future__ import annotations


CURRICULUM = {
    "Tronc Commun": {
        "abbreviation": "TC",
        "filieres": {
            "Sciences": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
                "Informatique",
            ],
            "Technologies": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
                "Informatique",
            ],
            "Lettres et Sciences Humaines": [
                "Mathématiques",
                "Sciences de la Vie et de la Terre (SVT)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
        },
    },
    "1ère Année Bac": {
        "abbreviation": "1BAC",
        "filieres": {
            "Sciences Mathématiques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Expérimentales": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences et Technologies Électriques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences et Technologies Mécaniques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Économiques et Gestion": [
                "Mathématiques",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
                "Économie et Organisation Administrative des Entreprises",
                "Comptabilité et Mathématiques financières",
                "Économie générale et Statistiques",
                "Droit",
                "Informatique de gestion",
            ],
            "Lettres et Sciences Humaines": [
                "Mathématiques",
                "Sciences de la Vie et de la Terre (SVT)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
        },
    },
    "2ème Année Bac": {
        "abbreviation": "2BAC",
        "filieres": {
            "Sciences Mathématiques A": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Mathématiques B": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Physiques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la Vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences de la Vie et de la Terre (SVT)": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences de la vie et de la Terre (SVT BIOF)",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Agronomiques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de la Vie et de la Terre (SVT)",
                "Sciences Végétales et Animales (SVA)",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences et Technologies Électriques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences et Technologies Mécaniques": [
                "Mathématiques",
                "Mathématiques (BIOF)",
                "Physique et Chimie",
                "Physique et Chimie (BIOF)",
                "Sciences de l'ingénieur",
                "Arabe",
                "Français",
                "Anglais",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Économiques": [
                "Mathématiques",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
                "Économie et Organisation Administrative des Entreprises",
                "Comptabilité et Mathématiques financières",
                "Économie générale et Statistiques",
                "Droit",
                "Informatique de gestion",
            ],
            "Sciences de Gestion Comptable (SGC)": [
                "Mathématiques",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
                "Économie et Organisation Administrative des Entreprises",
                "Comptabilité et Mathématiques financières",
                "Économie générale et Statistiques",
                "Droit",
                "Informatique de gestion",
            ],
            "Lettres": [
                "Mathématiques",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
            "Sciences Humaines": [
                "Mathématiques",
                "Arabe",
                "Français",
                "Anglais",
                "Histoire Géographie",
                "Education Islamique",
                "Philosophie",
            ],
        },
    },
}

FORUM_TAGS = (
    "Leçons",
    "Actualités Contrôles",
    "Examens Blancs",
)

EXAM_CHANNELS = {
    "1ère Année Bac": "🇲🇦-préparation-régional",
    "2ème Année Bac": "🇲🇦-préparation-national",
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

MAX_CLASSES_PER_STREAM = 20


def get_stream_subjects(level_name: str, stream_name: str) -> list[str]:
    """Return a copy of the subjects for one stream."""
    return list(CURRICULUM[level_name]["filieres"][stream_name])


def get_streams(level_name: str) -> list[str]:
    """Return the available streams for one level."""
    return list(CURRICULUM[level_name]["filieres"])
