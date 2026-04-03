import json
from typing import Any

from .router import classify_question, parse_question
from .backend_tools import (
    get_courses,
    get_course_by_code,
    get_programs,
    get_program_by_id,
    get_program_courses,
    get_program_courses_by_metadata,
)
from .ollama_rag import answer_question as rag_answer_question


def make_api_source(source: str, snippet: str, endpoint: str, data: Any = None):
    return {
        "source": source,
        "snippet": snippet,
        "source_type": "api",
        "metadata": {
            "endpoint": endpoint,
            "data": data,
        },
    }


def run_api_path(question: str, parsed: dict[str, Any]):
    used_tools: list[str] = []
    sources: list[dict[str, Any]] = []
    data: dict[str, Any] = {}

    # 1) exact course lookup
    if parsed.get("course_code"):
        code = parsed["course_code"]

        used_tools.append("get_course_by_code")
        course = get_course_by_code(code)

        if course is not None:
            data["course"] = course
            sources.append(
                make_api_source(
                    source=f"/courses/{code}",
                    snippet=f"Structured backend data for course {code}",
                    endpoint=f"/courses/{code}",
                    data=course,
                )
            )

        return data, used_tools, sources

    # 2) program by explicit id -> courses in that program
    if parsed.get("program_id") is not None:
        program_id = parsed["program_id"]

        used_tools.append("get_program_courses")
        courses = get_program_courses(
            program_id=program_id,
            ects=parsed.get("ects"),
            language=parsed.get("language"),
            semester=parsed.get("sem_id"),
            name_contains=parsed.get("name_contains"),
            mobility=parsed.get("mobility"),
            soft_skills=parsed.get("soft_skills"),
            course_type=parsed.get("course_type"),
            limit=parsed.get("limit") or 50,
        )

        data["courses"] = courses
        sources.append(
            make_api_source(
                source=f"/programs/{program_id}/courses",
                snippet=f"Returned {len(courses)} courses for program {program_id}",
                endpoint=f"/programs/{program_id}/courses",
                data={"count": len(courses)},
            )
        )

        # Optionally also fetch program itself for context
        try:
            used_tools.append("get_program_by_id")
            program = get_program_by_id(program_id)
            if program:
                data["program"] = program
        except Exception:
            pass

        return data, used_tools, sources

    # 3) program name / degree filters -> metadata-based program-course route
    if parsed.get("program_name") and (
        parsed.get("wants_courses")
        or parsed.get("course_type")
        or parsed.get("ects") is not None
        or parsed.get("language")
        or parsed.get("mobility") is not None
        or parsed.get("soft_skills") is not None
    ):
        used_tools.append("get_program_courses_by_metadata")
        courses = get_program_courses_by_metadata(
            program_name=parsed.get("program_name"),
            degree_level=parsed.get("degree_level"),
            study_start=parsed.get("study_start"),
            course_type=parsed.get("course_type"),
            semester_type=parsed.get("semester_type"),
            ects=parsed.get("ects"),
            language=parsed.get("language"),
            semester=parsed.get("sem_id"),
            name_contains=parsed.get("name_contains"),
            mobility=parsed.get("mobility"),
            soft_skills=parsed.get("soft_skills"),
            limit=parsed.get("limit") or 50,
        )

        data["courses"] = courses
        sources.append(
            make_api_source(
                source="/programs/courses",
                snippet=f"Returned {len(courses)} courses via metadata-based program lookup",
                endpoint="/programs/courses",
                data={"count": len(courses)},
            )
        )

        return data, used_tools, sources

    # 4) generic filtered program search
    if parsed.get("wants_programs") or parsed.get("program_name") or parsed.get("degree_level"):
        used_tools.append("get_programs")
        programs = get_programs(
            name=parsed.get("program_name"),
            degree_level=parsed.get("degree_level"),
            study_start=parsed.get("study_start"),
        )

        data["programs"] = programs
        sources.append(
            make_api_source(
                source="/programs",
                snippet=f"Returned {len(programs)} programs",
                endpoint="/programs",
                data={"count": len(programs)},
            )
        )

        return data, used_tools, sources

    # 5) generic structured course search
    if any(
        parsed.get(k) is not None
        for k in [
            "mobility",
            "soft_skills",
            "ects",
            "language",
            "sem_id",
            "name_contains",
        ]
    ) or parsed.get("wants_courses"):
        used_tools.append("get_courses")
        courses = get_courses(
            ects=parsed.get("ects"),
            language=parsed.get("language"),
            semester=parsed.get("sem_id"),
            name_contains=parsed.get("name_contains"),
            mobility=parsed.get("mobility"),
            soft_skills=parsed.get("soft_skills"),
            program_name=parsed.get("program_name"),
            limit=parsed.get("limit") or 50,
        )

        data["courses"] = courses
        sources.append(
            make_api_source(
                source="/courses",
                snippet=f"Returned {len(courses)} courses",
                endpoint="/courses",
                data={"count": len(courses)},
            )
        )

        return data, used_tools, sources

    return data, used_tools, sources


