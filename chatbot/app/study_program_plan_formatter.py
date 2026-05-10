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

    selected_codes = result.get("selected_elective_codes") or []
    lines: List[str] = []
    if selected_codes:
        lines.append(f"Here is the completed study-plan draft for **{name}** over **{semester_count} semesters**.\n")
    else:
        lines.append(f"Here is a study-plan draft for **{name}** over **{semester_count} semesters**.\n")

    lines.append(
        f"The program requires **{_fmt_ects(totals.get('total_ects'))}** in total. "
        f"The target load is about **{_fmt_ects(totals.get('target_ects_per_semester'))} per semester**. "
        f"After grouping likely bilingual/equivalent mandatory courses, about "
        f"**{_fmt_ects(totals.get('mandatory_ects_after_language_choices'))}** are mandatory and "
        f"about **{_fmt_ects(totals.get('elective_ects_required'))}** should be filled with electives."
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

    if not selected_codes:
        lines.append("\nElectives are **not placed yet**. Where elective credits are still needed, treat them as placeholders such as `Elective to choose: 4.5–6 ECTS`. After you send elective course codes, I will regenerate the concrete plan with only those electives. ")

    lines.append("\n**Suggested semester distribution:**")
    for slot in result.get("suggested_mandatory_semester_plan", []):
        header = (
            f"\nSemester {slot.get('semester_number')} "
            f"({slot.get('semester_type')}, target { _fmt_ects(slot.get('target_ects')) }, "
            f"planned { _fmt_ects(slot.get('planned_ects')) })"
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
                lines.append("- Selected elective, choose one option:")
                for option in options:
                    lines.append(f"  - {_course_label(option)}")
            elif options:
                lines.append(f"- Selected elective: {_course_label(options[0])}")

        if not selected_codes and slot.get("semester_number", 0) >= max(1, int((semester_count or 8) * 0.6)):
            target = slot.get("target_ects") or 0
            planned = slot.get("planned_ects") or 0
            try:
                missing = max(0.0, float(target) - float(planned))
            except Exception:
                missing = 0.0
            if missing >= 3:
                lines.append(f"- Elective to choose: about {_fmt_ects(missing)}")

    electives = result.get("elective_courses") or []
    if electives:
        lines.append("\n**Available elective options:**")
        for course in electives:
            lines.append(f"- {_course_label(course)}")

    if selected_codes:
        lines.append("\nIf you want to change electives, tell me the new elective course codes and I can regenerate the proposal.")
    else:
        lines.append("\nTo finish the plan, please choose electives by sending course codes, for example `UE-SIN.01022, UE-SIN.04022, UE-EEP.00160`.")

    lines.append("\nIf you have not completed all suggested earlier-year courses yet, tell me so I can show all courses again instead of filtering/placing them by study year.")
    return "\n".join(lines)
