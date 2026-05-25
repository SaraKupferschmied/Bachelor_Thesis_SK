# chatbot/app/hero_semester.py
from typing import Any, Dict


def is_plan_semester_hero(question: str) -> bool:
    return question.strip().lower() == "__hero__:plan_semester"


def _localized_plan_semester_intro(language: str | None) -> str:
    if language == "de":
        return (
            "Gerne — welches Semester möchtest du planen?\n\n"
            "Bitte sag mir:\n"
            "1. das Semester, zum Beispiel **FS-2026** oder **HS-2026**\n"
            "2. dein Studienprogramm oder deine Studienrichtung"
        )

    if language == "fr":
        return (
            "Bien sûr — quel semestre souhaites-tu planifier ?\n\n"
            "Indique-moi s’il te plaît :\n"
            "1. le semestre, par exemple **FS-2026** ou **HS-2026**\n"
            "2. ton programme d’études ou ton orientation"
        )

    return (
        "Sure — which semester would you like to plan?\n\n"
        "Please tell me:\n"
        "1. the semester, for example **FS-2026** or **HS-2026**\n"
        "2. your study program or direction"
    )


def start_plan_semester_flow(session_state: Dict[str, Any], language: str | None = None) -> Dict[str, Any]:
    session_state["hero_flow"] = {
        "name": "plan_semester",
        "step": "awaiting_semester_program_and_level",
    }

    return {
        "answer": _localized_plan_semester_intro(language),
        "sources": [],
        "used_tools": [],
        "session_state": session_state,
        "plan": {
            "mode": "hero",
            "hero": "plan_semester",
        },
        "planning_errors": None,
    }