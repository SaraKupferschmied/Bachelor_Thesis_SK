import re
from typing import Any


def classify_question(question: str) -> str:
    q = question.lower()

    # Exact structured backend lookups
    if any(x in q for x in [
        "course", "courses", "program", "programs", "degree",
        "bachelor", "master", "doctorate", "phd",
        "ects", "mobility", "soft skills", "soft skill",
        "mandatory", "elective", "semester offerings",
        "offered in semester",
    ]):
        # If it also sounds regulatory/explanatory, go hybrid
        if any(x in q for x in [
            "regulation", "article", "§", "rule", "rules",
            "allowed", "can i", "may i", "exam attempt",
            "absence", "repeat exam", "study plan says",
            "in the study plan",
        ]):
            return "hybrid"
        return "api"

    # Pure regulation / document questions
    if any(x in q for x in [
        "regulation", "article", "§", "exam attempt",
        "absence", "repeat exam", "ordinance", "rule", "rules",
    ]):
        return "rag"

    return "hybrid"


def _extract_program_name(question: str) -> str | None:
    text = question.strip()

    patterns = [
        r"(?:in|for|within)\s+the\s+(.+?)\s+program\b",
        r"(.+?)\s+program\b",
        r"program\s+(.+)$",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip(" ?.,:;")
            candidate = re.sub(
                r"\b(bachelor|master|doctorate|phd|mandatory|elective|autumn|spring|english|german|french)\b",
                "",
                candidate,
                flags=re.IGNORECASE,
            ).strip(" -,:;")
            if candidate and len(candidate) >= 3:
                return candidate

    # fallback for phrases like "Business Informatics bachelor"
    known_programish = re.search(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,4})\b", question
    )
    if known_programish:
        return known_programish.group(1).strip()

    return None


def parse_question(question: str) -> dict[str, Any]:
    q = question.lower()

    result: dict[str, Any] = {
        "course_code": None,
        "mobility": None,
        "soft_skills": None,
        "program_id": None,
        "program_name": None,
        "degree_level": None,
        "course_type": None,
        "semester_type": None,
        "study_start": None,
        "language": None,
        "ects": None,
        "faculty_name": None,
        "domain_name": None,
        "name_contains": None,
        "sem_id": None,
        "wants_programs": False,
        "wants_courses": False,
        "limit": None,
    }

    # Programs intent
    if any(x in q for x in ["list programs", "all programs", "programs", "degrees"]):
        result["wants_programs"] = True

    # Courses intent
    if any(x in q for x in ["course", "courses", "module", "modules"]):
        result["wants_courses"] = True

    # Exact course code
    code_match = re.search(
        r"\b(?:UE-[A-Z0-9]+(?:-[A-Z0-9]+)*\.\d{3,6}|[A-Z]{2,4}\.?\d{3,6})\b",
        question,
        flags=re.IGNORECASE,
    )
    if code_match:
        result["course_code"] = code_match.group(0).replace(" ", "").upper()

    # Flags
    if "mobility" in q:
        result["mobility"] = True

    if "soft skill" in q or "soft skills" in q:
        result["soft_skills"] = True

    # Degree level
    if "bachelor" in q:
        result["degree_level"] = "Bachelor"
    elif "master" in q:
        result["degree_level"] = "Master"
    elif "doctorate" in q or "phd" in q:
        result["degree_level"] = "Doctorate"

    # Course type
    if "mandatory" in q or "pflicht" in q:
        result["course_type"] = "Mandatory"
    elif "elective" in q or "wahl" in q:
        result["course_type"] = "Elective"

    # Semester type / study start
    if "autumn" in q or re.search(r"\bhs\b", q):
        result["semester_type"] = "Autumn"
        result["study_start"] = "Autumn"
    elif "spring" in q or re.search(r"\bfs\b", q):
        result["semester_type"] = "Spring"
        result["study_start"] = "Spring"

    if "both semesters" in q or "both starts" in q:
        result["study_start"] = "Both"

    # Language
    if "english" in q:
        result["language"] = "English"
    elif "german" in q:
        result["language"] = "German"
    elif "french" in q:
        result["language"] = "French"
    elif "italian" in q:
        result["language"] = "Italian"

    # ECTS
    ects_match = re.search(r"\b(\d+(?:\.\d+)?)\s*ects\b", q)
    if ects_match:
        val = float(ects_match.group(1))
        result["ects"] = int(val) if val.is_integer() else val

    # Semester id like FS-2026 / HS-2025
    sem_match = re.search(r"\b(HS|FS)[-_ ]?\d{4}\b", question, flags=re.IGNORECASE)
    if sem_match:
        result["sem_id"] = sem_match.group(0).upper().replace(" ", "-").replace("_", "-")

    # Program id
    prog_match = re.search(r"\bprogram\s+(\d+)\b", q)
    if prog_match:
        result["program_id"] = int(prog_match.group(1))

    # Limit only from explicit list/show/top phrasing
    limit_match = re.search(r"\b(?:top|show|list|name)\s+(\d+)\b", q)
    if limit_match:
        result["limit"] = int(limit_match.group(1))

    # Program name
    if "program" in q or result["degree_level"] or result["course_type"]:
        program_name = _extract_program_name(question)
        if program_name:
            result["program_name"] = program_name

    # Name keyword heuristics
    name_match = re.search(r"(?:named|called|with)\s+['\"]?([^'\"]+)['\"]?", question, flags=re.IGNORECASE)
    if name_match and not result["program_name"]:
        result["name_contains"] = name_match.group(1).strip()

    return result