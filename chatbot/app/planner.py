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
                                              
                                              Examples:

User: "Show me 6 ECTS English courses"
→ tool: get_courses
→ arguments: { "ects": 6, "language": "English" }

User: "AI courses in German"
→ tool: get_courses
→ arguments: { "domain_name": "AI", "language": "German" }

User: "courses in business informatics"
→ tool: get_courses
→ arguments: { "program_name": "Business Informatics" }

User: "6 ECTS courses in business informatics"
→ tool: get_courses
→ arguments: { "program_name": "Business Informatics", "ects": 6 }

User: "soft skill courses in FS-2026"
→ tool: get_courses
→ arguments: { "soft_skills": true, "semester": "FS-2026" }

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
  → Extract filters when possible:
    - ects (e.g. "6 ECTS")
    - faculty_name (e.g. "engineering faculty")
    - domain_name (e.g. "AI", "data science")
    - language (e.g. "English", "German")
    - semester (e.g. "FS-2026", "Autumn")
    - name_contains (keywords like "data", "machine learning")
    - mobility (true/false if mentioned)
    - soft_skills (true/false if mentioned)
    - program_name (if user mentions a program)

- Use get_programs / get_program_by_id for general program queries.

- Use get_program_courses when:
  → The user asks for courses within a specific program
  → Example: "courses in business informatics"
  → Include filters like ects, semester, language if present

- Use get_planner_context for semester planning questions involving a program and a semester.

- Use rag when structured tools are insufficient or the question is about:
  → regulations
  → policies
  → explanations
  → documents

- If the user says "this course", "this one", or "it", resolve that using session state if possible.

- Prefer structured tools when they can answer exactly.

- If multiple filters are present, include ALL of them in tool arguments.
""")

    msg = prompt.format_messages(
        question=question,
        tool_specs=json.dumps(TOOL_SPECS, ensure_ascii=False, indent=2),
        session_state=json.dumps(session_state or {}, ensure_ascii=False, indent=2),
    )

    resp = llm.invoke(msg)
    return json.loads(resp.content)