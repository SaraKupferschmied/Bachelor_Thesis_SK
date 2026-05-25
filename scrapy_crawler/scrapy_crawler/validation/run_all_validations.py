#!/usr/bin/env python3
"""
Run all validation scripts from the validation folder and write outputs to validation/metrics.

Place this file here:
  scrapy_crawler/scrapy_crawler/validation/run_all_validations.py

Run from that folder with:
  python run_all_validations.py
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


VALIDATION_DIR = Path(__file__).resolve().parent
ROOT = VALIDATION_DIR.parent
METRICS_DIR = VALIDATION_DIR / "metrics"
SPIDER_OUTPUTS = ROOT / "spider_outputs"
OUTPUTS = ROOT.parent / "outputs"


COMMANDS = [
    {
        "name": "Validate Courses",
        "cmd": [
            sys.executable,
            "validate_courses.py",
            "--courses",
            str(SPIDER_OUTPUTS / "courses.json"),
            "--output-prefix",
            "courses",
        ],
    },
    {
        "name": "Validate Program Curricula",
        "cmd": [
            sys.executable,
            "validate_programs.py",
            "--programs-file",
            str(SPIDER_OUTPUTS / "programmes_with_curricula_enriched.json"),
            "--output-dir",
            str(METRICS_DIR / "validate_programs_curricula"),
        ],
    },
    {
        "name": "Validate Program Docs",
        "cmd": [
            sys.executable,
            "validate_programs.py",
            "--programs-file",
            str(SPIDER_OUTPUTS / "program_links_with_ects_and_docs.json"),
            "--output-dir",
            str(METRICS_DIR / "validate_programs_docs"),
        ],
    },
    {
        "name": "Validate Document Downloads",
        "cmd": [
            sys.executable,
            "validate_doc_downloads.py",
            "--spider-outputs",
            str(SPIDER_OUTPUTS),
            "--manifest",
            str(OUTPUTS / "faculty_docs_v3" / "_faculty_docs_manifest.json"),
            "--out",
            str(METRICS_DIR / "documents_downloads" / "document_download_quality.json"),
        ],
    },
    {
        "name": "Validate Document Parsing",
        "cmd": [
            sys.executable,
            "validate_doc_parsing.py",
            "--outputs-root",
            str(OUTPUTS),
            "--manifest",
            str(OUTPUTS / "faculty_docs_v3" / "_faculty_docs_manifest.json"),
            "--parsed-dirs",
            "parsed_fulltext_docling_new2",
            "--out",
            str(METRICS_DIR / "documents_parsing" / "document_parsing_quality.json"),
        ],
    },
    {
        "name": "Compute Integrity Score",
        "cmd": [
            sys.executable,
            "integrity_score.py",
            "--metrics-dir",
            str(METRICS_DIR),
            "--out",
            str(METRICS_DIR / "scores" / "json_integrity_score.json"),
        ],
    },
]


def run_command(name: str, cmd: list[str]) -> None:
    print("\n" + "=" * 80)
    print(f"RUNNING: {name}")
    print("=" * 80)
    print(" ".join(cmd))

    result = subprocess.run(cmd, cwd=VALIDATION_DIR)

    if result.returncode != 0:
        print(f"\nFAILED: {name}")
        raise SystemExit(result.returncode)

    print(f"SUCCESS: {name}")


def main() -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)

    for command in COMMANDS:
        run_command(command["name"], command["cmd"])

    print("\nAll validation scripts completed successfully.")
    print(f"Metrics written to: {METRICS_DIR}")


if __name__ == "__main__":
    main()
