# chatbot/app/hero_semester.py
from typing import Any, Dict


def is_plan_semester_hero(question: str) -> bool:
    return question.strip().lower() == "__hero__:plan_semester"


def start_plan_semester_flow(session_state: Dict[str, Any]) -> Dict[str, Any]:
    session_state["hero_flow"] = {
        "name": "plan_semester",
        "step": "awaiting_semester_program_and_level",
    }

    return {
        "answer": (
            "Sure — which semester would you like to plan?\n\n"
            "Please tell me:\n"
            "1. the semester, for example **FS-2026** or **HS-2026**\n"
            "2. your study program or direction"
        ),
        "sources": [],
        "used_tools": [],
        "session_state": session_state,
        "plan": {
            "mode": "hero",
            "hero": "plan_semester",
        },
        "planning_errors": None,
    }