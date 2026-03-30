from typing import Any


def empty_session_state() -> dict[str, Any]:
    return {
        "last_course_code": None,
        "last_course_name": None,
        "last_program_id": None,
    }


def update_session_state(session_state: dict[str, Any] | None, tool_results: list[dict[str, Any]]) -> dict[str, Any]:
    state = dict(session_state or empty_session_state())

    for item in tool_results:
        if item["tool"] == "get_course_by_code" and item.get("result"):
            course = item["result"]
            state["last_course_code"] = course.get("code")
            state["last_course_name"] = course.get("name")

        elif item["tool"] == "get_program_by_id" and item.get("result"):
            program = item["result"]
            state["last_program_id"] = program.get("id")

    return state