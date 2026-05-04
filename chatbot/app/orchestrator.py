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


def _extract_semester_count(text: str) -> int | None:
    match = re.search(
        r"\b(\d{1,2})\s*(semester|semesters|semestri|semestren)\b",
        text.lower()
    )

    if not match:
        return None

    value = int(match.group(1))

    if 1 <= value <= 12:
        return value

    return None


def _extract_program_id(text: str) -> int | None:
    match = re.search(r"\b(?:id\s*)?(\d+)\b", text.lower())
    if not match:
        return None
    return int(match.group(1))


def _extract_semester_count(text: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\s*(semester|semesters|semestri|semestren)?\b", text.lower())
    if not match:
        return None

    value = int(match.group(1))

    if 1 <= value <= 16:
        return value

    return None


def _extract_total_ects(text: str) -> int | None:
    match = re.search(r"\b(\d{2,3})\s*ects\b", text.lower())
    if not match:
        return None
    return int(match.group(1))

def _extract_semester_id(text: str) -> str | None:
    ids = _extract_semester_ids(text)
    return ids[0] if ids else None

def _extract_semester_ids(text: str) -> list[str]:
    matches = re.findall(r"\b(FS|HS|SS|AS)[-\s]?(\d{4})\b", text, re.IGNORECASE)
    result = []

    for prefix, year in matches:
        prefix = prefix.upper()
        if prefix == "SS":
            prefix = "FS"
        if prefix == "AS":
            prefix = "HS"

        sem = f"{prefix}-{year}"
        if sem not in result:
            result.append(sem)

    return result[:2]


def _strip_semesters(text: str) -> str:
    text = re.sub(r"\b(FS|HS|SS|AS)[-\s]?\d{4}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-:")


def is_plan_study_program_hero(question: str) -> bool:
    q = question.lower().strip()
    return q in {
        "__hero__:plan_study_program",
        "__hero__:plan_whole_study_program",
        "__hero__:complete_study_plan",
    }

def is_plan_mobility_hero(question: str) -> bool:
    return question.strip().lower() == "__hero__:plan_mobility"

def start_plan_study_program_flow(session_state: Dict[str, Any]) -> Dict[str, Any]:
    session_state["hero_flow"] = {
        "name": "plan_study_program",
        "program_id": None,
        "program_name": None,
        "candidate_programs": [],
        "semesters": None,
        "total_ects": None,
    }

    return {
        "answer": (
            "Sure — which study program would you like to plan?\n\n"
            "Please tell me:\n"
            "1. the study program, for example **Business Informatics Bachelor**\n"
            "2. in how many semesters you would like to finish, for example **8 semesters**"
        ),
        "sources": [],
        "used_tools": [],
        "session_state": session_state,
        "plan": {"mode": "hero"},
        "planning_errors": None,
    }


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def format_semester_plan(result: Dict[str, Any]) -> str:
    courses = result.get("courses", [])

    if not courses:
        return "I did not find course offerings for this program in the selected semester."

    lines = ["Here are the available courses for this semester:\n"]

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


def format_study_program_plan(result: Dict[str, Any]) -> str:
    program = result.get("program", {})
    semesters = result.get("semesters", [])
    mandatory_groups = result.get("mandatory_groups", [])
    elective_courses = result.get("elective_courses", [])
    warnings = result.get("warnings", [])

    program_name = (
        program.get("display_name")
        or program.get("name")
        or result.get("program_name")
        or "the selected program"
    )

    total_ects = (
        result.get("total_ects")
        or program.get("total_ects")
        or result.get("required_total_ects")
    )

    required_elective_ects = result.get("required_elective_ects")
    mandatory_ects = result.get("mandatory_ects")

    lines = []

    lines.append(f"Here is a complete study plan draft for **{program_name}**.")

    if total_ects:
        intro = f"\nThe program requires **{total_ects} ECTS** in total"
        if mandatory_ects is not None and required_elective_ects is not None:
            intro += (
                f", including approximately **{mandatory_ects} ECTS** from mandatory courses "
                f"and **{required_elective_ects} ECTS** from elective courses"
            )
        intro += "."
        lines.append(intro)

    lines.append(
        "\nBecause future course offerings are not fully known yet, this plan reuses "
        "the next available offering pattern for courses. Exact dates and times may change."
    )

    if warnings:
        lines.append("\n⚠️ **Notes:**")
        for warning in warnings:
            lines.append(f"- {warning}")

    if mandatory_groups:
        lines.append("\n## Mandatory courses / required choices")
        for group in mandatory_groups:
            title = (
                group.get("title")
                or group.get("course_name")
                or group.get("name")
                or "Mandatory course group"
            )
            ects = group.get("required_ects") or group.get("ects")
            options = group.get("options") or group.get("courses") or []

            if len(options) > 1:
                lines.append(f"- **{title}** ({ects} ECTS): choose one language/version")
                for option in options:
                    option_name = option.get("course_name") or option.get("name") or option.get("code")
                    language = option.get("language")
                    code = option.get("code")
                    suffix = []
                    if code:
                        suffix.append(code)
                    if language:
                        suffix.append(language)
                    suffix_text = f" — {', '.join(suffix)}" if suffix else ""
                    lines.append(f"  - {option_name}{suffix_text}")
            else:
                lines.append(f"- **{title}** ({ects} ECTS)")

    if semesters:
        lines.append("\n## Suggested semester distribution")

        for semester in semesters:
            label = semester.get("label") or semester.get("semester") or f"Semester {semester.get('index')}"
            planned_ects = semester.get("planned_ects")
            courses = semester.get("mandatory") or semester.get("courses") or []

            heading = f"\n### {label}"
            if planned_ects is not None:
                heading += f" — {planned_ects} ECTS"
            lines.append(heading)

            if not courses:
                lines.append("- No mandatory courses assigned yet.")
                continue

            for course in courses:
                if isinstance(course, dict):
                    name = (
                        course.get("course_name")
                        or course.get("name")
                        or course.get("title")
                        or course.get("code")
                        or "Course"
                    )
                    ects = course.get("ects") or course.get("required_ects")
                    offered = course.get("semester_type") or course.get("offered_in")
                    time_info = course.get("day_time_info")

                    line = f"- **{name}**"
                    if ects is not None:
                        line += f" ({ects} ECTS)"
                    if offered:
                        line += f" — offered in {offered}"
                    lines.append(line)

                    if time_info:
                        lines.append(f"  - Time: {time_info}")
                else:
                    lines.append(f"- {course}")

    if elective_courses:
        lines.append("\n## Elective courses")
        lines.append(
            "Elective courses should be selected by the student. "
            "The following courses are available candidates:"
        )

        for course in elective_courses[:20]:
            name = course.get("course_name") or course.get("name") or course.get("code")
            code = course.get("code")
            ects = course.get("ects")
            semester_type = course.get("semester_type")
            language = course.get("language")

            details = []
            if code:
                details.append(code)
            if ects is not None:
                details.append(f"{ects} ECTS")
            if semester_type:
                details.append(str(semester_type))
            if language:
                details.append(str(language))

            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"- **{name}**{suffix}")

        lines.append(
            "\nTo finish the plan, please choose which electives you want to take. "
            "For bilingual mandatory courses, please also choose the language/version you prefer."
        )

    return "\n".join(lines)

