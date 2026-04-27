# chatbot/app/hero_study_program.py
import re
from typing import Any, Dict


def is_plan_study_program_hero(question: str) -> bool:
    return question.strip().lower() == "__hero__:plan_study_program"


def start_plan_study_program_flow(session_state: Dict[str, Any]) -> Dict[str, Any]:
    session_state["hero_flow"] = {
        "name": "plan_study_program",
        "step": "awaiting_program_and_duration",
    }

    return {
        "answer": (
            "Sure — which study program should I plan?\n\n"
            "Please tell me:\n"
            "1. your study program, for example **Bachelor Business Informatics**\n"
            "2. in how many semesters you want to finish, for example **6 semesters**"
        ),
        "sources": [],
        "used_tools": [],
        "session_state": session_state,
        "plan": {"mode": "hero", "hero": "plan_study_program"},
        "planning_errors": None,
    }


def extract_semester_count(text: str) -> int | None:
    q = text.lower()
    match = re.search(r"\b(\d{1,2})\s*(semester|semesters|sems|sem)\b", q)
    if match:
        return int(match.group(1))

    years = re.search(r"\b(\d{1,2})\s*(year|years|jahr|jahre)\b", q)
    if years:
        return int(years.group(1)) * 2

    return None


def infer_degree_level(text: str) -> str | None:
    q = text.lower()
    if "bachelor" in q:
        return "Bachelor"
    if "master" in q:
        return "Master"
    if "doctorate" in q or "phd" in q:
        return "Doctorate"
    return None
