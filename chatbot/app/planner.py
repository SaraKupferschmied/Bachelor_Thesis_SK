import json
from typing import Any
from langchain_ollama import ChatOllama
from langchain.prompts import ChatPromptTemplate

from .backend_tools import TOOL_SPECS
from .config import settings


def plan_tool_usage(question: str, session_state: dict[str, Any] | None = None) -> dict[str, Any]:
    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )

    prompt = ChatPromptTemplate.from_template("""
You are a planning assistant for a university semester planning chatbot.

Available tools:
{tool_specs}

Session state:
{session_state}

User question:
{question}

Return ONLY valid JSON in this format:
{{
  "decision": "tool" | "rag" | "hybrid",
  "tool_calls": [
    {{
      "name": "tool_name",
      "arguments": {{}}
    }}
  ],
  "reason": "short explanation"
}}

Rules:
- Use get_course_by_code for one specific course code or follow-up questions about one course.
- Use get_courses for requests asking for multiple courses or lists of courses.
- Use get_programs / get_program_by_id / get_program_courses for program-related questions.
- Use get_planner_context for semester planning questions involving a program and a semester.
- Use rag when structured tools are insufficient or the question is about regulations, policy, or explanatory document content.
- If the user says "this course", "this one", or "it", resolve that using session state if possible.
- Prefer structured tools when they can answer exactly.
""")

    msg = prompt.format_messages(
        question=question,
        tool_specs=json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2),
        session_state=json.dumps(session_state or {}, ensure_ascii=False, indent=2),
    )

    resp = llm.invoke(msg)
    return json.loads(resp.content)