import re
from typing import Any, Optional


def classify_question(question: str) -> str:
    q = question.lower()

    if any(x in q for x in ["course", "courses", "mobility", "soft skills", "module code"]):
        return "api"

    if any(x in q for x in ["program", "degree", "bachelor", "master", "semester offerings"]):
        return "api"

    if any(x in q for x in ["regulation", "article", "§", "exam attempt", "absence", "repeat exam"]):
        return "rag"

    if any(x in q for x in ["mandatory", "in the study plan", "part of semester", "offered in semester"]):
        return "hybrid"

    return "hybrid"

def parse_question(question: str) -> dict[str, Any]:
    q = question.lower()

    result: dict[str, Any] = {
        "course_code": None,
        "mobility": None,
        "soft_skills": None,
        "program_id": None,
        "sem_id": None,
        "wants_programs": False,
        "limit": None,
    }

    if "list programs" in q or "all programs" in q or "programs" in q:
        result["wants_programs"] = True

    code_match = re.search(
        r"\b(?:UE-[A-Z0-9]+(?:-[A-Z0-9]+)*\.\d{3,6}|[A-Z]{2,4}\s?\d{3,4})\b",
        question,
        flags=re.IGNORECASE,
    )
    if code_match:
        result["course_code"] = code_match.group(0).replace(" ", "").upper()

    if "mobility" in q:
        result["mobility"] = True

    if "soft skill" in q or "soft skills" in q:
        result["soft_skills"] = True

    sem_match = re.search(r"\b(HS|FS)[-_ ]?\d{4}\b", question, flags=re.IGNORECASE)
    if sem_match:
        result["sem_id"] = sem_match.group(0).upper().replace(" ", "-").replace("_", "-")

    prog_match = re.search(r"\bprogram\s+(\d+)\b", q)
    if prog_match:
        result["program_id"] = int(prog_match.group(1))

    num_match = re.search(r"\b(\d+)\b", q)
    if num_match:
        result["limit"] = int(num_match.group(1))
        
    return result