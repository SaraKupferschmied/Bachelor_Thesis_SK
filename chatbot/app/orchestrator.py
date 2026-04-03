from typing import Any, Dict, List

from .planner import plan_tool_usage
from .backend_tools import TOOLS
from .ollama_rag import answer_question as rag_answer
from .session_state import update_session_state


def _format_tool_result(tool_name: str, result: Any) -> str:
    if result is None:
        return f"{tool_name}: no result found."

    if isinstance(result, list):
        if not result:
            return f"{tool_name}: no matching results found."

        lines = []
        for item in result[:10]:
            if isinstance(item, dict):
                name = item.get("name") or item.get("title") or item.get("code") or "item"
                code = item.get("code")
                ects = item.get("ects")
                extra = []
                if code:
                    extra.append(code)
                if ects is not None:
                    extra.append(f"{ects} ECTS")
                suffix = f" ({', '.join(extra)})" if extra else ""
                lines.append(f"- {name}{suffix}")
            else:
                lines.append(f"- {item}")
        return "\n".join(lines)

    return str(result)


def answer_question(
    question: str,
    db_study=None,
    db_regl=None,
    language: str | None = None,
    session_state: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    session_state = session_state or {}
    final_answer = ""

    try:
        plan = plan_tool_usage(question, session_state=session_state)
        planning_errors = None
    except Exception as e:
        plan = {"mode": "rag", "tool_calls": [], "reason": "Planner fallback"}
        planning_errors = str(e)

    mode = plan.get("mode", "rag")
    tool_results: List[Dict[str, Any]] = []
    sources = []
    answer_parts: List[str] = []

    if mode in ("tool", "hybrid"):
        for call in plan.get("tool_calls", []):
            tool_name = call.get("tool")
            args = call.get("args", {}) or {}

            tool_fn = TOOLS.get(tool_name)
            if not tool_fn:
                tool_results.append({
                    "tool": tool_name,
                    "result": None,
                    "error": f"Unknown tool: {tool_name}",
                })
                answer_parts.append(f"{tool_name} failed: unknown tool")
                continue

            try:
                result = tool_fn(**args)
                tool_results.append({
                    "tool": tool_name,
                    "result": result,
                })
                answer_parts.append(_format_tool_result(tool_name, result))
            except Exception as e:
                tool_results.append({
                    "tool": tool_name,
                    "result": None,
                    "error": str(e),
                })
                answer_parts.append(f"{tool_name} failed: {e}")

    if mode in ("rag", "hybrid"):
        db = db_regl
        q = question.lower()
        study_keywords = ["course", "ects", "program", "study plan", "module", "semester"]
        if any(k in q for k in study_keywords):
            db = db_study or db_regl

        if db is not None:
            rag_text, rag_sources = rag_answer(
                db=db,
                question=question,
                language=language,
            )
            sources.extend(rag_sources)

            if mode == "rag":
                final_answer = rag_text
            else:
                answer_parts.append("Document answer:\n" + rag_text)
        else:
            if mode == "rag":
                final_answer = "The document index is not loaded."
            else:
                answer_parts.append("The document index is not loaded.")

    if mode == "tool":
        final_answer = "\n".join(answer_parts) if answer_parts else "No tool result available."
    elif mode == "hybrid":
        final_answer = "\n\n".join(part for part in answer_parts if part) or "No answer available."
    elif mode == "rag" and not final_answer:
        final_answer = "No answer available."

    new_session_state = update_session_state(session_state, tool_results)

    return {
        "answer": final_answer,
        "sources": sources,
        "used_tools": [x["tool"] for x in tool_results if x.get("tool")],
        "session_state": new_session_state,
        "plan": plan,
        "planning_errors": planning_errors,
    }