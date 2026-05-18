from __future__ import annotations

import re
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.review_agent.engine import run_review
from src.review_agent.extractors import extract_submission


PROFILE_ID = "analysts_2026_requirements"

CRITERION_MARKERS = [
    ("1. Структура", "structure_template"),
    ("2. Оформление", "formatting_instruction"),
    ("3. Отсутств", "literacy_punctuation"),
    ("4.1.", "document_versioning"),
    ("4.2.", "terms_glossary"),
    ("4.3.", "table_of_contents"),
    ("4.4.", "system_naming"),
    ("4.5.", "business_goals"),
    ("4.6.", "current_situation"),
    ("4.7.", "data_requirements"),
    ("4.8.", "visualization_requirements"),
    ("4.9.", "non_functional_requirements"),
    ("4.10.", "security_access"),
    ("4.11.", "independent_work"),
]

NAME_MARKERS = {
    "demchenko": ["демченко"],
    "dubrovskaya": ["дубров"],
    "kostykin": ["костькин"],
    "nedobuga": ["недобуга"],
    "potapova": ["потапова"],
    "rybina": ["рыбина"],
    "semenov": ["семенов"],
    "ulyanets": ["ульянец"],
}


def detect_key(path: Path) -> str | None:
    name = path.name.lower()
    for key, markers in NAME_MARKERS.items():
        if any(marker in name for marker in markers):
            return key
    if path.name == "ФТТ_стажировка_пример.docx":
        return "dubrovskaya"
    return None


def parse_mentor_scores(path: Path) -> dict[str, int]:
    extracted = extract_submission(str(path))
    scores: dict[str, int] = {}
    for idx, line in enumerate(extracted.lines):
        criterion_id = next(
            (cid for marker, cid in CRITERION_MARKERS if line.strip().startswith(marker)),
            None,
        )
        if not criterion_id or criterion_id in scores:
            continue

        numbers: list[int] = []
        for next_line in extracted.lines[idx + 1 : idx + 10]:
            stripped = next_line.strip()
            if re.fullmatch(r"\d+", stripped):
                numbers.append(int(stripped))
                if len(numbers) >= 2:
                    break
        if len(numbers) >= 2:
            scores[criterion_id] = numbers[1]

    return scores


def collect_files(split: str) -> tuple[dict[str, Path], dict[str, Path]]:
    base = ROOT / "calibration_inbox" / split
    works: dict[str, Path] = {}
    mentor_scores: dict[str, Path] = {}

    for path in (base / "works").glob("*"):
        if path.suffix.lower() == ".pdf":
            continue
        key = detect_key(path)
        if key:
            works[key] = path

    for path in (base / "mentor_scores").glob("*"):
        key = detect_key(path)
        if key:
            mentor_scores[key] = path

    return works, mentor_scores


def run_split(split: str, *, enable_llm: bool) -> None:
    works, mentor_score_files = collect_files(split)
    total_abs_errors: list[int] = []
    criterion_abs_errors: list[int] = []

    print(f"\n{split.upper()}")
    for key in sorted(mentor_score_files):
        if key not in works:
            print(f"{key}: no work file")
            continue

        expected = parse_mentor_scores(mentor_score_files[key])
        result = run_review(str(works[key]), PROFILE_ID, enable_llm=enable_llm)
        actual = {
            item["criterion_id"]: round(item["weight"] * item["score"] / 100)
            for item in result["criteria"]
        }

        expected_total = sum(expected.values())
        actual_total = sum(actual.values())
        diff = actual_total - expected_total
        total_abs_errors.append(abs(diff))

        criterion_diffs = {
            criterion_id: actual.get(criterion_id, 0) - expected_score
            for criterion_id, expected_score in expected.items()
        }
        criterion_abs_errors.extend(abs(value) for value in criterion_diffs.values())

        non_zero_diffs = {k: v for k, v in criterion_diffs.items() if v}
        print(
            f"{key}: expected={expected_total} actual={actual_total} "
            f"diff={diff} diffs={non_zero_diffs}"
        )
        llm_status = result.get("llm_status", {}).get("status")
        if enable_llm:
            print(f"  llm_status={llm_status}")

    if criterion_abs_errors:
        criterion_mae = sum(criterion_abs_errors) / len(criterion_abs_errors)
        total_mae = sum(total_abs_errors) / len(total_abs_errors)
        print(f"criterion_MAE={criterion_mae:.3f} total_MAE={total_mae:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["calibration", "holdout", "all"], default="all")
    parser.add_argument("--enable-llm", action="store_true")
    args = parser.parse_args()

    splits = ["calibration", "holdout"] if args.split == "all" else [args.split]
    for split in splits:
        run_split(split, enable_llm=args.enable_llm)


if __name__ == "__main__":
    main()
