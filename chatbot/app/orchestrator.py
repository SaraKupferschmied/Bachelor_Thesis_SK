import re
from typing import Any, Dict, List

from .planner import plan_tool_usage
from .backend_tools import TOOLS
from .ollama_rag import answer_question as rag_answer
from .session_state import update_session_state
from .hero_semester import is_plan_semester_hero, start_plan_semester_flow

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

def _extract_semester_id(text: str) -> str | None:
    match = re.search(r"\b(FS|HS|SS|AS)[-\s]?(\d{4})\b", text, re.IGNORECASE)

    if not match:
        return None

    prefix = match.group(1).upper()
    year = match.group(2)

    # Normalize SS/AS if your backend uses FS/HS
    if prefix == "SS":
        prefix = "FS"
    elif prefix == "AS":
        prefix = "HS"

    return f"{prefix}-{year}"

def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def format_semester_plan(result: Dict[str, Any]) -> str:
    courses = result.get("courses", [])

    if not courses:
        return "I did not find course offerings for this program in the selected semester."

    lines = ["Here are the available courses for this semester:\n"]

    # naive conflict detection (based on same time string)
    time_map = {}

    for c in courses:
        name = c.get("course_name") or c.get("code")
        time_info = c.get("day_time_info")

        if time_info:
            time_map.setdefault(time_info, []).append(name)

    conflicts = {
        t: names for t, names in time_map.items() if len(names) > 1
    }

    for c in courses:
        mandatory = bool(c.get("mandatory_for"))
        tag = "mandatory" if mandatory else "elective"

        name = c.get("course_name") or c.get("code")

        lines.append(
            f"- **{name}** "
            f"({c.get('code')}, {c.get('ects')} ECTS, {tag})"
        )

        if c.get("day_time_info"):
            lines.append(f"  - Time: {c['day_time_info']}")

    # conflict highlighting
    if conflicts:
        lines.append("\n⚠️ **Potential schedule conflicts detected:**")
        for t, names in conflicts.items():
            lines.append(f"- {t}: {', '.join(names)}")

    lines.append(
        "\nPlease tell me which of these you have already completed, "
        "and which ones you are considering for this semester. "
        "I can then help you avoid timetable conflicts."
    )

    return "\n".join(lines)

def _extract_program_id(text: str) -> int | None:
    match = re.search(r"\b(?:id\s*)?(\d+)\b", text.lower())
    if not match:
        return None
    return int(match.group(1))

def answer_question(
    question: str,
    db_study=None,
    db_regl=None,
    language: str | None = None,
    session_state: Dict[str, Any] | None = None,
    run_mode: str | None = None,
) -> Dict[str, Any]:
    session_state = session_state or {}
    final_answer = ""

    if is_plan_semester_hero(question):
        return start_plan_semester_flow(session_state)

    flow = session_state.get("hero_flow")

    if flow and flow.get("name") == "plan_semester":
        sem_id = flow.get("sem_id")
        selected_program_id = flow.get("program_id")

        # Step A: extract semester if missing
        if not sem_id:
            sem_id = _extract_semester_id(question)

            if sem_id:
                session_state["hero_flow"]["sem_id"] = sem_id
            else:
                return {
                    "answer": "Which semester would you like to plan? Please use a format like **FS-2026** or **HS-2026**.",
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

        # Step B: if we previously showed program options, interpret number as selection
        candidate_programs = flow.get("candidate_programs", [])

        if candidate_programs and not selected_program_id:
            chosen_id = _extract_program_id(question)

            if chosen_id:
                matching = [
                    p for p in candidate_programs
                    if int(p.get("program_id")) == chosen_id
                ]

                if matching:
                    selected_program_id = chosen_id
                    session_state["hero_flow"]["program_id"] = selected_program_id
                else:
                    return {
                        "answer": "I could not find that ID in the options I showed. Please choose one of the listed program IDs.",
                        "sources": [],
                        "used_tools": [],
                        "session_state": session_state,
                        "plan": {"mode": "hero"},
                        "planning_errors": None,
                    }

        # Step C: if no program selected yet, search programs
        if not selected_program_id:
            # Guard: user only gave semester, no program yet
            cleaned_question = question.replace(sem_id, "").strip(" ,.-")

            if not cleaned_question:
                return {
                    "answer": (
                        f"Great, I will plan **{sem_id}**.\n\n"
                        "Which study program or direction are you studying? "
                        "For example: **Wirtschaftsinformatik Bachelor**."
                    ),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            programs = TOOLS["get_planner_programs"](
                q=cleaned_question,
                degree_level="Bachelor" if "bachelor" in question.lower() else None,
                locale=language or "en",
            )

            if not programs:
                return {
                    "answer": "I could not find a matching study program. Could you write the official program name?",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            bachelor_programs = [
                p for p in programs
                if str(p.get("degree_level", "")).lower() == "bachelor"
            ]

            if "bachelor" in question.lower() and bachelor_programs:
                programs = bachelor_programs

            if len(programs) > 1:
                session_state["hero_flow"]["candidate_programs"] = programs[:8]

                options = "\n".join(
                    (
                        f"- {p['program_id']}: {p.get('display_name')} "
                        f"({p.get('degree_level')}"
                        f"{', ' + str(p.get('total_ects')) + ' ECTS' if p.get('total_ects') else ''}"
                    )
                    for p in programs[:8]
                )

                return {
                    "answer": f"I found several matching programs. Which one should I use?\n\n{options}\n\nPlease answer with the program ID.",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            selected_program_id = programs[0]["program_id"]
            session_state["hero_flow"]["program_id"] = selected_program_id

        # Step D: now call planner
        planner_result = TOOLS["get_planner_courses"](
            sem_id=sem_id,
            program_ids=[selected_program_id],
            locale=language or "en",
        )

        answer = format_semester_plan(planner_result)

        session_state["hero_flow"] = None

        return {
            "answer": answer,
            "sources": [],
            "used_tools": ["get_planner_courses"],
            "session_state": session_state,
            "plan": {"mode": "hero"},
            "planning_errors": None,
        }

    if run_mode and run_mode != "auto":
        plan = {
            "mode": run_mode,
            "tool_calls": [],
            "reason": f"Forced mode: {run_mode}",
        }
        planning_errors = None
    else:
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
        q = question.lower()

        study_keywords = [
            "course", "courses", "module", "modules", "semester", "study plan", "program", "ects",
            "kurs", "kurse", "modul", "module", "semester", "studienplan", "bachelor", "master",
            "wirtschaftsinformatik", "business informatics", "pflichtfach", "wahlfach",
        ]

        regl_keywords = [
            "reglement", "regulation", "regulations", "ordnung", "article", "artikel", "paragraph", "§",
        ]

        if any(k in q for k in study_keywords):
            db = db_study or db_regl
        elif any(k in q for k in regl_keywords):
            db = db_regl or db_study
        else:
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