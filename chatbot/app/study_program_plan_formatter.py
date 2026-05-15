from typing import Any, Dict, List


def _fmt_ects(value: Any) -> str:
    if value is None:
        return "unknown ECTS"
    try:
        num = float(value)
        return f"{int(num)} ECTS" if num.is_integer() else f"{num:g} ECTS"
    except Exception:
        return f"{value} ECTS"


def _course_label(course: Dict[str, Any]) -> str:
    name = course.get("course_name") or course.get("code")
    code = course.get("code")
    ects = _fmt_ects(course.get("ects"))
    langs = course.get("teaching_languages") or []
    sem_type = course.get("semester_type") or "offering semester unknown"
    time = course.get("day_time_info")
    bits = [f"{code}", ects, sem_type]
    if langs:
        bits.append("/".join(langs))
    label = f"**{name}** ({', '.join(str(x) for x in bits if x)})"
    if time:
        label += f" — latest known time: {time}"
    return label


def format_study_program_plan(result: Dict[str, Any], rag_rules: str | None = None) -> str:
    program = result.get("program") or {}
    totals = result.get("totals") or {}
    name = program.get("display_name") or "this study program"
    semester_count = result.get("requested_semesters")

    lines: List[str] = []
    lines.append(f"Here is a first full-program plan for **{name}** over **{semester_count} semesters**.\n")

    lines.append(
        f"The program requires **{_fmt_ects(totals.get('total_ects'))}** in total. "
        f"After grouping likely bilingual/equivalent mandatory courses, about "
        f"**{_fmt_ects(totals.get('mandatory_ects_after_language_choices'))}** are mandatory and "
        f"about **{_fmt_ects(totals.get('elective_ects_required'))}** must be filled with electives."
    )

    if rag_rules:
        lines.append("\n**Rules found in the study-plan/regulation documents:**")
        lines.append(rag_rules.strip())

    assumptions = result.get("assumptions") or []
    if assumptions:
        lines.append("\n**Important planning assumptions:**")
        for assumption in assumptions:
            lines.append(f"- {assumption}")

    choice_groups = [g for g in result.get("mandatory_choice_groups", []) if g.get("requires_choice")]
    if choice_groups:
        lines.append("\n**Language/equivalent-course choices needed:**")
        for group in choice_groups[:12]:
            opts = group.get("options") or []
            lines.append(f"- Choose **one** of: {', '.join((o.get('course_name') or o.get('code')) for o in opts)}")

    lines.append("\n**Suggested mandatory-course distribution:**")
    for slot in result.get("suggested_mandatory_semester_plan", []):
        header = (
            f"\nSemester {slot.get('semester_number')} "
            f"({slot.get('semester_type')}, target { _fmt_ects(slot.get('target_ects')) }, "
            f"planned mandatory { _fmt_ects(slot.get('planned_ects')) })"
        )
        lines.append(header)
        mandatory = slot.get("mandatory") or []
        electives_in_slot = slot.get("electives") or []
        if not mandatory and not electives_in_slot:
            lines.append("- No course placed here yet.")
            continue
        for group in mandatory:
            options = group.get("options") or []
            if group.get("requires_choice"):
                lines.append("- Choose one mandatory language/equivalent option:")
                for option in options:
                    lines.append(f"  - {_course_label(option)}")
            elif options:
                lines.append(f"- Mandatory: {_course_label(options[0])}")
        for group in electives_in_slot:
            options = group.get("options") or []
            if group.get("requires_choice"):
                lines.append("- Elective suggestion, choose one option:")
                for option in options:
                    lines.append(f"  - {_course_label(option)}")
            elif options:
                lines.append(f"- Elective suggestion: {_course_label(options[0])}")

    electives = result.get("elective_courses") or []
    if electives:
        lines.append("\n**Further available elective options:**")
        for course in electives[:12]:
            lines.append(f"- {_course_label(course)}")
        if len(electives) > 12:
            lines.append(f"- …and {len(electives) - 12} more electives.")

    return "\n".join(lines)
