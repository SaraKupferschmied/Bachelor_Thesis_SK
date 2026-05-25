import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

from .backend_tools import TOOL_SPECS
from .config import settings
from .performance import timed_step


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    if "```" in text:
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            try:
                return json.loads(candidate)
            except Exception:
                continue

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end + 1]
        return json.loads(candidate)

    raise ValueError("No valid JSON found in planner output")


LANGUAGE_WORDS = {
    "en": ["english", "anglais", "englisch"],
    "de": ["german", "deutsch", "allemand"],
    "fr": ["french", "français", "francais", "franzoesisch", "französisch"],
}

STRUCTURED_SYNONYMS = {
    "course": ["course", "courses", "module", "modules", "kurs", "kurse", "cours", "enseignement", "enseignements"],
    "program": ["program", "programs", "programme", "programmes", "study program", "study programmes", "studiengang", "studiengänge", "studiengaenge", "studienprogramm", "programme d'études", "programme d’etudes"],
    "mandatory": ["mandatory", "required", "pflicht", "pflichtfach", "obligatorisch", "obligatoire"],
    "elective": ["elective", "wahl", "wahlfach", "optionnel", "à option", "a option"],
    "autumn": ["autumn", "fall", "herbst", "herbstsemester", "automne", "semestre d'automne"],
    "spring": ["spring", "frühling", "fruehling", "frühlingssemester", "fruehlingssemester", "printemps", "semestre de printemps"],
    "bachelor": ["bachelor", "bachelorstudium", "baccalauréat", "baccalaureat"],
    "master": ["master", "masterstudium", "maîtrise", "maitrise"],
    "doctorate": ["doctorate", "phd", "doktorat", "doctorat"],
}


def _contains_any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _detect_query_language(question: str) -> str:
    q = question.lower()
    if any(x in q for x in ["welche", "welcher", "studiengang", "kurse", "pflicht", "wahl", "deutsch", "herbst", "frühling", "fruehling"]):
        return "de"
    if any(x in q for x in ["quels", "quelles", "programme", "cours", "obligatoire", "optionnel", "français", "francais", "automne", "printemps"]):
        return "fr"
    return "en"


def _localized_program_args(program_name: str | None, question: str) -> dict[str, str]:
    if not program_name:
        return {}
    lang = _detect_query_language(question)
    key = {"de": "program_de", "fr": "program_fr"}.get(lang, "program_en")
    # q is a broad OR search in the backend. Keeping the language-specific field
    # preserves precision while q makes mixed-language metadata robust.
    return {key: program_name, "q": program_name}


def _extract_requested_course_language(question: str) -> str | None:
    q = question.lower()
    if _contains_any(q, LANGUAGE_WORDS["en"]):
        return "English"
    if _contains_any(q, LANGUAGE_WORDS["de"]):
        return "German"
    if _contains_any(q, LANGUAGE_WORDS["fr"]):
        return "French"
    return None


def _extract_course_code(question: str) -> str | None:
    match = re.search(r"\b[A-Z]{2}-[A-Z]\d{2}\.\d{5}\b", question)
    return match.group(0) if match else None


