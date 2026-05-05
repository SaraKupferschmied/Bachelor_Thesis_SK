import json
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


def plan_tool_usage(
    question: str,
    session_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session_state = session_state or {}

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
- Use "tool" when backend tools can answer the question with structured data.
- Use "rag" for regulations, policy, explanatory document questions, or questions about rules.
- Use "hybrid" when both structured backend data and document context are needed.
- Use get_course_by_code for one exact course code or a follow-up about one known course.
- Use get_courses for filtered lists of courses.
- Use get_programs for program searches.
- Use get_program_by_id when the id is known.
- Use get_program_courses when the user asks for courses of a known program id.
- Use get_program_courses_by_metadata when the user asks for courses in a named program but no id is known.
- Use get_program_docs when the user asks for official documents of a known program.
- Use get_offerings when the user asks what is offered in a given semester.
- Use get_planner_context for semester planning with known program and semester.
- Resolve references like "this course", "that one", or "it" from session state when possible.
- Prefer structured tools when they can answer exactly.
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