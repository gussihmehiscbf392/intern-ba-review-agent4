from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.review_agent.engine import run_review
from tools.evaluate_calibration import PROFILE_ID, collect_files, parse_mentor_scores


FOCUS_CRITERIA = [
    ("formatting_instruction", "2. Оформление"),
    ("visualization_requirements", "4.8 Визуализация"),
    ("non_functional_requirements", "4.9 НФТ"),
]

DISPLAY_NAMES = {
    "dubrovskaya": "Дубровская",
    "kostykin": "Костькин",
    "nedobuga": "Недобуга",
    "potapova": "Потапова",
    "rybina": "Рыбина",
    "semenov": "Семенов",
    "demchenko": "Демченко",
    "ulyanets": "Ульянец",
}


def _points(item: dict[str, Any]) -> int:
    return round(int(item.get("weight", 0)) * float(item.get("score", 0)) / 100)


def _criterion_by_id(result: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    for item in result.get("criteria", []):
        if item.get("criterion_id") == criterion_id:
            return item
    return {}


def _short(value: Any, limit: int = 240) -> str:
    if isinstance(value, list):
        text = "; ".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value or "").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _formatting_signals(result: dict[str, Any]) -> str:
    formatting = result.get("coverage_summary", {}).get("formatting_instruction", {})
    facts = formatting.get("checklist_facts", {}) if isinstance(formatting, dict) else {}
    hint = facts.get("decision_hint", {}) if isinstance(facts, dict) else {}
    checklist = facts.get("checklist", []) if isinstance(facts, dict) else []
    failed = []
    if isinstance(checklist, list):
        for item in checklist:
            if not isinstance(item, dict) or item.get("status") != "fail":
                continue
            rule_id = str(item.get("id", "")).strip()
            count = item.get("error_count", 0)
            systemic = "systemic" if item.get("systemic") else "isolated"
            if rule_id and rule_id != "systemic_formatting_errors":
                failed.append(f"{rule_id}:{count}/{systemic}")
            if len(failed) >= 5:
                break
    llm_check = result.get("llm_status", {}).get("payload", {}).get("formatting_instruction_check", {})
    llm_decisions = []
    if isinstance(llm_check, dict):
        for item in llm_check.get("rule_decisions", []) or []:
            if not isinstance(item, dict) or item.get("status") not in {"fail", "warn", "needs_review"}:
                continue
            llm_decisions.append(f"{item.get('rule_id')}:{item.get('status')}")
            if len(llm_decisions) >= 4:
                break
    return _short(
        [
            f"estimated_errors={hint.get('estimated_error_count', 0)}",
            f"systemic={','.join(hint.get('systemic_rule_ids', []) or [])}",
            f"failed_rules={'; '.join(failed)}",
            f"llm={'; '.join(llm_decisions)}",
        ],
        520,
    )


def _visualization_signals(result: dict[str, Any]) -> str:
    data = result.get("coverage_summary", {}).get("visualization_requirements", {})
    if not isinstance(data, dict):
        data = {}
    return _short(
        [
            f"section={data.get('section_present')}",
            f"coverage={data.get('coverage')}",
            f"quality_groups={data.get('quality_groups')}",
            f"strong_hits={data.get('strong_visual_hits')}",
            f"unbacked_factor={data.get('has_unbacked_factor_block')}",
        ],
        360,
    )


def _nfr_signals(result: dict[str, Any]) -> str:
    data = result.get("coverage_summary", {}).get("non_functional_requirements", {})
    if not isinstance(data, dict):
        data = {}
    return _short(
        [
            f"section={data.get('section_present')}",
            f"response_time={data.get('has_response_time')}",
            f"reliability_or_access={data.get('has_reliability_or_accessibility')}",
            f"functional_hits={data.get('functional_hits')}",
            f"mixes_security={data.get('mixes_security')}",
            f"unconfirmed_usability={data.get('has_unconfirmed_usability')}",
            f"keyword_hits={data.get('expected_keyword_hits')}",
        ],
        420,
    )


def _signals(result: dict[str, Any], criterion_id: str) -> str:
    if criterion_id == "formatting_instruction":
        return _formatting_signals(result)
    if criterion_id == "visualization_requirements":
        return _visualization_signals(result)
    if criterion_id == "non_functional_requirements":
        return _nfr_signals(result)
    return ""


def build_rows(split: str, *, enable_llm: bool) -> list[dict[str, str]]:
    works, mentor_files = collect_files(split)
    rows: list[dict[str, str]] = []
    for key in sorted(mentor_files):
        if key not in works:
            continue
        expected = parse_mentor_scores(mentor_files[key])
        result = run_review(str(works[key]), PROFILE_ID, enable_llm=enable_llm)
        for criterion_id, title in FOCUS_CRITERIA:
            item = _criterion_by_id(result, criterion_id)
            mentor = expected.get(criterion_id, 0)
            app = _points(item) if item else 0
            rows.append(
                {
                    "split": split,
                    "work": DISPLAY_NAMES.get(key, key),
                    "criterion": title,
                    "mentor": str(mentor),
                    "app": str(app),
                    "diff": f"{app - mentor:+d}",
                    "signals": _signals(result, criterion_id),
                    "rationale": _short(item.get("rationale", ""), 360),
                    "evidence": _short(item.get("evidence", []), 260),
                    "llm_status": str(result.get("llm_status", {}).get("status", "n/a")),
                }
            )
    return rows


def write_outputs(rows: list[dict[str, str]], split: str, enable_llm: bool) -> tuple[Path, Path]:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mode = "llm" if enable_llm else "rules"
    base = ROOT / "outputs" / f"diagnostic_focus_{split}_{mode}_{stamp}"
    csv_path = base.with_suffix(".csv")
    md_path = base.with_suffix(".md")

    fieldnames = ["split", "work", "criterion", "mentor", "app", "diff", "signals", "rationale", "evidence", "llm_status"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# Диагностика критериев: {split}, режим {'LLM hybrid' if enable_llm else 'rule-based'}",
        "",
        "| Работа | Критерий | Наставник | Агент | Diff | Признаки | Объяснение |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {work} | {criterion} | {mentor} | {app} | {diff} | {signals} | {rationale} |".format(
                **{key: str(value).replace("|", "\\|") for key, value in row.items()}
            )
        )
    lines.append("")
    lines.append("Diff = app - mentor. Положительный diff означает завышение агентом, отрицательный - занижение.")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["calibration", "holdout"], default="calibration")
    parser.add_argument("--enable-llm", action="store_true")
    args = parser.parse_args()

    rows = build_rows(args.split, enable_llm=args.enable_llm)
    md_path, csv_path = write_outputs(rows, args.split, args.enable_llm)
    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    for row in rows:
        if row["diff"] != "+0":
            print(f"{row['work']} | {row['criterion']} | mentor={row['mentor']} app={row['app']} diff={row['diff']}")


if __name__ == "__main__":
    main()