def _clean_program_name(value: str) -> str | None:
    value = re.sub(r"\b(with|having|mit|avec)?\s*\d{2,3}\s*ects\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\b(program|programme|studiengang|studiengänge|studiengaenge|courses?|modules?|kurse?|cours|mandatory|elective|pflicht|wahl|obligatoire|optionnel|include|includes|including|first study year|first year|study year|studienjahr|année|annee)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"^(im|in|dans|dans le|dans la|du|de la|des|für|fuer|for|of)\s+", "", value, flags=re.IGNORECASE)
    value = value.strip(" ?.,;:-")
    if not value or value.lower() in {"level", "program", "programs", "course", "courses"}:
        return None
    return " ".join(w.capitalize() if w.islower() else w for w in value.split())


def _extract_program_name(question: str) -> str | None:
    # Strong patterns first: "Bachelor of Business Informatics", "Bachelor in Economics".
    strong = re.search(
        r"\b(?:bachelor|master|doctorate)\s+(?:of|in)\s+(.+?)(?:\s+with\s+\d|\s+\d{2,3}\s*ects|\?|$)",
        question,
        flags=re.IGNORECASE,
    )
    if strong:
        return _clean_program_name(strong.group(1))

    # "courses in the Bachelor Business Informatics program"
    strong = re.search(
        r"\b(?:bachelor|master|doctorate)\s+(.+?)(?:\s+with\s+\d|\s+\d{2,3}\s*ects|\s+program|\?|$)",
        question,
        flags=re.IGNORECASE,
    )
    if strong and "level" not in strong.group(1).lower():
        return _clean_program_name(strong.group(1))

    # Fallbacks for English/German/French phrasing.
    fallback_patterns = [
        r"\b(?:courses?|modules?)\s+(?:in|for|of)\s+(.+?)(?:\?|$)",
        r"\b(?:kurse?|module?)\s+(?:im|in|für|fuer)\s+(.+?)(?:\?|$)",
        r"\b(?:cours|enseignements?)\s+(?:dans|du|de la|des|en|pour)\s+(.+?)(?:\?|$)",
        r"\b(?:studiengang|studienprogramm|programme d['’]études|programme d['’]etudes)\s+(.+?)(?:\?|$)",
    ]
    for pattern in fallback_patterns:
        fallback = re.search(pattern, question, flags=re.IGNORECASE)
        if fallback:
            cleaned = _clean_program_name(fallback.group(1))
            if cleaned:
                return cleaned

    return None


def _extract_numeric_filter(question: str, field_name: str = "ects") -> int | str | None:
    q = question.lower()
    m = re.search(r"(greater than|more than|above|over|at least|minimum|min|less than|fewer than|below|under|at most|maximum|max|>=|<=|>|<|=)?\s*(\d{1,3})\s*(?:total\s*)?" + re.escape(field_name), q)
    if not m:
        return None
    op, value = m.groups()
    word_to_op = {
        "greater than": ">", "more than": ">", "above": ">", "over": ">",
        "at least": ">=", "minimum": ">=", "min": ">=",
        "less than": "<", "fewer than": "<", "below": "<", "under": "<",
        "at most": "<=", "maximum": "<=", "max": "<=",
    }
    op = word_to_op.get(op or "", op)
    return f"{op}{value}" if op and op != "=" else int(value)


def _fast_structured_plan(question: str) -> dict[str, Any] | None:
    """Rule-based plans for frequent structured DB questions.

    This avoids sending simple API-selection questions to the local LLM, which can
    be very slow. The LLM remains a fallback for ambiguous mixed RAG/tool questions.
    """
    q = question.lower()

    code = _extract_course_code(question)
    if code:
        return {
            "mode": "api",
            "tool_calls": [{"tool": "get_course_by_code", "args": {"code": code}}],
            "reason": "Fast structured rule: exact course code",
        }

    wants_programs = _contains_any(q, STRUCTURED_SYNONYMS["program"])
    wants_courses = _contains_any(q, STRUCTURED_SYNONYMS["course"])

    degree_level = None
    if _contains_any(q, STRUCTURED_SYNONYMS["bachelor"]):
        degree_level = "Bachelor"
    elif _contains_any(q, STRUCTURED_SYNONYMS["master"]):
        degree_level = "Master"
    elif _contains_any(q, STRUCTURED_SYNONYMS["doctorate"]):
        degree_level = "Doctorate"

    program_type = None
    if "minor" in q:
        program_type = "minor"
    elif "major" in q:
        program_type = "major"
    elif "mono" in q or "monoprogram" in q or "mono program" in q:
        program_type = "mono"

    total_ects = _extract_numeric_filter(question, "ects")

    if wants_courses:
        program_name = _extract_program_name(question)

        # Generic course list by course ECTS, not program total ECTS.
        if not program_name and not degree_level and not program_type and total_ects is not None and not (_contains_any(q, STRUCTURED_SYNONYMS["mandatory"]) or _contains_any(q, STRUCTURED_SYNONYMS["elective"])):
            return {
                "mode": "api",
                "tool_calls": [{"tool": "get_courses", "args": {"ects": total_ects, "limit": 500}}],
                "reason": "Fast structured rule: course ECTS filter",
            }

        args: dict[str, Any] = {"limit": 500}
        args.update(_localized_program_args(program_name, question))
        if degree_level:
            args["degree_level"] = degree_level
        if total_ects is not None:
            args["total_ects"] = total_ects
        if program_type:
            args["program_type"] = program_type
        if _contains_any(q, STRUCTURED_SYNONYMS["mandatory"]):
            args["course_type"] = "Mandatory"
        elif _contains_any(q, STRUCTURED_SYNONYMS["elective"]):
            args["course_type"] = "Elective"
        if _contains_any(q, STRUCTURED_SYNONYMS["autumn"]):
            args["semester_type"] = "Autumn"
        elif _contains_any(q, STRUCTURED_SYNONYMS["spring"]):
            args["semester_type"] = "Spring"
        requested_language = _extract_requested_course_language(question)
        if requested_language:
            args["language"] = requested_language

        if program_name or degree_level or total_ects is not None or program_type or args.get("course_type"):
            return {
                "mode": "api",
                "tool_calls": [{"tool": "get_program_courses_by_metadata", "args": args}],
                "reason": "Fast structured rule: program-course query by metadata",
            }

    if wants_programs and not wants_courses:
        args: dict[str, Any] = {}
        if degree_level:
            args["degree_level"] = degree_level
        if program_type:
            args["program_type"] = program_type
        if total_ects is not None:
            args["total_ects"] = total_ects
        return {
            "mode": "api",
            "tool_calls": [{"tool": "get_programs", "args": args}],
            "reason": "Fast structured rule: program listing/filter query",
        }

    return None

def plan_tool_usage(
    question: str,
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_state = session_state or {}

    fast_plan = _fast_structured_plan(question)
    if fast_plan is not None:
        return fast_plan

    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )

    prompt = ChatPromptTemplate.from_template("""
You are a planning assistant for a university study chatbot.

Available tools:
{tool_specs}

Session state:
{session_state}

User question:
{question}

Return ONLY valid JSON in this exact format:
{{
  "mode": "api" | "rag" | "hybrid",
  "tool_calls": [
    {{
      "tool": "tool_name",
      "args": {{}}
    }}
  ],
  "reason": "short explanation"
}}

Rules:
- Use "api" when backend tools can answer the question with structured data.
- Use "rag" for regulations, policy, explanatory document questions, or questions about rules.
- Use "hybrid" when both structured backend data and document context are needed.
- Use get_course_by_code for one exact course code or a follow-up about one known course.
- Use get_courses for filtered lists of courses. For ECTS comparisons pass ects as strings like ">6", "<3", ">=5".
- Use get_programs for program searches. For minor/major/mono use program_type. For ECTS comparisons pass total_ects as strings like ">90", "<90", ">=120".
- Use get_program_by_id when the id is known.
- Use get_program_courses when the user asks for courses of a known program id. For ECTS comparisons pass ects as strings like ">6" or "<3".
- Use get_program_courses_by_metadata when the user asks for courses in a named program but no id is known. This is the best DB tool for mandatory/elective courses in Bachelor/Master X with Y ECTS.
- For German/French user questions, keep the original program name in the matching localized argument: program_de for German wording, program_fr for French wording, program_en for English wording. Also set q to the same program text when useful.
- Normalize requested teaching-language filters: Deutsch/allemand -> German, français/francais -> French, Englisch/anglais -> English.
- Use get_program_course_sections when the user asks for section headings, proposed study year, or consists-of table metadata for a known program id.
- Use get_program_docs when the user asks for official documents of a known program.
- Use get_offerings when the user asks what is offered in a given semester.
- Use get_planner_context for semester planning with known program and semester.
- Resolve references like "this course", "that one", or "it" from session state when possible.
- Prefer structured tools when they can answer exactly. Do not choose RAG for DB list questions such as all programs, minor/major/mono programs, course ECTS by code, or program-course lists.
""")

    msg = prompt.format_messages(
        question=question,
        tool_specs=json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2),
        session_state=json.dumps(session_state, ensure_ascii=False, indent=2),
    )

    with timed_step("planner.llm_decision"):
        resp = llm.invoke(msg)

    with timed_step("planner.parse_json"):
        plan = _extract_json(resp.content)

    if "decision" in plan and "mode" not in plan:
        plan["mode"] = plan.pop("decision")

    for call in plan.get("tool_calls", []):
        if "name" in call and "api" not in call:
            call["api"] = call.pop("name")
        if "arguments" in call and "args" not in call:
            call["args"] = call.pop("arguments")

    plan.setdefault("mode", "rag")
    plan.setdefault("tool_calls", [])
    plan.setdefault("reason", "")

    return plan