def format_mobility_plan(result: Dict[str, Any]) -> str:
    lines = [f"Here are mobility-enabled course candidates for **{result['interest']}**."]

    for sem in result["semesters"]:
        lines.append(f"\n## {sem}")
        courses = result["courses_by_semester"].get(sem, [])

        if not courses:
            lines.append("- No matching mobility courses found.")
            continue

        for c in courses[:12]:
            details = [c.get("code")]
            if c.get("ects") is not None:
                details.append(f"{c['ects']} ECTS")
            if c.get("domain_name"):
                details.append(c["domain_name"])

            lines.append(
                f"- **{c.get('name') or c.get('code')}** "
                f"({', '.join(x for x in details if x)})"
            )

            if c.get("description"):
                lines.append(f"  - Content: {str(c['description'])[:180]}...")

    return "\n".join(lines)


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

    if is_plan_study_program_hero(question):
        return start_plan_study_program_flow(session_state)

    flow = session_state.get("hero_flow")

    if is_plan_mobility_hero(question):
        session_state["hero_flow"] = {
            "name": "plan_mobility",
            "semesters": [],
            "interest": None,
        }

        return {
            "answer": (
                "Sure — which exchange semester(s) are you here for, "
                "and what course direction are you interested in?"
            ),
            "sources": [],
            "used_tools": [],
            "session_state": session_state,
            "plan": {"mode": "hero", "hero": "plan_mobility"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Whole study-program planner flow
    # ---------------------------------------------------------------------
    if flow and flow.get("name") == "plan_study_program":
        selected_program_id = flow.get("program_id")
        semesters = flow.get("semesters")
        total_ects = flow.get("total_ects")

        found_semesters = _extract_semester_count(question)
        found_total_ects = _extract_total_ects(question)

        if found_semesters:
            semesters = found_semesters
            session_state["hero_flow"]["semesters"] = semesters

        if found_total_ects:
            total_ects = found_total_ects
            session_state["hero_flow"]["total_ects"] = total_ects

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
                    session_state["hero_flow"]["program_name"] = (
                        matching[0].get("display_name")
                        or matching[0].get("name")
                    )
                else:
                    return {
                        "answer": "I could not find that ID in the options I showed. Please choose one of the listed program IDs.",
                        "sources": [],
                        "used_tools": [],
                        "session_state": session_state,
                        "plan": {"mode": "hero"},
                        "planning_errors": None,
                    }

        if not selected_program_id:
            cleaned_question = question

            if found_semesters:
                cleaned_question = re.sub(
                    r"\b\d{1,2}\s*(semester|semesters|semestri|semestren)?\b",
                    "",
                    cleaned_question,
                    flags=re.IGNORECASE,
                )

            if found_total_ects:
                cleaned_question = re.sub(
                    r"\b\d{2,3}\s*ects\b",
                    "",
                    cleaned_question,
                    flags=re.IGNORECASE,
                )

            cleaned_question = cleaned_question.strip(" ,.-")

            if not cleaned_question:
                return {
                    "answer": (
                        "Which study program should I plan? "
                        "For example: **Business Informatics Bachelor**."
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
                    "answer": "I could not find a matching study program. Please write the official program name.",
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
                        f"{', ' + str(p.get('total_ects')) + ' ECTS' if p.get('total_ects') else ''})"
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
            session_state["hero_flow"]["program_name"] = (
                programs[0].get("display_name")
                or programs[0].get("name")
            )

        if not semesters:
            return {
                "answer": (
                    "In how many semesters would you like to finish? "
                    "For example: **6 semesters** or **8 semesters**."
                ),
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero"},
                "planning_errors": None,
            }

        planner_args = {
            "program_id": selected_program_id,
            "semesters": semesters,
            "locale": language or "en",
        }

        if total_ects:
            planner_args["total_ects"] = total_ects

        planner_result = TOOLS["get_study_program_plan"](**planner_args)

        print("DEBUG study_program_plan result:", planner_result)

        answer = format_study_program_plan(planner_result)

        session_state["hero_flow"] = None

        return {
            "answer": answer,
            "sources": [],
            "used_tools": ["get_study_program_plan"],
            "session_state": session_state,
            "plan": {"mode": "hero"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Existing semester planner flow — kept from your old working version
    # ---------------------------------------------------------------------
    if flow and flow.get("name") == "plan_semester":
        sem_id = flow.get("sem_id")
        selected_program_id = flow.get("program_id")

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

        if not selected_program_id:
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
                        f"{', ' + str(p.get('total_ects')) + ' ECTS' if p.get('total_ects') else ''})"
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
    
    if flow and flow.get("name") == "plan_mobility":
        semesters = _extract_semester_ids(question)
        interest = _strip_semesters(question)

        if not semesters:
            return {
                "answer": "Which semester(s)? Please use a format like FS-2026 or HS-2026.",
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero", "hero": "plan_mobility"},
                "planning_errors": None,
            }

        if not interest:
            return {
                "answer": "What course direction are you interested in?",
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero", "hero": "plan_mobility"},
                "planning_errors": None,
            }

        result = TOOLS["get_mobility_courses"](
            semesters=semesters,
            interest=interest,
            language=language or None,
        )

        session_state["hero_flow"] = None

        return {
            "answer": format_mobility_plan(result),
            "sources": [],
            "used_tools": ["get_mobility_courses"],
            "session_state": session_state,
            "plan": {"mode": "hero", "hero": "plan_mobility"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Normal tool/RAG behavior
    # ---------------------------------------------------------------------
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

            allowed_args_by_tool = {
                "get_courses": {"name_contains", "code", "program_id", "locale"},
                "get_planner_programs": {"q", "degree_level", "locale"},
                "get_planner_courses": {"sem_id", "program_ids", "locale"},
                "get_study_program_plan": {"program_id", "semesters", "locale", "total_ects"},
                "get_mobility_courses": {"semesters", "interest", "language"},
            }

            allowed = allowed_args_by_tool.get(tool_name)
            if allowed is not None:
                args = {k: v for k, v in args.items() if k in allowed}

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