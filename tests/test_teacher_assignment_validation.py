import pytest

from services.teacher_assignment import TeacherAssignmentError, execute_teacher_assignment


@pytest.mark.asyncio
async def test_teacher_assignment_rejects_invalid_level_before_discord_mutations(monkeypatch):
    monkeypatch.setattr("services.teacher_assignment.teacher_target_conflict", lambda _member, _guild: None)

    class Guild:
        id = 1

    class Teacher:
        bot = False
        roles = []

    with pytest.raises(TeacherAssignmentError, match="Niveau ou filière invalide"):
        await execute_teacher_assignment(
            guild=Guild(),
            teacher=Teacher(),
            gender_value="male",
            level="__invalid_level__",
            stream="__invalid_stream__",
            subjects="Mathématiques",
            actor_id=101,
            actor_display_name="Prof Test",
            self_registration=True,
        )


@pytest.mark.asyncio
async def test_teacher_assignment_rejects_invalid_section_before_discord_mutations(monkeypatch):
    monkeypatch.setattr("services.teacher_assignment.teacher_target_conflict", lambda _member, _guild: None)

    class Guild:
        id = 1

    class Teacher:
        bot = False
        roles = []

    with pytest.raises(TeacherAssignmentError, match="section doit être compris"):
        await execute_teacher_assignment(
            guild=Guild(),
            teacher=Teacher(),
            gender_value="male",
            level="2ème Année Bac",
            stream="2ème Année Bac Sciences Physiques",
            subjects="Mathématiques",
            actor_id=101,
            actor_display_name="Prof Test",
            self_registration=True,
            assignments=[{
                "level": "2ème Année Bac",
                "stream": "2ème Année Bac Sciences Physiques",
                "section": 9,
                "subjects": ["Mathématiques"],
            }],
        )


@pytest.mark.asyncio
async def test_teacher_assignment_validates_every_stream_and_subject_before_discord_mutations(monkeypatch):
    monkeypatch.setattr("services.teacher_assignment.teacher_target_conflict", lambda _member, _guild: None)

    class Guild:
        id = 1

    class Teacher:
        bot = False
        roles = []

    with pytest.raises(TeacherAssignmentError, match="Aucune matière reconnue"):
        await execute_teacher_assignment(
            guild=Guild(),
            teacher=Teacher(),
            gender_value="male",
            level="2ème Année Bac",
            stream="2ème Année Bac Sciences Physiques",
            subjects="Mathématiques",
            actor_id=101,
            actor_display_name="Prof Test",
            self_registration=True,
            assignments=[
                {
                    "level": "2ème Année Bac",
                    "stream": "2ème Année Bac Sciences Physiques",
                    "section": 3,
                    "subjects": ["Mathématiques"],
                },
                {
                    "level": "1ère Année Bac",
                    "stream": "1ère Année Bac Sciences Expérimentales",
                    "section": 2,
                    "subjects": ["__not_a_subject__"],
                },
            ],
        )
