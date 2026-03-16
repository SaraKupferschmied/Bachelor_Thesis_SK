# Tool functions

import re
from typing import Any


def summarize_regulation(text: str) -> str:
    # Lightweight heuristic summary; the LLM will do the real summarization in the final answer.
    # This tool just extracts a few salient anchors (articles/paragraph headers) to help grounding.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    anchors = [ln for ln in lines if re.match(r"^(Art\.|§)\s*\d+", ln)]
    anchors = anchors[:10]
    return (
        "Key articles/paragraphs found:\n" + "\n".join(f"- {a}" for a in anchors)
        if anchors
        else "No explicit Art./§ anchors found."
    )


def extract_studyplan_facts(text: str) -> dict[str, Any]:
    # Extract common studyplan bits: ECTS, module codes, exam form keywords.
    facts: dict[str, Any] = {
        "ects_mentions": [],
        "module_codes": [],
        "exam_keywords": [],
    }

    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*ECTS", text, flags=re.IGNORECASE):
        facts["ects_mentions"].append(m.group(0))

    for m in re.finditer(r"\b[A-Z]{2,4}\s*\d{3,4}\b", text):
        facts["module_codes"].append(m.group(0).replace(" ", ""))

    for kw in [
        "exam",
        "assessment",
        "pass",
        "fail",
        "repetition",
        "resit",
        "grading",
        "prerequisite",
    ]:
        if re.search(rf"\b{kw}\b", text, flags=re.IGNORECASE):
            facts["exam_keywords"].append(kw)

    # de-dup
    facts["ects_mentions"] = sorted(set(facts["ects_mentions"]))
    facts["module_codes"] = sorted(set(facts["module_codes"]))
    facts["exam_keywords"] = sorted(set(facts["exam_keywords"]))

    return facts


TOOLS = {
    "summarize_regulation": summarize_regulation,
    "extract_studyplan_facts": extract_studyplan_facts,
}