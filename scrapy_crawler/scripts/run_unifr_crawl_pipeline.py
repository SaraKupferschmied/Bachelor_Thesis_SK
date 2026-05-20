#!/usr/bin/env python3
"""
Run the UNIFR crawler pipeline from scrapy_crawler/scripts.

Expected location:
    <repo-root>/scrapy_crawler/scripts/run_unifr_crawl_pipeline.py

Usage from repo root:
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py

Usage from scripts folder:
    cd scrapy_crawler/scripts
    python run_unifr_crawl_pipeline.py

Useful options:
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --list-only
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --skip-crawl
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --skip-merge
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def find_repo_root(script_path: Path) -> Path:
    """
    Script is intended to live in:
        repo_root / scrapy_crawler / scripts / this_file.py

    This also supports being copied elsewhere inside the repo by walking upward
    until the expected project folders are found.
    """
    for candidate in [script_path.parent, *script_path.parents]:
        if (
            (candidate / "scrapy_crawler" / "scrapy_crawler").is_dir()
            and (candidate / "DB_service").is_dir()
        ):
            return candidate

    raise RuntimeError(
        "Could not find repo root. Expected folders: "
        "scrapy_crawler/scrapy_crawler and DB_service"
    )


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = find_repo_root(SCRIPT_PATH)

SCRAPY_PROJECT_ROOT = REPO_ROOT / "scrapy_crawler" / "scrapy_crawler"
SPIDERS_DIR = SCRAPY_PROJECT_ROOT / "spiders"
OUTPUT_DIR = SCRAPY_PROJECT_ROOT / "spider_outputs"
FACULTY_PROGRAMS_DIR = OUTPUT_DIR / "faculty_programs"

IMPORT_DIR = REPO_ROOT / "DB_service" / "src" / "import"


def ensure_nested_spider_packages() -> None:
    """
    Scrapy only discovers nested spider modules reliably if subfolders are Python packages.
    This creates missing __init__.py files under scrapy_crawler/scrapy_crawler/spiders.
    """
    if not SPIDERS_DIR.exists():
        raise RuntimeError(f"Spiders directory not found: {SPIDERS_DIR}")

    for directory in [SPIDERS_DIR, *[p for p in SPIDERS_DIR.rglob("*") if p.is_dir()]]:
        init_file = directory / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")


def run_step(label: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n=== {label} ===")
    print("$ " + " ".join(str(part) for part in cmd))

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        shell=False,
    )

    if result.returncode != 0:
        raise SystemExit(f"ERROR: step failed with exit code {result.returncode}: {label}")


def scrapy_cmd(*args: str | Path) -> list[str]:
    return ["scrapy", *[str(a) for a in args]]


def python_cmd(script_path: Path, *args: str | Path) -> list[str]:
    return [sys.executable, str(script_path), *[str(a) for a in args]]


def list_spiders() -> None:
    ensure_nested_spider_packages()
    run_step("available Scrapy spiders", scrapy_cmd("list"), cwd=SCRAPY_PROJECT_ROOT)


def run_crawls() -> None:
    ensure_nested_spider_packages()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FACULTY_PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)

    steps: list[tuple[str, list[str], Path]] = [
        (
            "1. curricula links with ECTS",
            scrapy_cmd(
                "crawl",
                "curricula_links_level2_ects",
                "-O",
                OUTPUT_DIR / "program_links_with_ects.json",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "2. curricula links enriched",
            scrapy_cmd(
                "crawl",
                "curricula_links_level2_enriched",
                "-O",
                SCRAPY_PROJECT_ROOT / "programmes_with_curricula_enriched.json",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "3. download links",
            scrapy_cmd(
                "crawl",
                "download_links_level3",
                "-O",
                OUTPUT_DIR / "download_links.json",
                "-a",
                f"input_json_path={OUTPUT_DIR / 'program_links_with_ects.json'}",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "4. faculty links",
            scrapy_cmd(
                "crawl",
                "faculty_links",
                "-O",
                OUTPUT_DIR / "faculties.json",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "5. education faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_edu_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "edu.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "6. science and medicine faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_scimed_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "scimed.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "7. interfaculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_interfaculty_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "interfaculty.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "8. law faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_ius_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "law.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "9. philosophy faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_phil_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "philo.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "10. economics and social sciences faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_ses_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "ses.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "11. theology faculty studyplans",
            scrapy_cmd(
                "crawl",
                "unifr_theo_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "theo.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "12. timetable courses",
            scrapy_cmd(
                "crawl",
                "timetable_courses_en",
                "-O",
                OUTPUT_DIR / "courses.json",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "13. UNIFR directory",
            scrapy_cmd(
                "crawl",
                "unifr_directory",
                "-a",
                f"courses_file={OUTPUT_DIR / 'courses.json'}",
                "-O",
                OUTPUT_DIR / "unifr_people.jsonl",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
        (
            "14. reglementation docs",
            scrapy_cmd(
                "crawl",
                "reglementation",
                "-O",
                OUTPUT_DIR / "reglementation_docs.json",
            ),
            SCRAPY_PROJECT_ROOT,
        ),
    ]

    for label, cmd, cwd in steps:
        run_step(label, cmd, cwd)


def run_merges() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FACULTY_PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)

    steps: list[tuple[str, list[str], Path]] = [
        (
            "merge 1. normalize faculty JSONs",
            python_cmd(
                IMPORT_DIR / "normalize_faculty_jsons.py",
                "--input-dir",
                FACULTY_PROGRAMS_DIR,
                "--out",
                OUTPUT_DIR / "faculty_programs_normalized.json",
            ),
            REPO_ROOT,
        ),
        (
            "merge 2. merge studyplans",
            python_cmd(
                IMPORT_DIR / "merge_studyplans.py",
                "--base",
                OUTPUT_DIR / "program_links_with_ects.json",
                "--inputs",
                OUTPUT_DIR / "faculty_programs_normalized.json",
                "--out",
                OUTPUT_DIR / "program_links_with_ects_and_docs.json",
            ),
            REPO_ROOT,
        ),
        (
            "merge 3. unmatched patch",
            python_cmd(
                IMPORT_DIR / "unmatched_patch.py",
                "--in",
                OUTPUT_DIR / "program_links_with_ects_and_docs.json",
                "--out",
                OUTPUT_DIR / "program_links_with_ects_and_docs_enriched.json",
            ),
            REPO_ROOT,
        ),
    ]

    for label, cmd, cwd in steps:
        run_step(label, cmd, cwd)


def print_paths() -> None:
    print("Resolved paths:")
    print(f"  script:              {SCRIPT_PATH}")
    print(f"  repo root:           {REPO_ROOT}")
    print(f"  Scrapy project root: {SCRAPY_PROJECT_ROOT}")
    print(f"  spiders dir:         {SPIDERS_DIR}")
    print(f"  output dir:          {OUTPUT_DIR}")
    print(f"  import scripts dir:  {IMPORT_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run UNIFR Scrapy spiders and merge steps."
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print resolved paths and available Scrapy spiders.",
    )
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help="Skip Scrapy crawl steps and only run merge steps.",
    )
    parser.add_argument(
        "--skip-merge",
        action="store_true",
        help="Run Scrapy crawl steps but skip merge steps.",
    )
    parser.add_argument(
        "--print-paths",
        action="store_true",
        help="Print resolved paths before running.",
    )

    args = parser.parse_args()

    if args.print_paths or args.list_only:
        print_paths()

    if args.list_only:
        list_spiders()
        return

    if not args.skip_crawl:
        run_crawls()

    if not args.skip_merge:
        run_merges()

    print("\nPipeline finished successfully.")


if __name__ == "__main__":
    main()
