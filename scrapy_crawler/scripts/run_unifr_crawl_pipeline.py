#!/usr/bin/env python3
"""
Robust UNIFR crawler pipeline runner.

Recommended location:
    <repo-root>/scrapy_crawler/scripts/run_unifr_crawl_pipeline.py

Run from repo root:
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py

Run from this scripts folder:
    python run_unifr_crawl_pipeline.py

Common usage:
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --list-only
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --skip-existing
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --from-step 12
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --from-step 12 --skip-merge
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --skip-crawl
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --retries 2
    python scrapy_crawler/scripts/run_unifr_crawl_pipeline.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal


StepKind = Literal["crawl", "merge", "info"]


@dataclass(frozen=True)
class Step:
    number: int
    label: str
    kind: StepKind
    cmd: list[str]
    cwd: Path
    output: Path | None = None
    required_inputs: tuple[Path, ...] = ()


def find_repo_root(script_path: Path) -> Path:
    """
    Script is intended to live in:
        repo_root / scrapy_crawler / scripts / this_file.py

    It also supports being copied elsewhere inside the repo by walking upward
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
LOG_DIR = OUTPUT_DIR / "_pipeline_logs"

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


def scrapy_cmd(*args: str | Path) -> list[str]:
    return ["scrapy", *[str(a) for a in args]]


def python_cmd(script_path: Path, *args: str | Path) -> list[str]:
    return [sys.executable, str(script_path), *[str(a) for a in args]]


def build_steps() -> list[Step]:
    program_links = OUTPUT_DIR / "program_links_with_ects.json"
    faculty_normalized = OUTPUT_DIR / "faculty_programs_normalized.json"
    merged_docs = OUTPUT_DIR / "program_links_with_ects_and_docs.json"
    merged_docs_enriched = OUTPUT_DIR / "program_links_with_ects_and_docs_enriched.json"
    courses = OUTPUT_DIR / "courses.json"

    return [
        Step(
            1,
            "curricula links with ECTS",
            "crawl",
            scrapy_cmd("crawl", "curricula_links_level2_ects", "-O", program_links),
            SCRAPY_PROJECT_ROOT,
            output=program_links,
        ),
        Step(
            2,
            "curricula links enriched",
            "crawl",
            scrapy_cmd(
                "crawl",
                "curricula_links_level2_enriched",
                "-O",
                SCRAPY_PROJECT_ROOT / "programmes_with_curricula_enriched.json",
            ),
            SCRAPY_PROJECT_ROOT,
            output=SCRAPY_PROJECT_ROOT / "programmes_with_curricula_enriched.json",
        ),
        Step(
            3,
            "download links",
            "crawl",
            scrapy_cmd(
                "crawl",
                "download_links_level3",
                "-O",
                OUTPUT_DIR / "download_links.json",
                "-a",
                f"input_json_path={program_links}",
            ),
            SCRAPY_PROJECT_ROOT,
            output=OUTPUT_DIR / "download_links.json",
            required_inputs=(program_links,),
        ),
        Step(
            4,
            "faculty links",
            "crawl",
            scrapy_cmd("crawl", "faculty_links", "-O", OUTPUT_DIR / "faculties.json"),
            SCRAPY_PROJECT_ROOT,
            output=OUTPUT_DIR / "faculties.json",
        ),
        Step(
            5,
            "education faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_edu_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "edu.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "edu.json",
        ),
        Step(
            6,
            "science and medicine faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_scimed_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "scimed.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "scimed.json",
        ),
        Step(
            7,
            "interfaculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_interfaculty_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "interfaculty.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "interfaculty.json",
        ),
        Step(
            8,
            "law faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_ius_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "law.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "law.json",
        ),
        Step(
            9,
            "philosophy faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_phil_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "philo.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "philo.json",
        ),
        Step(
            10,
            "economics and social sciences faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_ses_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "ses.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "ses.json",
        ),
        Step(
            11,
            "theology faculty studyplans",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_theo_studyplans",
                "-O",
                FACULTY_PROGRAMS_DIR / "theo.json",
                "-a",
                "lang=en",
            ),
            SCRAPY_PROJECT_ROOT,
            output=FACULTY_PROGRAMS_DIR / "theo.json",
        ),
        Step(
            12,
            "timetable courses",
            "crawl",
            scrapy_cmd("crawl", "timetable_courses_en", "-O", courses),
            SCRAPY_PROJECT_ROOT,
            output=courses,
        ),
        Step(
            13,
            "UNIFR directory",
            "crawl",
            scrapy_cmd(
                "crawl",
                "unifr_directory",
                "-a",
                f"courses_file={courses}",
                "-O",
                OUTPUT_DIR / "unifr_people.jsonl",
            ),
            SCRAPY_PROJECT_ROOT,
            output=OUTPUT_DIR / "unifr_people.jsonl",
            required_inputs=(courses,),
        ),
        Step(
            14,
            "reglementation docs",
            "crawl",
            scrapy_cmd(
                "crawl",
                "reglementation",
                "-O",
                OUTPUT_DIR / "reglementation_docs.json",
            ),
            SCRAPY_PROJECT_ROOT,
            output=OUTPUT_DIR / "reglementation_docs.json",
        ),
        Step(
            15,
            "merge: normalize faculty JSONs",
            "merge",
            python_cmd(
                IMPORT_DIR / "normalize_faculty_jsons.py",
                "--input-dir",
                FACULTY_PROGRAMS_DIR,
                "--out",
                faculty_normalized,
            ),
            REPO_ROOT,
            output=faculty_normalized,
            required_inputs=(
                FACULTY_PROGRAMS_DIR / "edu.json",
                FACULTY_PROGRAMS_DIR / "scimed.json",
                FACULTY_PROGRAMS_DIR / "interfaculty.json",
                FACULTY_PROGRAMS_DIR / "law.json",
                FACULTY_PROGRAMS_DIR / "philo.json",
                FACULTY_PROGRAMS_DIR / "ses.json",
                FACULTY_PROGRAMS_DIR / "theo.json",
            ),
        ),
        Step(
            16,
            "merge: merge studyplans",
            "merge",
            python_cmd(
                IMPORT_DIR / "merge_studyplans.py",
                "--base",
                program_links,
                "--inputs",
                faculty_normalized,
                "--out",
                merged_docs,
            ),
            REPO_ROOT,
            output=merged_docs,
            required_inputs=(program_links, faculty_normalized),
        ),
        Step(
            17,
            "merge: unmatched patch",
            "merge",
            python_cmd(
                IMPORT_DIR / "unmatched_patch.py",
                "--in",
                merged_docs,
                "--out",
                merged_docs_enriched,
            ),
            REPO_ROOT,
            output=merged_docs_enriched,
            required_inputs=(merged_docs,),
        ),
    ]