def build_api_answer(api_data: dict[str, Any], language: str | None = None) -> str:
    if language == "de":
        no_course = "Ich konnte diesen Kurs nicht finden."
        no_courses = "Es wurden keine passenden Kurse gefunden."
        no_programs = "Es wurden keine passenden Studiengänge gefunden."
        no_backend = "Es wurden keine strukturierten Backend-Informationen gefunden."
        course_intro = "Hier sind die Kursinformationen aus dem Backend:\n\n"
        courses_intro = "Ich habe {count} passende Kurse gefunden:\n\n"
        programs_intro = "Ich habe {count} passende Studiengänge gefunden:\n\n"
    elif language == "fr":
        no_course = "Je n’ai pas trouvé ce cours."
        no_courses = "Aucun cours correspondant n’a été trouvé."
        no_programs = "Aucun programme correspondant n’a été trouvé."
        no_backend = "Aucune information structurée n’a été trouvée dans le backend."
        course_intro = "Voici les informations du cours provenant du backend :\n\n"
        courses_intro = "J’ai trouvé {count} cours correspondants :\n\n"
        programs_intro = "J’ai trouvé {count} programmes correspondants :\n\n"
    else:
        no_course = "I could not find that course."
        no_courses = "No matching courses were found."
        no_programs = "No matching programs were found."
        no_backend = "No structured backend information was found."
        course_intro = "Here is the course information from the backend:\n\n"
        courses_intro = "I found {count} matching courses:\n\n"
        programs_intro = "I found {count} matching programs:\n\n"

    if api_data.get("course") is not None:
        course = api_data["course"]
        if not course:
            return no_course
        return course_intro + json.dumps(course, indent=2, ensure_ascii=False)

    if api_data.get("courses") is not None:
        courses = api_data["courses"]
        if not courses:
            return no_courses

        lines = []
        for c in courses[:10]:
            code = c.get("code", "N/A")
            name = c.get("name", "Unnamed course")
            ects = c.get("ects", "?")
            extra = []

            if c.get("program_name"):
                extra.append(str(c["program_name"]))
            if c.get("course_type"):
                extra.append(str(c["course_type"]))

            suffix = f" [{' | '.join(extra)}]" if extra else ""
            lines.append(f"- {code}: {name} ({ects} ECTS){suffix}")

        return courses_intro.format(count=len(courses)) + "\n".join(lines)

    if api_data.get("programs") is not None:
        programs = api_data["programs"]
        if not programs:
            return no_programs

        lines = []
        for p in programs[:10]:
            pid = p.get("program_id", "N/A")
            name = p.get("name", "Unnamed program")
            degree = p.get("degree_level", "?")
            ects = p.get("total_ects", "?")
            lines.append(f"- {pid}: {name} ({degree}, {ects} ECTS)")

        return programs_intro.format(count=len(programs)) + "\n".join(lines)

    return no_backend


def merge_hybrid_answer(api_data: dict[str, Any], rag_answer: str | None, language: str | None = None) -> str:
    if language == "de":
        structured_label = "Strukturierte Backend-Informationen:"
        rag_label = "Relevante Informationen aus Dokumenten:"
        empty_text = "Ich konnte nicht genug Informationen finden, um die Frage zu beantworten."
    elif language == "fr":
        structured_label = "Informations structurées du backend :"
        rag_label = "Informations pertinentes provenant des documents :"
        empty_text = "Je n’ai pas trouvé assez d’informations pour répondre à la question."
    else:
        structured_label = "Structured backend information:"
        rag_label = "Relevant information from documents:"
        empty_text = "I could not find enough information to answer the question."

    parts = []

    if api_data:
        parts.append(structured_label)
        parts.append(build_api_answer(api_data, language=language))

    if rag_answer:
        parts.append(rag_label)
        parts.append(rag_answer)

    if not parts:
        return empty_text

    return "\n\n".join(parts)


def answer_question(question: str, db_study=None, db_regl=None, language: str | None = None) -> dict[str, Any]:
    route = classify_question(question)
    parsed = parse_question(question)

    used_tools: list[str] = []
    sources: list[dict[str, Any]] = []

    api_data = {}
    rag_answer = None
    rag_sources = []
    api_has_data = False

    if route in ("api", "hybrid"):
        api_data, api_tools, api_sources = run_api_path(question, parsed)
        used_tools.extend(api_tools)
        sources.extend(api_sources)

        api_has_data = bool(
            api_data.get("course")
            or (api_data.get("courses") and len(api_data.get("courses", [])) > 0)
            or (api_data.get("programs") and len(api_data.get("programs", [])) > 0)
        )

    should_try_rag = (
        route in ("rag", "hybrid")
        or (route == "api" and not api_has_data)
    )

    if should_try_rag:
        rag_db = db_regl if route == "rag" else db_study

        if rag_db:
            rag_answer, rag_sources = rag_answer_question(
                rag_db,
                question,
                language=language,
            )
            sources.extend(rag_sources)

    if api_has_data and rag_answer:
        final_answer = merge_hybrid_answer(api_data, rag_answer, language=language)
    elif api_has_data:
        final_answer = build_api_answer(api_data, language=language)
    elif rag_answer:
        final_answer = rag_answer
    else:
        if language == "de":
            final_answer = "Ich konnte weder strukturierte Backend-Daten noch relevante Dokumentinformationen finden."
        elif language == "fr":
            final_answer = "Je n’ai trouvé ni données structurées du backend ni informations pertinentes dans les documents."
        else:
            final_answer = "I could not find structured backend data or relevant information in the documents."

    return {
        "answer": final_answer,
        "sources": sources,
        "used_tools": used_tools,
    }