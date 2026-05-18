from __future__ import annotations

import argparse
from pathlib import Path

from .engine import run_review
from .reporting import save_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MVP-проверка работ стажеров-аналитиков (задание: Сбор требований)"
    )
    parser.add_argument("--input", required=True, help="Путь к файлу ответа стажера (DOCX/PDF)")
    parser.add_argument(
        "--profile",
        default="analysts_2026_requirements",
        help="Идентификатор профиля задания",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs",
        help="Папка для сохранения JSON и Markdown отчета",
    )
    parser.add_argument(
        "--disable-llm",
        action="store_true",
        help="Отключить LLM-слой и использовать только rule-based проверку",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_review(
        input_path=args.input,
        profile_id=args.profile,
        enable_llm=not args.disable_llm,
    )

    base_name = f"review_{Path(args.input).stem}"
    json_path, md_path = save_outputs(result, args.output_dir, base_name)

    print(f"JSON: {json_path}")
    print(f"MD: {md_path}")
    print(f"Status: {result.get('status')}")
    if result.get("status") == "ok":
        print(f"Overall score: {result.get('overall_score')}")
        return 0

    print(f"Error: {result.get('error')}")
    return 2