def print_paths() -> None:
    print("Resolved paths:")
    print(f"  script:              {SCRIPT_PATH}")
    print(f"  repo root:           {REPO_ROOT}")
    print(f"  Scrapy project root: {SCRAPY_PROJECT_ROOT}")
    print(f"  spiders dir:         {SPIDERS_DIR}")
    print(f"  output dir:          {OUTPUT_DIR}")
    print(f"  log dir:             {LOG_DIR}")
    print(f"  import scripts dir:  {IMPORT_DIR}")


def list_spiders() -> None:
    ensure_nested_spider_packages()
    run_step(
        Step(
            0,
            "available Scrapy spiders",
            "info",
            scrapy_cmd("list"),
            SCRAPY_PROJECT_ROOT,
        ),
        retries=0,
        dry_run=False,
        skip_existing=False,
    )


def file_has_content(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size > 0


def validate_required_inputs(step: Step) -> None:
    missing = [p for p in step.required_inputs if not file_has_content(p)]
    if missing:
        missing_str = "\n".join(f"  - {p}" for p in missing)
        raise SystemExit(
            f"ERROR: cannot run step {step.number} ({step.label}). "
            f"Required input file(s) are missing or empty:\n{missing_str}"
        )


def command_to_string(cmd: list[str]) -> str:
    return " ".join(str(part) for part in cmd)


def write_checkpoint(
    step: Step,
    status: str,
    attempt: int,
    duration_seconds: float | None = None,
    returncode: int | None = None,
) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    checkpoint_path = LOG_DIR / "latest_checkpoint.json"

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "step_number": step.number,
        "step_label": step.label,
        "step_kind": step.kind,
        "attempt": attempt,
        "duration_seconds": round(duration_seconds, 2) if duration_seconds is not None else None,
        "returncode": returncode,
        "output": str(step.output) if step.output else None,
        "command": command_to_string(step.cmd),
        "cwd": str(step.cwd),
    }
    checkpoint_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_step(step: Step, retries: int, dry_run: bool, skip_existing: bool) -> str:
    if step.output and skip_existing and file_has_content(step.output):
        print(f"\n=== Step {step.number}: {step.label} ===")
        print(f"[SKIP] Output already exists: {step.output}")
        write_checkpoint(step, "skipped_existing", attempt=0)
        return "skipped"

    validate_required_inputs(step)

    print(f"\n=== Step {step.number}: {step.label} ===")
    print("$ " + command_to_string(step.cmd))

    if dry_run:
        print("[DRY RUN] Command not executed.")
        write_checkpoint(step, "dry_run", attempt=0)
        return "dry-run"

    step.cwd.mkdir(parents=True, exist_ok=True)

    if step.output:
        step.output.parent.mkdir(parents=True, exist_ok=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{step.number:02d}_{slugify(step.label)}.log"

    max_attempts = 1 + max(0, retries)

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        print(f"[RUN] Attempt {attempt}/{max_attempts}")
        write_checkpoint(step, "running", attempt=attempt)

        with log_file.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "=" * 80 + "\n")
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"Step {step.number}: {step.label}\n")
            fh.write(f"Attempt {attempt}/{max_attempts}\n")
            fh.write(f"CWD: {step.cwd}\n")
            fh.write(f"CMD: {command_to_string(step.cmd)}\n")
            fh.write("=" * 80 + "\n")

            result = subprocess.run(
                step.cmd,
                cwd=str(step.cwd),
                text=True,
                shell=False,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )

        duration = time.perf_counter() - started

        if result.returncode == 0:
            print(f"[OK] Finished in {format_duration(duration)}")
            print(f"[LOG] {log_file}")
            write_checkpoint(
                step,
                "success",
                attempt=attempt,
                duration_seconds=duration,
                returncode=result.returncode,
            )
            return "success"

        print(f"[FAIL] Exit code {result.returncode} after {format_duration(duration)}")
        print(f"[LOG] {log_file}")
        write_checkpoint(
            step,
            "failed_attempt",
            attempt=attempt,
            duration_seconds=duration,
            returncode=result.returncode,
        )

        if attempt < max_attempts:
            print("[RETRY] Retrying step...")

    raise SystemExit(
        "\n"
        f"ERROR: step {step.number} failed after {max_attempts} attempt(s): {step.label}\n"
        f"Command: {command_to_string(step.cmd)}\n"
        f"Log file: {log_file}\n"
        f"Resume with: python {SCRIPT_PATH} --from-step {step.number}\n"
    )


