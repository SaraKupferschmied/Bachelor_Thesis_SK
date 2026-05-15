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


def _extract_course_code(question: str) -> str | None:
    match = re.search(r"\b[A-Z]{2}-[A-Z]\d{2}\.\d{5}\b", question)
    return match.group(0) if match else None


def _clean_program_name(value: str) -> str | None:
    value = re.sub(r"\b(with|having)?\s*\d{2,3}\s*ects\b.*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(program|programme|courses?|modules?|mandatory|elective|include|includes|including|first study year|first year|study year)\b.*$", "", value, flags=re.IGNORECASE)
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

    # Fallback for "courses in Business Informatics".
    fallback = re.search(r"\b(?:courses?|modules?)\s+(?:in|for|of)\s+(.+?)(?:\?|$)", question, flags=re.IGNORECASE)
    if fallback:
        return _clean_program_name(fallback.group(1))

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
            "mode": "tool",
            "tool_calls": [{"tool": "get_course_by_code", "args": {"code": code}}],
            "reason": "Fast structured rule: exact course code",
        }

    wants_programs = any(word in q for word in ["program", "programs", "study programs", "studienprogramme", "studiengang"])
    wants_courses = any(word in q for word in ["course", "courses", "module", "modules", "kurs", "kurse"])

    degree_level = None
    if "bachelor" in q:
        degree_level = "Bachelor"
    elif "master" in q:
        degree_level = "Master"
    elif "doctorate" in q or "phd" in q:
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
        if not program_name and not degree_level and not program_type and total_ects is not None and not any(x in q for x in ["mandatory", "elective", "pflicht", "wahl"]):
            return {
                "mode": "tool",
                "tool_calls": [{"tool": "get_courses", "args": {"ects": total_ects, "limit": 500}}],
                "reason": "Fast structured rule: course ECTS filter",
            }

        args: dict[str, Any] = {"limit": 500}
        if program_name:
            args["program_en"] = program_name
        if degree_level:
            args["degree_level"] = degree_level
        if total_ects is not None:
            args["total_ects"] = total_ects
        if program_type:
            args["program_type"] = program_type
        if "mandatory" in q or "pflicht" in q:
            args["course_type"] = "Mandatory"
        elif "elective" in q or "wahl" in q:
            args["course_type"] = "Elective"
        if "autumn" in q or "fall" in q or "herbst" in q:
            args["semester_type"] = "Autumn"
        elif "spring" in q or "frühling" in q or "printemps" in q:
            args["semester_type"] = "Spring"
        if "english" in q:
            args["language"] = "English"
        elif "german" in q or "deutsch" in q:
            args["language"] = "German"
        elif "french" in q or "français" in q:
            args["language"] = "French"

        if program_name or degree_level or total_ects is not None or program_type or args.get("course_type"):
            return {
                "mode": "tool",
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
            "mode": "tool",
            "tool_calls": [{"tool": "get_programs", "args": args}],
            "reason": "Fast structured rule: program listing/filter query",
        }

    return None

def plan_tool_usage(
    question: str,
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_state = session_state or {}

    # Keep only deterministic extraction that is genuinely unambiguous.
    # Do NOT route broad natural-language questions with keyword rules such as
    # "bachelor" -> get_programs. That turns the chatbot into a brittle backend
    # form. Semantic tool selection is handled by the LLM below using TOOL_SPECS.
    code = _extract_course_code(question)
    if code:
        return {
            "mode": "tool",
            "tool_calls": [{"tool": "get_course_by_code", "args": {"code": code}}],
            "reason": "Exact course code detected",
        }

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
  "mode": "tool" | "rag" | "hybrid",
  "tool_calls": [
    {{
      "tool": "tool_name",
      "args": {{}}
    }}
  ],
  "reason": "short explanation"
}}

Rules:
- First understand the user's information need. Do not choose tools by keyword matching.
- Use "tool" when backend tools can answer the actual question with structured data. (first try to find a matching api because if it exists its way faster than rag)
- Use "rag" for conceptual, regulatory, policy, explanatory, or general knowledge questions.
- Use "hybrid" when structured data is needed but the answer also needs explanation from documents.
- A question can mention entities like "bachelor", "program", "ECTS", or "Business Informatics"
  without asking for a list. Decide whether the user wants:
  1. a specific value/fact,
  2. a filtered list,
  3. a computed/aggregated answer,
  4. an explanation.
- For specific values in the database, call the narrowest matching tool and let the assistant synthesize the answer.
  Example: "What is the total number of ECTS for the Business Informatics bachelor?" ->
  get_programs with name="Business Informatics" and degree_level="Bachelor", not RAG.
- For broad conceptual questions like "How many ECTS does an average Bachelor have?" prefer RAG or hybrid,
  because the user is not asking to list every Bachelor program row.
- For filtered list questions like "show all Bachelor programs" or "which Bachelor minors have 60 ECTS",
  use get_programs.
- Use get_course_by_code for one exact course code or a follow-up about one known course.
- Use get_courses for filtered lists of courses. For ECTS comparisons pass ects as strings like ">6", "<3", ">=5".
- Use get_programs for program searches. For minor/major/mono use program_type. For ECTS comparisons pass total_ects as strings like ">90", "<90", ">=120".
- Use get_program_by_id when the id is known.
- Use get_program_courses when the user asks for courses of a known program id. For ECTS comparisons pass ects as strings like ">6" or "<3".
- Use get_program_courses_by_metadata when the user asks for courses in a named program but no id is known. This is the best DB tool for mandatory/elective courses in Bachelor/Master X with Y ECTS.
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
        if "name" in call and "tool" not in call:
            call["tool"] = call.pop("name")
        if "arguments" in call and "args" not in call:
            call["args"] = call.pop("arguments")

    plan.setdefault("mode", "rag")
    plan.setdefault("tool_calls", [])
    plan.setdefault("reason", "")

    return plan