def slugify(value: str) -> str:
    clean = []
    for char in value.lower():
        if char.isalnum():
            clean.append(char)
        elif char in {" ", "-", "_", ":"}:
            clean.append("_")
    return "".join(clean).strip("_") or "step"


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def filter_steps(
    steps: list[Step],
    from_step: int | None,
    to_step: int | None,
    skip_crawl: bool,
    skip_merge: bool,
) -> list[Step]:
    selected = steps

    if skip_crawl:
        selected = [step for step in selected if step.kind != "crawl"]

    if skip_merge:
        selected = [step for step in selected if step.kind != "merge"]

    if from_step is not None:
        selected = [step for step in selected if step.number >= from_step]

    if to_step is not None:
        selected = [step for step in selected if step.number <= to_step]

    return selected


def print_step_plan(steps: list[Step]) -> None:
    print("\nPlanned steps:")
    for step in steps:
        output_text = f" -> {step.output}" if step.output else ""
        print(f"  {step.number:02d}. [{step.kind}] {step.label}{output_text}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run UNIFR Scrapy spiders and merge steps with resume support."
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="Only print resolved paths and available Scrapy spiders.",
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print selected steps and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands and checkpoints without executing them.",
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
        "--skip-existing",
        action="store_true",
        help="Skip a step when its output file already exists and is not empty.",
    )
    parser.add_argument(
        "--from-step",
        type=int,
        default=None,
        help="Start from this numeric step, e.g. --from-step 12.",
    )
    parser.add_argument(
        "--to-step",
        type=int,
        default=None,
        help="Stop after this numeric step, e.g. --to-step 14.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=0,
        help="Retry each failed step this many additional times.",
    )
    parser.add_argument(
        "--print-paths",
        action="store_true",
        help="Print resolved paths before running.",
    )

    args = parser.parse_args()

    if args.retries < 0:
        raise SystemExit("ERROR: --retries must be >= 0")

    if args.from_step is not None and args.to_step is not None and args.from_step > args.to_step:
        raise SystemExit("ERROR: --from-step cannot be greater than --to-step")

    ensure_nested_spider_packages()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FACULTY_PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.print_paths or args.list_only:
        print_paths()

    if args.list_only:
        list_spiders()
        return

    steps = build_steps()
    selected_steps = filter_steps(
        steps,
        from_step=args.from_step,
        to_step=args.to_step,
        skip_crawl=args.skip_crawl,
        skip_merge=args.skip_merge,
    )

    if not selected_steps:
        raise SystemExit("No steps selected. Check --from-step/--to-step/--skip-* options.")

    print_step_plan(selected_steps)

    if args.plan:
        return

    started_all = time.perf_counter()
    summary: list[tuple[int, str, str]] = []

    for step in selected_steps:
        status = run_step(
            step,
            retries=args.retries,
            dry_run=args.dry_run,
            skip_existing=args.skip_existing,
        )
        summary.append((step.number, step.label, status))

    total_duration = time.perf_counter() - started_all

    print("\nPipeline summary:")
    for number, label, status in summary:
        print(f"  {number:02d}. {status.upper():12s} {label}")

    print(f"\nPipeline finished in {format_duration(total_duration)}.")
    print(f"Logs: {LOG_DIR}")


if __name__ == "__main__":
    main()
