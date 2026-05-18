from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .models import AnalysisResult, CriterionScore, Issue, RiskSignal


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^а-яёa-z0-9 ]", " ", text.lower())).strip()


def _clip(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _find_matching_lines(lines: list[str], pattern: str) -> list[str]:
    compiled = re.compile(pattern, flags=re.IGNORECASE)
    return [line for line in lines if compiled.search(line)]


def _compact_unique(items: list[Any], limit: int = 8) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in items:
        text = re.sub(r"\s+", " ", str(raw).strip())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(_clip(text, 220))
        if len(result) >= limit:
            break
    return result


def _formatting_checklist_facts(
    formatting_metadata: dict[str, Any] | None,
    template_hint_evidence: list[str],
) -> dict[str, Any]:
    metadata = formatting_metadata if isinstance(formatting_metadata, dict) else {}
    doc_format = str(metadata.get("format", "")).strip() or "unknown"
    table_count = int(metadata.get("table_count", 0) or 0)
    heading_style_count = int(metadata.get("heading_style_count", 0) or 0)
    numbered_paragraph_count = int(metadata.get("numbered_paragraph_count", 0) or 0)
    toc_like_line_count = int(metadata.get("toc_like_line_count", 0) or 0)
    template_hint_candidates = metadata.get("template_hint_candidates", [])
    if not isinstance(template_hint_candidates, list):
        template_hint_candidates = []

    metadata_hint_evidence = _compact_unique(
        [
            item.get("text", "")
            for item in template_hint_candidates
            if isinstance(item, dict) and item.get("text")
        ],
        limit=10,
    )
    all_hint_evidence = _compact_unique(template_hint_evidence + metadata_hint_evidence, limit=10)

    table_samples = []
    tables_raw = metadata.get("tables", [])
    if not isinstance(tables_raw, list):
        tables_raw = []
    for table in tables_raw:
        if not isinstance(table, dict):
            continue
        sample = str(table.get("sample_text", "")).strip()
        if sample:
            table_samples.append(f"Таблица {table.get('index', '?')}: {sample}")

    heading_samples = [
        item.get("text", "")
        for item in metadata.get("heading_candidates", [])
        if isinstance(item, dict) and item.get("text")
    ] if isinstance(metadata.get("heading_candidates", []), list) else []

    toc_samples = metadata.get("toc_like_samples", [])
    if not isinstance(toc_samples, list):
        toc_samples = []
    numbered_samples = metadata.get("numbered_samples", [])
    if not isinstance(numbered_samples, list):
        numbered_samples = []
    font_summary = metadata.get("font_summary", {})
    if not isinstance(font_summary, dict):
        font_summary = {}
    font_checked_runs = int(font_summary.get("checked_text_runs", 0) or 0)
    non_verdana_count = int(font_summary.get("non_verdana_count", 0) or 0)
    non_verdana_samples_raw = font_summary.get("non_verdana_samples", [])
    if not isinstance(non_verdana_samples_raw, list):
        non_verdana_samples_raw = []
    non_verdana_samples_by_font_raw = font_summary.get("non_verdana_samples_by_font", {})
    if not isinstance(non_verdana_samples_by_font_raw, dict):
        non_verdana_samples_by_font_raw = {}

    non_verdana_sample_items: list[dict[str, Any]] = []
    for font in sorted(non_verdana_samples_by_font_raw):
        samples = non_verdana_samples_by_font_raw.get(font, [])
        if isinstance(samples, list):
            non_verdana_sample_items.extend(item for item in samples[:2] if isinstance(item, dict))
    non_verdana_sample_items.extend(item for item in non_verdana_samples_raw if isinstance(item, dict))
    non_verdana_samples = _compact_unique(
        [
            f"{item.get('font', '')}: {item.get('text', '')}"
            for item in non_verdana_sample_items
        ],
        limit=12,
    )
    paragraph_summary = metadata.get("paragraph_format_summary", {})
    if not isinstance(paragraph_summary, dict):
        paragraph_summary = {}
    size_counts = paragraph_summary.get("size_counts", {})
    if not isinstance(size_counts, dict):
        size_counts = {}
    non_body_size_samples = paragraph_summary.get("non_body_size_samples", [])
    if not isinstance(non_body_size_samples, list):
        non_body_size_samples = []
    body_spacing_issues = paragraph_summary.get("body_spacing_before_issues", [])
    if not isinstance(body_spacing_issues, list):
        body_spacing_issues = []
    body_indent_issues = paragraph_summary.get("body_indent_issues", [])
    if not isinstance(body_indent_issues, list):
        body_indent_issues = []
    heading_format_samples_raw = paragraph_summary.get("heading_format_samples", [])
    if not isinstance(heading_format_samples_raw, list):
        heading_format_samples_raw = []
    list_format_samples_raw = paragraph_summary.get("list_format_samples", [])
    if not isinstance(list_format_samples_raw, list):
        list_format_samples_raw = []
    table_reference_summary = metadata.get("table_reference_summary", {})
    if not isinstance(table_reference_summary, dict):
        table_reference_summary = {}
    figure_summary = metadata.get("figure_summary", {})
    if not isinstance(figure_summary, dict):
        figure_summary = {}
    footer_summary = metadata.get("footer_summary", {})
    if not isinstance(footer_summary, dict):
        footer_summary = {}

    def _issue_status(count: int, systemic: bool = False) -> str:
        if count <= 0:
            return "pass"
        return "fail" if systemic or count >= 3 else "warn"

    def _metadata_limited(rule_id: str, rule: str, evidence: str) -> dict[str, Any]:
        return {
            "id": rule_id,
            "rule": rule,
            "status": "needs_review",
            "blocking": False,
            "error_count": 0,
            "systemic": False,
            "evidence": [evidence],
        }

    def _heading_samples(level: int | None = None) -> list[dict[str, Any]]:
        result = []
        for item in heading_format_samples_raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", ""))
            match = re.match(r"^(\d+(?:\.\d+)*)", text)
            detected_level = len(match.group(1).split(".")) if match else None
            if level is None or detected_level == level:
                result.append(item)
        return result

    def _heading_errors(samples: list[dict[str, Any]], expected_size: int, require_caps: bool | None) -> list[str]:
        errors: list[str] = []
        for item in samples:
            fonts = [str(font).lower() for font in item.get("fonts", []) if str(font).strip()]
            sizes = [float(size) for size in item.get("sizes_pt", []) if isinstance(size, (int, float))]
            text = str(item.get("text", ""))
            if fonts and any(font != "verdana" for font in fonts):
                errors.append(f"{text}: шрифт {', '.join(item.get('fonts', []))}")
            if sizes and any(abs(size - expected_size) > 0.1 for size in sizes):
                errors.append(f"{text}: размер {', '.join(str(size) for size in sizes)}")
            if not item.get("all_bold"):
                errors.append(f"{text}: не весь заголовок полужирный")
            if require_caps is True and not item.get("all_caps"):
                errors.append(f"{text}: заголовок первого уровня не прописными буквами")
            if require_caps is False and item.get("all_caps"):
                errors.append(f"{text}: заголовок не должен быть полностью прописными буквами")
            if len(errors) >= 8:
                break
        return _compact_unique(errors, limit=8)

    def _list_errors(samples: list[dict[str, Any]]) -> list[str]:
        errors: list[str] = []
        for item in samples:
            text = str(item.get("text", ""))
            fonts = [str(font).lower() for font in item.get("fonts", []) if str(font).strip()]
            sizes = [float(size) for size in item.get("sizes_pt", []) if isinstance(size, (int, float))]
            is_numbered_item = bool(item.get("starts_with_arabic_dot")) or bool(re.match(r"^\d+\.", text))
            numbering_format = str(item.get("numbering_format", "")).lower()
            numbering_text = str(item.get("numbering_text", "")).strip()
            is_bulleted_item = (
                numbering_format == "bullet"
                or (bool(item.get("numbering_id")) and not is_numbered_item)
                or (bool(item.get("numbering_level")) and not is_numbered_item)
            )
            if fonts and any(font != "verdana" for font in fonts):
                errors.append(f"{text}: шрифт списка {', '.join(item.get('fonts', []))}")
            if sizes and any(abs(size - 9) > 0.1 for size in sizes):
                errors.append(f"{text}: размер списка {', '.join(str(size) for size in sizes)}")
            if text.startswith("•") or text.startswith("*") or (
                is_bulleted_item and not item.get("starts_with_hyphen") and numbering_text not in {"-", "–", "—"}
            ):
                errors.append(f"{text}: маркер списка не дефис")
            if re.match(r"^\d+[)]", text):
                errors.append(f"{text}: нумерация должна быть арабской цифрой с точкой")
            if len(errors) >= 8:
                break
        return _compact_unique(errors, limit=8)

    checklist: list[dict[str, Any]] = []

    checklist.append(
        {
            "id": "no_template_explanations",
            "rule": "В финальной версии нет служебных пояснений, серых подсказок и примеров из шаблона.",
            "status": "fail" if all_hint_evidence else "pass",
            "blocking": bool(all_hint_evidence),
            "error_count": len(all_hint_evidence),
            "systemic": len(all_hint_evidence) >= 3,
            "evidence": all_hint_evidence,
        }
    )

    if doc_format != "docx":
        font_status = "needs_review"
        font_error_count = 0
        font_systemic = False
        font_evidence = ["Метаданные шрифтов доступны только для DOCX."]
    elif font_checked_runs == 0:
        font_status = "needs_review"
        font_error_count = 0
        font_systemic = False
        font_evidence = ["Не удалось определить шрифты текста из DOCX-метаданных."]
    else:
        non_verdana_ratio = non_verdana_count / font_checked_runs
        font_systemic = non_verdana_count >= 3 or non_verdana_ratio > 0.05
        font_status = "pass" if non_verdana_count == 0 else ("fail" if font_systemic else "warn")
        font_error_count = 0 if non_verdana_count == 0 else (3 if font_systemic else non_verdana_count)
        font_evidence = non_verdana_samples or [f"Найдено фрагментов не Verdana: {non_verdana_count}."]
    checklist.append(
        {
            "id": "font_verdana",
            "rule": "При оформлении документа необходимо использовать шрифт Verdana.",
            "status": font_status,
            "blocking": False,
            "error_count": font_error_count,
            "systemic": font_systemic,
            "evidence": font_evidence,
        }
    )

    body_size_errors = _compact_unique(
        [f"{item.get('size', '')}: {item.get('text', '')}" for item in non_body_size_samples if isinstance(item, dict)],
        limit=8,
    )
    body_size_count = len(body_size_errors)
    checklist.append(
        {
            "id": "body_font_size_9",
            "rule": "Основной текст должен быть набран размером 9 пунктов.",
            "status": _issue_status(body_size_count, body_size_count >= 3),
            "blocking": False,
            "error_count": 3 if body_size_count >= 3 else body_size_count,
            "systemic": body_size_count >= 3,
            "evidence": body_size_errors or [f"Размеры текста по DOCX: {size_counts}"],
        }
    )

    spacing_errors = _compact_unique(
        [
            f"before={item.get('before_twips', '')} twips: {item.get('text', '')}"
            for item in body_spacing_issues
            if isinstance(item, dict)
        ],
        limit=8,
    )
    spacing_count = len(spacing_errors)
    checklist.append(
        {
            "id": "body_single_line_no_before_spacing",
            "rule": "Основной текст: одинарный межстрочный интервал; перед абзацем увеличение интервала не допускается.",
            "status": _issue_status(spacing_count, spacing_count >= 3),
            "blocking": False,
            "error_count": 3 if spacing_count >= 3 else spacing_count,
            "systemic": spacing_count >= 3,
            "evidence": spacing_errors or ["У основного текста не выявлены интервалы перед абзацем по DOCX-метаданным."],
        }
    )

    body_indent_errors = _compact_unique(
        [
            f"{item.get('indent', '')}: {item.get('text', '')}"
            for item in body_indent_issues
            if isinstance(item, dict)
        ],
        limit=8,
    )
    body_indent_count = len(body_indent_errors)
    checklist.append(
        {
            "id": "body_no_indent",
            "rule": "Основной текст выполняется без абзацного отступа и выступа.",
            "status": _issue_status(body_indent_count, body_indent_count >= 3),
            "blocking": False,
            "error_count": 3 if body_indent_count >= 3 else body_indent_count,
            "systemic": body_indent_count >= 3,
            "evidence": body_indent_errors or ["У основного текста не выявлены отступы/выступы по DOCX-метаданным."],
        }
    )

    for rule_id, level, expected_size, caps_rule, rule_text in [
        (
            "heading_level1_format",
            1,
            12,
            True,
            "Заголовки первого уровня: прописные буквы, полужирный Verdana 12, одинарный интервал, интервалы 30/12 пт.",
        ),
        (
            "heading_level2_format",
            2,
            12,
            False,
            "Заголовки второго уровня: первая прописная, полужирный Verdana 12, одинарный интервал, интервалы 20/10 пт.",
        ),
        (
            "heading_level3_plus_format",
            3,
            11,
            False,
            "Заголовки третьего и следующих уровней: первая прописная, полужирный Verdana 11, одинарный интервал, интервалы 20/10 пт.",
        ),
    ]:
        samples = _heading_samples(level)
        errors = _heading_errors(samples, expected_size, caps_rule)
        status = "needs_review" if doc_format != "docx" else _issue_status(len(errors), len(errors) >= 3)
        checklist.append(
            {
                "id": rule_id,
                "rule": rule_text,
                "status": status,
                "blocking": False,
                "error_count": 0 if status == "needs_review" else (3 if len(errors) >= 3 else len(errors)),
                "systemic": len(errors) >= 3,
                "evidence": errors or _compact_unique([item.get("text", "") for item in samples if isinstance(item, dict)], limit=6)
                or ["Подходящие заголовки не найдены или требуют ручной проверки уровня."],
            }
        )

    checklist.extend(
        [
            _metadata_limited(
                "numbered_heading_indents",
                "Пронумерованные заголовки 1/2/3+ уровней выполняются с заданными абзацными выступами 1,15/1,6/1,8 мм.",
                "DOCX содержит indent/hanging в twips; точное сопоставление с мм и уровнем заголовка передается LLM как метаданные.",
            ),
            _metadata_limited(
                "unnumbered_heading_indent",
                "Непронумерованные заголовки выполняются с абзацного отступа 1,25 мм.",
                "Непронумерованные заголовки требуют экспертной сверки по indent/firstLine в DOCX-метаданных.",
            ),
        ]
    )

    list_errors = _list_errors([item for item in list_format_samples_raw if isinstance(item, dict)])
    checklist.append(
        {
            "id": "list_font_and_markers",
            "rule": "Списки: Verdana 9, одинарный интервал; нумерованные пункты арабской цифрой с точкой, маркированные - дефисом.",
            "status": _issue_status(len(list_errors), len(list_errors) >= 3),
            "blocking": False,
            "error_count": 3 if len(list_errors) >= 3 else len(list_errors),
            "systemic": len(list_errors) >= 3,
            "evidence": list_errors or _compact_unique(
                [item.get("text", "") for item in list_format_samples_raw if isinstance(item, dict)],
                limit=6,
            )
            or ["Списки по DOCX-метаданным не найдены."],
        }
    )
    checklist.extend(
        [
            _metadata_limited(
                "list_indents",
                "Перечисления 1/2/3+ уровней выполняются с заданными абзацными выступами/отступами 1/1,2/1,8 мм.",
                "Уровни списков и indent/hanging извлечены в `list_format_samples`; итоговая оценка передается LLM.",
            ),
            _metadata_limited(
                "list_capitalization_punctuation",
                "Пункты перечислений начинаются с прописной буквы и заканчиваются точкой либо единообразно строчными с точкой с запятой.",
                "Правило частично языковое и проверяется LLM по sample строкам списков.",
            ),
        ]
    )

    if doc_format != "docx":
        table_status = "needs_review"
        table_error_count = 0
        table_evidence = ["Метаданные таблиц доступны только для DOCX."]
    elif table_count >= 2:
        table_status = "pass"
        table_error_count = 0
        table_evidence = _compact_unique(table_samples, limit=5)
    elif table_count == 1:
        table_status = "warn"
        table_error_count = 1
        table_evidence = _compact_unique(table_samples + ["В DOCX найдена только одна таблица."], limit=5)
    else:
        table_status = "warn"
        table_error_count = 1
        table_evidence = ["В DOCX не найдены таблицы, хотя инструкция ожидает табличные разделы."]
    checklist.append(
        {
            "id": "template_tables",
            "rule": "Табличные разделы инструкции оформлены таблицами.",
            "status": table_status,
            "blocking": False,
            "error_count": table_error_count,
            "systemic": table_count == 0 and doc_format == "docx",
            "evidence": table_evidence,
        }
    )

    table_caption_count = int(table_reference_summary.get("table_caption_count", 0) or 0)
    table_reference_count = int(table_reference_summary.get("table_reference_count", 0) or 0)
    table_captions = table_reference_summary.get("table_captions", [])
    if not isinstance(table_captions, list):
        table_captions = []
    table_references = table_reference_summary.get("table_references", [])
    if not isinstance(table_references, list):
        table_references = []
    table_caption_gap = max(0, table_count - table_caption_count)
    checklist.append(
        {
            "id": "table_sequential_numbering_and_caption",
            "rule": "Таблицы нумеруются сквозной арабской нумерацией; подпись над таблицей в формате «Таблица X - Наименование».",
            "status": _issue_status(table_caption_gap, table_caption_gap >= 3),
            "blocking": False,
            "error_count": 3 if table_caption_gap >= 3 else table_caption_gap,
            "systemic": table_caption_gap >= 3,
            "evidence": _compact_unique(table_captions, limit=8)
            or [f"Таблиц найдено: {table_count}; подписей таблиц найдено: {table_caption_count}."],
        }
    )
    checklist.append(
        {
            "id": "table_caption_formatting",
            "rule": "Подпись таблицы: Verdana 9, одинарный интервал, выравнивание по правому краю, без отступов/интервалов.",
            "status": "needs_review",
            "blocking": False,
            "error_count": 0,
            "systemic": False,
            "evidence": _compact_unique(table_captions, limit=8)
            or ["Формат подписи таблицы требует проверки по paragraph-format samples."],
        }
    )
    checklist.append(
        {
            "id": "table_body_and_header_formatting",
            "rule": "Текст в таблицах: Verdana 9 слева; строка заголовков - полужирный Verdana 11 по центру без отступов.",
            "status": "needs_review",
            "blocking": False,
            "error_count": 0,
            "systemic": False,
            "evidence": _compact_unique(table_samples, limit=5)
            or ["Детальная проверка форматирования ячеек требует table cell metadata."],
        }
    )
    checklist.append(
        {
            "id": "table_references_and_placement",
            "rule": "В тексте есть ссылки «см. Таблица X»; таблица размещается после первой ссылки, при переносе повторяются заголовки и «Продолжение таблицы X».",
            "status": "pass" if table_count == 0 or table_reference_count >= min(table_count, 1) else "warn",
            "blocking": False,
            "error_count": 0 if table_count == 0 or table_reference_count >= min(table_count, 1) else 1,
            "systemic": table_count > 1 and table_reference_count == 0,
            "evidence": _compact_unique(table_references, limit=8)
            or [f"Таблиц найдено: {table_count}; ссылок на таблицы найдено: {table_reference_count}."],
        }
    )
    checklist.append(
        {
            "id": "table_borders_no_indents",
            "rule": "Границы таблиц оформляются без отступов.",
            "status": "needs_review",
            "blocking": False,
            "error_count": 0,
            "systemic": False,
            "evidence": ["Границы и внутренние отступы таблиц требуют отдельного анализа table properties XML."],
        }
    )

    drawing_count = int(figure_summary.get("drawing_count", 0) or 0)
    figure_caption_count = int(figure_summary.get("figure_caption_count", 0) or 0)
    figure_reference_count = int(figure_summary.get("figure_reference_count", 0) or 0)
    figure_captions = figure_summary.get("figure_captions", [])
    if not isinstance(figure_captions, list):
        figure_captions = []
    figure_references = figure_summary.get("figure_references", [])
    if not isinstance(figure_references, list):
        figure_references = []
    figure_caption_gap = max(0, drawing_count - figure_caption_count)
    checklist.append(
        {
            "id": "figure_numbering_caption_and_references",
            "rule": "Рисунки нумеруются сквозной арабской нумерацией; подпись под рисунком «Рисунок X - Наименование»; в тексте есть ссылки «см. Рисунок X».",
            "status": _issue_status(figure_caption_gap, figure_caption_gap >= 3),
            "blocking": False,
            "error_count": 3 if figure_caption_gap >= 3 else figure_caption_gap,
            "systemic": figure_caption_gap >= 3,
            "evidence": _compact_unique(figure_captions + figure_references, limit=8)
            or [f"Рисунков найдено: {drawing_count}; подписей: {figure_caption_count}; ссылок: {figure_reference_count}."],
        }
    )
    checklist.append(
        {
            "id": "figure_caption_formatting_and_readability",
            "rule": "Подпись рисунка: Verdana 9, одинарный интервал, по центру; рисунки читаемы при печати A4, допускается альбомная ориентация.",
            "status": "needs_review",
            "blocking": False,
            "error_count": 0,
            "systemic": False,
            "evidence": _compact_unique(figure_captions, limit=8)
            or ["Читаемость рисунков и точное оформление подписи требуют LLM/ручной проверки."],
        }
    )

    toc_status = "pass" if toc_like_line_count >= 3 else "warn"
    checklist.append(
        {
            "id": "toc_format",
            "rule": "Оглавление похоже на оглавление с разделами/подразделами и номерами страниц.",
            "status": toc_status,
            "blocking": False,
            "error_count": 0 if toc_status == "pass" else 1,
            "systemic": False,
            "evidence": _compact_unique(toc_samples, limit=6)
            or ["Не найдено достаточное количество строк, похожих на оглавление."],
        }
    )

    heading_signals = len(heading_samples)
    heading_status = "pass" if heading_style_count > 0 or heading_signals >= 6 else "warn"
    checklist.append(
        {
            "id": "heading_numbering",
            "rule": "Заголовки и уровни разделов оформлены единообразно.",
            "status": heading_status,
            "blocking": False,
            "error_count": 0 if heading_status == "pass" else 1,
            "systemic": heading_status == "warn" and heading_signals < 3,
            "evidence": _compact_unique(heading_samples, limit=8)
            or ["Не найдено достаточное количество признаков заголовков."],
        }
    )

    structured_status = "pass" if table_count >= 1 and (heading_style_count > 0 or heading_signals >= 6) else "warn"
    checklist.append(
        {
            "id": "structured_sections",
            "rule": "Структурные блоки шаблона визуально отделены: таблицы, заголовки, списки используются по назначению.",
            "status": structured_status,
            "blocking": False,
            "error_count": 0 if structured_status == "pass" else 1,
            "systemic": structured_status == "warn" and table_count == 0,
            "evidence": _compact_unique(
                [
                    f"Таблиц: {table_count}",
                    f"Абзацев со стилями заголовков: {heading_style_count}",
                    f"Нумерованных абзацев/кандидатов: {numbered_paragraph_count}",
                ],
                limit=5,
            ),
        }
    )

    page_field_count = int(footer_summary.get("page_field_count", 0) or 0)
    numpages_field_count = int(footer_summary.get("numpages_field_count", 0) or 0)
    footer_texts = footer_summary.get("footer_texts", [])
    if not isinstance(footer_texts, list):
        footer_texts = []
    footer_font_counts = footer_summary.get("font_counts", {})
    if not isinstance(footer_font_counts, dict):
        footer_font_counts = {}
    footer_size_counts = footer_summary.get("size_counts", {})
    if not isinstance(footer_size_counts, dict):
        footer_size_counts = {}
    has_footer_page_number = page_field_count > 0 or any(re.search(r"\b\d+\s*(?:из|/)\s*\d+\b", text) for text in footer_texts)
    checklist.append(
        {
            "id": "page_numbering_footer",
            "rule": "Страницы нумеруются арабскими цифрами сквозной нумерацией с текущей страницей из общего количества; номер внизу справа, полужирный Verdana 9.",
            "status": "pass" if has_footer_page_number else "warn",
            "blocking": False,
            "error_count": 0 if has_footer_page_number else 1,
            "systemic": False,
            "evidence": _compact_unique(footer_texts, limit=6)
            or [f"PAGE fields: {page_field_count}; NUMPAGES fields: {numpages_field_count}."],
        }
    )
    checklist.append(
        {
            "id": "footer_document_name",
            "rule": "В нижнем колонтитуле справа указано наименование документа шрифтом Verdana 9.",
            "status": "pass" if footer_texts else "warn",
            "blocking": False,
            "error_count": 0 if footer_texts else 1,
            "systemic": False,
            "evidence": _compact_unique(
                footer_texts
                + [
                    f"footer fonts: {footer_font_counts}",
                    f"footer sizes: {footer_size_counts}",
                ],
                limit=8,
            ),
        }
    )

    warn_or_fail = [
        item
        for item in checklist
        if item["status"] in {"fail", "warn"} and item["id"] != "systemic_formatting_errors"
    ]
    systemic_ids = [item["id"] for item in warn_or_fail if item.get("systemic") or item.get("blocking")]
    estimated_error_count = sum(int(item.get("error_count", 0) or 0) for item in warn_or_fail)
    checklist.append(
        {
            "id": "systemic_formatting_errors",
            "rule": "Ошибки оформления не повторяются по всему документу.",
            "status": "fail" if systemic_ids or estimated_error_count >= 3 else "pass",
            "blocking": bool(systemic_ids),
            "error_count": estimated_error_count,
            "systemic": bool(systemic_ids),
            "evidence": systemic_ids or ["Системные признаки оформления по метаданным не выявлены."],
        }
    )

    return {
        "source": "docx_metadata_and_text_rules",
        "document_format": doc_format,
        "metadata_summary": {
            "paragraph_count": int(metadata.get("paragraph_count", 0) or 0),
            "table_count": table_count,
            "heading_style_count": heading_style_count,
            "heading_candidate_count": heading_signals,
            "font_checked_text_runs": font_checked_runs,
            "non_verdana_count": non_verdana_count,
            "font_counts": font_summary.get("font_counts", {}),
            "numbered_paragraph_count": numbered_paragraph_count,
            "toc_like_line_count": toc_like_line_count,
            "template_hint_candidate_count": int(metadata.get("template_hint_candidate_count", 0) or 0),
            "shaded_or_highlighted_count": int(metadata.get("shaded_or_highlighted_count", 0) or 0),
            "table_caption_count": int(table_reference_summary.get("table_caption_count", 0) or 0),
            "table_reference_count": int(table_reference_summary.get("table_reference_count", 0) or 0),
            "drawing_count": int(figure_summary.get("drawing_count", 0) or 0),
            "figure_caption_count": int(figure_summary.get("figure_caption_count", 0) or 0),
            "figure_reference_count": int(figure_summary.get("figure_reference_count", 0) or 0),
            "footer_page_field_count": int(footer_summary.get("page_field_count", 0) or 0),
            "footer_numpages_field_count": int(footer_summary.get("numpages_field_count", 0) or 0),
        },
        "checklist": checklist,
        "compact_samples": {
            "tables": _compact_unique(table_samples, limit=5),
            "headings": _compact_unique(heading_samples, limit=10),
            "toc": _compact_unique(toc_samples, limit=8),
            "numbering": _compact_unique(numbered_samples, limit=8),
            "table_captions": _compact_unique(table_captions, limit=8),
            "table_references": _compact_unique(table_references, limit=8),
            "figure_captions": _compact_unique(figure_captions, limit=8),
            "figure_references": _compact_unique(figure_references, limit=8),
            "footer_texts": _compact_unique(footer_texts, limit=6),
        },
        "decision_hint": {
            "must_zero_if_template_explanation": bool(all_hint_evidence),
            "estimated_error_count": estimated_error_count,
            "systemic_rule_ids": systemic_ids,
        },
    }


def _detect_language_error_hints(lines: list[str]) -> list[str]:
    patterns = [
        (r"\bнеобходимости\s+подготовки\s+отчет\s+по\s+продажам\b", "нарушение управления: нужно «подготовки отчета»"),
        (
            r"\b[а-яё]+(?:\s+[а-яё]+)?\s+анализирует\s+отчет\s+по\s+продажам\s+ассортименту\b",
            "нарушение управления: некорректная связь «по продажам ассортименту»",
        ),
        (
            r"\b[а-яё]+\s+(?:должен|должна|должны|должно)\s+обновлять\s+данных\b",
            "нарушение управления: нужно «обновлять данные»",
        ),
        (r"\bтип\s+агрегации\s+по\s+по\s+магазинам\b", "повтор предлога «по»"),
        (r"\bв\s+сплывающем\s+окне\b", "опечатка: нужно «всплывающем»"),
        (r"\bдолжн(?:а|ы|о)?\s+производится\b", "ошибка формы глагола: нужно «производиться»"),
        (r"\bдоступ\s+к\s+ко\s+всем\s+данным\b", "лишний предлог «к»"),
        (r"\bв\s+ручную\b", "ошибка написания: нужно «вручную»"),
        (r"\bв\s+праве\b", "ошибка написания: в этом контексте нужно «вправе»"),
        (r"\bедин(?:ый|ого|ым|ом)\s+отчет\b", "нарушение согласования: нужно «единого отчета»"),
        (r"\bвремя\b.{0,80}\bне\s+должна\b", "нарушение согласования: «время» не должно быть"),
        (r"\bдоступ\s+данных\b", "нарушение управления: нужно «доступ к данным»"),
        (r"\bс\s+1\s+по\s+3\s+числа\b", "нарушение согласования: корректнее «с 1-го по 3-е число»"),
        (r"\b[а-яёa-z]+,[а-яёa-z]+\b", "отсутствует пробел после запятой"),
        (r"\b([а-яё]{2,})\s+\1\b", "повтор слова"),
    ]

    errors: list[str] = []
    seen: set[str] = set()
    for pattern, comment in patterns:
        compiled = re.compile(pattern, flags=re.IGNORECASE)
        for line in lines:
            for match in compiled.finditer(line):
                fragment = re.sub(r"\s+", " ", match.group(0)).strip(" .;:-")
                if not fragment:
                    continue
                key = fragment.lower()
                if key in seen or any(key in existing or existing in key for existing in seen):
                    continue
                seen.add(key)
                errors.append(f"{fragment} — {comment}")
    return errors


def _contains_keyword(norm_text: str, keyword: str) -> bool:
    norm_keyword = _normalize(keyword)
    if not norm_keyword:
        return False
    if norm_keyword in norm_text:
        return True
    if " " in norm_keyword:
        return False
    if len(norm_keyword) < 5:
        return False
    # Light stemming heuristic to catch common Russian inflections.
    return bool(re.search(rf"\b{re.escape(norm_keyword[:5])}\w*\b", norm_text))


def _has_any(norm_text: str, markers: list[str]) -> bool:
    return any(_contains_keyword(norm_text, marker) for marker in markers)


def _count_groups(norm_text: str, groups: list[list[str]]) -> int:
    return sum(1 for group in groups if _has_any(norm_text, group))


def _section_found(normalized_text: str, section: str, aliases: dict[str, list[str]]) -> bool:
    candidates = [section]
    candidates.extend(aliases.get(section, []))
    return any(_contains_keyword(normalized_text, candidate) for candidate in candidates)


def _find_section_evidence(lines: list[str], section: str, aliases: dict[str, list[str]]) -> str:
    candidates = [section]
    candidates.extend(aliases.get(section, []))
    normalized_candidates = [_normalize(candidate) for candidate in candidates]

    def _looks_like_toc_row(raw_line: str) -> bool:
        compact = re.sub(r"\s+", "", raw_line)
        return bool(re.search(r"^\d+(?:\.\d+)*\.?.+\d{1,3}$", compact))

    for line in lines:
        clean_line = re.sub(r"\s+", " ", line).strip(" .")
        if not clean_line:
            continue
        if section != "Оглавление" and _looks_like_toc_row(clean_line):
            continue
        norm_line = _normalize(clean_line)
        for norm_candidate in normalized_candidates:
            if _heading_matches(clean_line, norm_line, norm_candidate):
                return section

    for line in lines:
        clean_line = re.sub(r"\s+", " ", line).strip(" .")
        if not clean_line:
            continue
        if section != "Оглавление" and _looks_like_toc_row(clean_line):
            continue
        norm_line = _normalize(clean_line)
        if any(_contains_keyword(norm_line, candidate) for candidate in candidates):
            return section

    return section


def _clean_heading_for_display(line: str) -> str:
    clean = re.sub(r"\s+", " ", line).strip(" .")
    if not clean:
        return clean
    clean = re.sub(r"^(\d+(?:\.\d+)*\.?)(?=[А-ЯЁа-яёA-Za-z])", r"\1 ", clean)
    # DOCX extraction can glue TOC page numbers to headings: "1. ОБЩИЕ СВЕДЕНИЯ4".
    if re.match(r"^\d+(?:\.\d+)*\.?\s+", clean) or clean.isupper():
        clean = re.sub(r"(?<=[А-ЯЁа-яёA-Za-z)])\d{1,3}$", "", clean).strip()
        clean = re.sub(r"(?<=[А-ЯЁа-яёA-Za-z)])\s+\d{1,3}$", "", clean).strip()
    else:
        clean = re.sub(r"(?<=[А-ЯЁа-яёA-Za-z)])\s+\d{1,3}$", "", clean).strip()
    return clean


def _find_section_heading(lines: list[str], section: str, aliases: dict[str, list[str]]) -> str:
    candidates = [section]
    candidates.extend(aliases.get(section, []))
    normalized_candidates = [_normalize(candidate) for candidate in candidates]

    for line in lines:
        clean_line = _clean_heading_for_display(line)
        if not clean_line:
            continue
        norm_line = _normalize(clean_line)
        for norm_candidate in normalized_candidates:
            if _heading_matches(clean_line, norm_line, norm_candidate):
                return clean_line

    for line in lines:
        clean_line = _clean_heading_for_display(line)
        if not clean_line:
            continue
        norm_line = _normalize(clean_line)
        if any(_contains_keyword(norm_line, candidate) for candidate in candidates):
            return clean_line

    return section


def _looks_like_toc_row(raw_line: str) -> bool:
    compact = re.sub(r"\s+", "", raw_line)
    if bool(re.search(r"^\d+(?:\.\d+)*\.?.+\d{1,3}$", compact)):
        return True
    return bool(
        len(compact) <= 140
        and re.search(r"[а-яёa-z]", compact, flags=re.IGNORECASE)
        and re.search(r"\d{1,3}$", compact)
    )


def _heading_matches_section(line: str, section: str, aliases: dict[str, list[str]]) -> bool:
    norm_line = _normalize(line)
    if not norm_line:
        return False
    for candidate in [section] + aliases.get(section, []):
        if _heading_matches(line, norm_line, _normalize(candidate)):
            return True
    return False


def _split_document_body_and_toc(
    lines: list[str],
    required_sections: list[str],
    aliases: dict[str, list[str]],
) -> tuple[list[str], list[str]]:
    toc_start: int | None = None
    for idx, line in enumerate(lines):
        if _heading_matches_section(line, "Оглавление", aliases):
            toc_start = idx
            break

    if toc_start is None:
        return lines, []

    toc_lines = [lines[toc_start]]
    body_start = len(lines)
    for idx in range(toc_start + 1, len(lines)):
        line = lines[idx]
        if _looks_like_toc_row(line):
            toc_lines.append(line)
            continue
        if any(_heading_matches_section(line, section, aliases) for section in required_sections if section != "Оглавление"):
            body_start = idx
            break
        toc_lines.append(line)

    body_lines = lines[:toc_start] + lines[body_start:]
    return body_lines, toc_lines


def _section_found_in_lines(lines: list[str], section: str, aliases: dict[str, list[str]]) -> bool:
    normalized_text = _normalize("\n".join(lines))
    return _section_found(normalized_text, section, aliases)


def _line_matches_any_template_section(line: str, required_sections: list[str], aliases: dict[str, list[str]]) -> bool:
    return any(_heading_matches_section(line, section, aliases) for section in required_sections)


def _looks_like_heading(line: str) -> bool:
    clean = re.sub(r"\s+", " ", line).strip()
    if not clean:
        return False
    if len(clean) > 140:
        return False
    if clean.lower().startswith("таблица "):
        return False
    if re.match(r"^\d+(?:\.\d+)*\.?\s+\S", clean):
        return True

    letters = [char for char in clean if char.isalpha()]
    if not letters:
        return False
    upper_ratio = sum(1 for char in letters if char == char.upper()) / len(letters)
    if upper_ratio >= 0.8 and len(letters) >= 8:
        return True

    heading_markers = (
        "вопросы",
        "приложение",
        "ограничения",
        "допущения",
        "риски",
        "открытые",
    )
    normalized = _normalize(clean)
    return len(normalized.split()) <= 8 and any(marker in normalized for marker in heading_markers)


def _find_added_sections(
    body_lines: list[str],
    required_sections: list[str],
    aliases: dict[str, list[str]],
) -> list[str]:
    added: list[str] = []
    seen: set[str] = set()
    for line in body_lines:
        clean = re.sub(r"\s+", " ", line).strip(" .")
        if not clean or not _looks_like_heading(clean):
            continue
        if _line_matches_any_template_section(clean, required_sections, aliases):
            continue
        normalized = _normalize(clean)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        added.append(_clip(_clean_heading_for_display(clean), 120))
    return added


def _display_added_sections_with_toc_numbers(added_sections: list[str], toc_lines: list[str]) -> list[str]:
    prepared: list[str] = []
    for section in added_sections:
        normalized_section = _normalize(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", section))
        replacement = section
        for toc_line in toc_lines:
            clean_toc = _clean_heading_for_display(toc_line)
            normalized_toc = _normalize(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", clean_toc))
            if normalized_section and normalized_toc and (
                normalized_section in normalized_toc or normalized_toc in normalized_section
            ):
                replacement = clean_toc
                break
        prepared.append(replacement)
    return prepared


def _display_headings_with_toc_numbers(headings: list[str], toc_lines: list[str]) -> list[str]:
    prepared: list[str] = []
    for heading in headings:
        normalized_heading = _normalize(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading))
        replacement = heading
        for toc_line in toc_lines:
            clean_toc = _clean_heading_for_display(toc_line)
            if not re.match(r"^\d+(?:\.\d+)*\.?\s+", clean_toc):
                continue
            normalized_toc = _normalize(re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", clean_toc))
            if normalized_heading and normalized_toc and (
                normalized_heading in normalized_toc or normalized_toc in normalized_heading
            ):
                replacement = clean_toc
                break
        prepared.append(replacement)
    return prepared


def _section_content_blocks(
    lines: list[str],
    required_sections: list[str],
    aliases: dict[str, list[str]],
) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {section: [] for section in required_sections}
    current: str | None = None

    for line in lines:
        matched: str | None = None
        for section in required_sections:
            if _heading_matches_section(line, section, aliases):
                matched = section
                break
        if matched:
            current = matched
            continue
        if current:
            blocks[current].append(line)
    return blocks


def _empty_or_nearly_empty_sections(
    body_lines: list[str],
    required_sections: list[str],
    aliases: dict[str, list[str]],
) -> list[str]:
    blocks = _section_content_blocks(body_lines, required_sections, aliases)
    empty_sections: list[str] = []
    container_sections = {
        "Общие сведения",
        "Характеристика объекта автоматизации",
        "Требования к системе",
    }
    for section, block_lines in blocks.items():
        if section in {"Оглавление"} or section in container_sections:
            continue
        meaningful_lines = []
        for raw_line in block_lines:
            clean = re.sub(r"\s+", " ", raw_line).strip()
            if not clean:
                continue
            if clean.lower().startswith("таблица "):
                continue
            if _looks_like_heading(clean):
                continue
            meaningful_lines.append(clean)

        meaningful_text = _normalize("\n".join(meaningful_lines))
        if len(meaningful_text) < 25:
            empty_sections.append(section)
    return empty_sections


def _structure_template_assessment(
    required_sections: list[str],
    lines: list[str],
    aliases: dict[str, list[str]],
) -> tuple[float, str, list[str], dict[str, Any]]:
    body_lines, toc_lines = _split_document_body_and_toc(lines, required_sections, aliases)
    body_presence = {
        section: _section_found_in_lines(body_lines, section, aliases)
        for section in required_sections
        if section != "Оглавление"
    }
    toc_section_present = _section_found_in_lines(lines, "Оглавление", aliases)
    toc_presence = {
        section: _section_found_in_lines(toc_lines, section, aliases)
        for section in required_sections
        if section != "Оглавление"
    }

    found_sections = [
        section
        for section in required_sections
        if (toc_section_present if section == "Оглавление" else body_presence.get(section))
    ]
    missing_sections = [
        section
        for section in required_sections
        if not (toc_section_present if section == "Оглавление" else body_presence.get(section))
    ]
    evidence = [
        _find_section_evidence(lines if section == "Оглавление" else body_lines, section, aliases)
        for section in found_sections
    ]
    found_section_headings = [
        _find_section_heading(lines if section == "Оглавление" else body_lines, section, aliases)
        for section in found_sections
    ]
    found_section_headings_display = _display_headings_with_toc_numbers(found_section_headings, toc_lines)

    toc_comparable_sections = [
        section
        for section in required_sections
        if section
        not in {
            "Сведения о документе",
            "Термины, понятия и сокращения",
            "Оглавление",
        }
    ]

    body_not_in_toc = [
        section
        for section in toc_comparable_sections
        if body_presence.get(section) and toc_lines and not toc_presence.get(section)
    ]
    toc_not_in_body = [
        section
        for section in toc_comparable_sections
        if toc_presence.get(section) and not body_presence.get(section)
    ]
    pre_toc_sections_in_toc = [
        section
        for section in ["Сведения о документе", "Термины, понятия и сокращения"]
        if section in required_sections and body_presence.get(section) and toc_presence.get(section)
    ]
    added_sections = _find_added_sections(body_lines, required_sections, aliases)
    added_sections_display = _display_added_sections_with_toc_numbers(added_sections, toc_lines)
    empty_sections = [
        section for section in _empty_or_nearly_empty_sections(body_lines, required_sections, aliases)
        if section in found_sections
    ]

    critical_deviations: list[str] = []
    neutral_notes: list[str] = []
    if missing_sections:
        missing_text = "; ".join(f"«{section}»" for section in missing_sections)
        critical_deviations.append(f"отсутствуют обязательные разделы {missing_text}")
    if body_not_in_toc:
        body_not_in_toc_text = "; ".join(f"«{section}»" for section in body_not_in_toc)
        neutral_notes.append(f"разделы есть в теле документа, но не отражены в оглавлении: {body_not_in_toc_text}")
    if toc_not_in_body:
        toc_not_in_body_text = "; ".join(f"«{section}»" for section in toc_not_in_body)
        critical_deviations.append(f"разделы указаны в оглавлении, но не найдены в теле документа: {toc_not_in_body_text}")
    if pre_toc_sections_in_toc:
        pre_toc_text = "; ".join(f"«{section}»" for section in pre_toc_sections_in_toc)
        neutral_notes.append(
            "предшаблонные разделы расположены до оглавления и дополнительно указаны в оглавлении: "
            f"{pre_toc_text}"
        )
    if added_sections_display:
        added_text = "; ".join(f"«{section}»" for section in added_sections_display)
        neutral_notes.append(f"добавлены разделы вне шаблона: {added_text}")
    if empty_sections:
        empty_text = "; ".join(f"«{section}»" for section in empty_sections)
        neutral_notes.append(
            "разделы найдены, но выглядят пустыми или почти пустыми "
            f"(балл по критерию 1 не снижен): {empty_text}"
        )

    details = {
        "found_sections": found_sections,
        "found_section_headings": found_section_headings_display,
        "missing_sections": missing_sections,
        "body_not_in_toc": body_not_in_toc,
        "toc_not_in_body": toc_not_in_body,
        "pre_toc_sections_in_toc": pre_toc_sections_in_toc,
        "added_sections": added_sections_display,
        "empty_sections": empty_sections,
        "critical_deviations": critical_deviations,
        "neutral_notes": neutral_notes,
    }

    if missing_sections:
        missing_text = "; ".join(f"«{section}»" for section in missing_sections)
        found_text = "; ".join(f"«{item}»" for item in evidence) if evidence else "не выделены"
        critical_text = "; ".join(critical_deviations)
        neutral_text = "; ".join(neutral_notes) if neutral_notes else "не выявлены"
        rationale = (
            "Структура не соответствует шаблону: отсутствует обязательный раздел "
            f"{missing_text}. По критерию достаточно отсутствия хотя бы одного раздела, поэтому 0 баллов. "
            f"Найденные разделы: {found_text}. "
            f"Критичные отклонения, влияющие на балл: {critical_text}. "
            f"Особенности структуры, не влияющие на балл: {neutral_text}."
        )
        return 0.0, rationale, evidence, details

    evidence_text = "; ".join(f"«{item}»" for item in evidence)
    neutral_text = "; ".join(neutral_notes) if neutral_notes else "не выявлены"
    rationale = (
        "Структура соответствует шаблону: есть "
        f"{evidence_text}. Разделы шаблона не потеряны. "
        "Критичные отклонения, влияющие на балл: не выявлены. "
        f"Особенности структуры, не влияющие на балл: {neutral_text}."
    )
    return 100.0, rationale, evidence, details


def _heading_matches(line: str, norm_line: str, norm_section: str) -> bool:
    if not norm_line or not norm_section:
        return False

    # Headings are usually short; this avoids matching full requirement sentences.
    if len(norm_line) > 120:
        return False

    # Direct and prefix matches.
    if norm_line == norm_section or norm_line.startswith(norm_section):
        return True

    # Matches like "1.2. Цели создания системы" after removing numbering prefix.
    norm_wo_num = re.sub(r"^\d+(?:[ .]\d+)*[ .]?", "", norm_line).strip()
    if norm_wo_num == norm_section or norm_wo_num.startswith(norm_section):
        return True

    # Do not allow loose "contains" matches here, they produce many false section switches.
    return False


def _is_glossary_term_line(line: str) -> bool:
    normalized_line = _normalize(line)
    if not normalized_line:
        return False
    if normalized_line in {"термин понятие сокращение", "определение"}:
        return False
    if normalized_line.startswith("в данном разделе"):
        return False
    if len(normalized_line) > 40:
        return False
    if len(normalized_line.split()) > 4:
        return False

    letters = [char for char in line if char.isalpha()]
    upper_ratio = 0.0
    if letters:
        upper_ratio = sum(1 for char in letters if char == char.upper()) / len(letters)

    # Typical glossary term rows are short and often uppercase/acronym.
    return upper_ratio >= 0.55 or len(normalized_line.split()) == 1


def _section_map(lines: list[str], heading_candidates: list[str]) -> dict[str, list[str]]:
    section_blocks: dict[str, list[str]] = defaultdict(list)
    current = "UNMAPPED"

    normalized_sections = {section: _normalize(section) for section in heading_candidates}

    for line in lines:
        norm_line = _normalize(line)
        switched = False
        for section, norm_section in normalized_sections.items():
            if _heading_matches(line, norm_line, norm_section):
                current = section
                switched = True
                break
        if switched:
            continue
        section_blocks[current].append(line)
    return section_blocks


def _pick_section_lines(section_blocks: dict[str, list[str]], candidates: list[str]) -> list[str]:
    for candidate in candidates:
        lines = section_blocks.get(candidate, [])
        if lines:
            return lines

    normalized_candidates = [_normalize(item) for item in candidates]
    for section, lines in section_blocks.items():
        norm_section = _normalize(section)
        if any(
            norm_candidate
            and (norm_candidate in norm_section or norm_section in norm_candidate)
            for norm_candidate in normalized_candidates
        ):
            if lines:
                return lines
    return []


def analyze_rule_based(
    text: str,
    lines: list[str],
    profile: dict[str, Any],
    formatting_metadata: dict[str, Any] | None = None,
) -> AnalysisResult:
    rules_cfg = profile.get("rules", {})
    required_sections = profile.get("required_sections", [])
    criteria_cfg = profile.get("criteria", [])

    issues: list[Issue] = []
    risk_signals: list[RiskSignal] = []
    intern_tips: list[str] = []

    normalized_text = _normalize(text)
    section_aliases: dict[str, list[str]] = rules_cfg.get("section_aliases", {})
    section_presence: dict[str, bool] = {}
    missing_sections: list[str] = []
    for section in required_sections:
        found = _section_found(normalized_text, section, section_aliases)
        section_presence[section] = found
        if not found:
            missing_sections.append(section)

    for section in missing_sections:
        issues.append(
            Issue(
                code="RULE_STRUCTURE_001",
                category="structure",
                severity="high",
                message=f"Отсутствует обязательный раздел: {section}",
                evidence=section,
                rule_ref="RULE_STRUCTURE_001",
                hint_for_intern=f"Проверь, добавлен ли раздел '{section}' и раскрыт ли он по сути.",
            )
        )

    template_hint_patterns = rules_cfg.get("template_hint_patterns", [])
    template_hint_hits = 0
    template_hint_evidence: list[str] = []
    for pattern in template_hint_patterns:
        for match in _find_matching_lines(lines, pattern):
            template_hint_hits += 1
            clipped_match = _clip(match)
            template_hint_evidence.append(clipped_match)
            issues.append(
                Issue(
                    code="RULE_STRUCTURE_002",
                    category="structure",
                    severity="medium",
                    message="Похоже, в тексте остались служебные пояснения из шаблона.",
                    evidence=clipped_match,
                    rule_ref="RULE_STRUCTURE_002",
                    hint_for_intern="Проверь, удалены ли подсказки и примеры из шаблона.",
                )
            )

    vague_words = rules_cfg.get("vague_words", [])
    vague_hits = 0
    for word in vague_words:
        pattern = rf"\b{re.escape(word.lower())}\b"
        vague_hits += len(re.findall(pattern, normalized_text))
    if vague_hits:
        issues.append(
            Issue(
                code="RULE_QUALITY_001",
                category="quality",
                severity="medium",
                message="Есть расплывчатые и слабо проверяемые формулировки.",
                evidence=f"Найдено потенциально расплывчатых маркеров: {vague_hits}",
                rule_ref="RULE_QUALITY_001",
                hint_for_intern="Уточни формулировки, добавь проверяемые условия и критерии приемки.",
            )
        )

    assumptions_patterns = rules_cfg.get("assumption_patterns", [])
    assumptions_hits = 0
    for pattern in assumptions_patterns:
        assumptions_hits += len(_find_matching_lines(lines, pattern))
    if assumptions_hits:
        issues.append(
            Issue(
                code="RULE_FACT_001",
                category="factual",
                severity="medium",
                message="Есть признаки домыслов без явной опоры на кейс/интервью.",
                evidence=f"Фрагментов с маркерами предположений: {assumptions_hits}",
                rule_ref="RULE_FACT_001",
                hint_for_intern="Сверь спорные места с кейсом и интервью, убери домыслы.",
            )
        )

    punctuation_noise = len(re.findall(r"[!?.,]{2,}", text))
    language_error_hints = _detect_language_error_hints(lines)
    if punctuation_noise > 2:
        issues.append(
            Issue(
                code="RULE_STYLE_001",
                category="style",
                severity="low",
                message="Есть признаки проблем с пунктуацией.",
                evidence=f"Повторы знаков препинания: {punctuation_noise}",
                rule_ref="RULE_STYLE_001",
                hint_for_intern="Проверь орфографию и пунктуацию во всем документе.",
            )
        )

    ai_signal_patterns = rules_cfg.get("ai_signal_patterns", [])
    ai_marker_hits = 0
    for pattern in ai_signal_patterns:
        for match in _find_matching_lines(lines, pattern):
            ai_marker_hits += 1
            risk_signals.append(
                RiskSignal(
                    signal="Потенциальный ИИ-маркер",
                    evidence=_clip(match),
                    explanation="Фраза похожа на шаблонный оборот. Это риск-сигнал, а не доказательство.",
                )
            )

    ai_symbol_patterns = rules_cfg.get("ai_symbol_patterns", [])
    symbol_hits = 0
    for symbol in ai_symbol_patterns:
        if symbol:
            symbol_hits += text.count(symbol)
    if symbol_hits:
        risk_signals.append(
            RiskSignal(
                signal="Символы, характерные для ИИ-черновиков",
                evidence=f"Найдено специальных символов: {symbol_hits}",
                explanation="В техдокументах такие символы могут быть индикатором машинной генерации.",
            )
        )

    polite_words = rules_cfg.get("polite_words", [])
    polite_hits = 0
    for word in polite_words:
        polite_hits += len(re.findall(rf"\b{re.escape(word.lower())}\b", normalized_text))
    if polite_hits:
        risk_signals.append(
            RiskSignal(
                signal="Избыточно вежливый стиль",
                evidence=f"Найдено вежливых оборотов: {polite_hits}",
                explanation="Для технического документа это нетипичный стиль и может быть ИИ-сигналом.",
            )
        )

    em_dash_hits = text.count("—")

    heading_candidates = list(
        dict.fromkeys(
            required_sections
            + [
                "Сведения о документе",
                "Термины, понятия и сокращения",
                "Термины и сокращения",
                "Оглавление",
                "Полное наименование системы и ее условное обозначение",
                "Цели создания системы",
                "Цели",
                "Описание текущей ситуации",
                "Текущая ситуация",
                "Требования к данным",
                "Требования к визуализации",
                "Нефункциональные требования",
                "Требования к безопасности и разграничению доступа",
            ]
        )
    )
    section_blocks = _section_map(lines, heading_candidates)

    required_content_groups = rules_cfg.get("required_content_groups", {})
    covered_groups = 0
    missing_groups: list[str] = []
    for group_name, keyword_list in required_content_groups.items():
        if any(_normalize(keyword) in normalized_text for keyword in keyword_list):
            covered_groups += 1
        else:
            missing_groups.append(group_name)

    doc_info_lines = _pick_section_lines(section_blocks, ["Сведения о документе"])
    terms_lines = _pick_section_lines(
        section_blocks,
        ["Термины, понятия и сокращения", "Термины и сокращения", "Термины"],
    )
    toc_lines = _pick_section_lines(section_blocks, ["Оглавление"])
    naming_lines = _pick_section_lines(
        section_blocks,
        ["Полное наименование системы и ее условное обозначение"],
    )
    goals_lines = _pick_section_lines(section_blocks, ["Цели создания системы", "Цели"])
    current_lines = _pick_section_lines(section_blocks, ["Описание текущей ситуации", "Текущая ситуация"])
    data_lines = _pick_section_lines(section_blocks, ["Требования к данным"])
    visualization_lines = _pick_section_lines(section_blocks, ["Требования к визуализации"])
    nfr_lines = _pick_section_lines(section_blocks, ["Нефункциональные требования"])
    security_lines = _pick_section_lines(
        section_blocks,
        ["Требования к безопасности и разграничению доступа"],
    )

    doc_info_text = "\n".join(doc_info_lines)
    terms_text = "\n".join(terms_lines)
    toc_text = "\n".join(toc_lines)
    naming_text = "\n".join(naming_lines)
    goals_text = "\n".join(goals_lines)
    current_text = "\n".join(current_lines)
    data_text = "\n".join(data_lines)
    visualization_text = "\n".join(visualization_lines)
    nfr_text = "\n".join(nfr_lines)
    security_text = "\n".join(security_lines)

    document_versioning_score = 0.0
    has_version = bool(re.search(r"\b\d+\.\d+\.\d+\b", doc_info_text))
    has_date = bool(re.search(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b", doc_info_text))
    has_fio = bool(
        re.search(r"\b[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+\b", doc_info_text)
        or re.search(r"\b[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+\b", doc_info_text)
        or re.search(r"\b[А-ЯЁ][а-яё]+ [А-ЯЁ]\.\s*[А-ЯЁ]\.", doc_info_text)
        or re.search(r"\bфио\b", _normalize(doc_info_text))
    )
    if has_version and has_date and has_fio:
        document_versioning_score = 100.0
    else:
        issues.append(
            Issue(
                code="RULE_DOCINFO_001",
                category="structure",
                severity="medium",
                message="Раздел «Сведения о документе» заполнен не полностью.",
                evidence=_clip(doc_info_text) if doc_info_text else "Раздел не найден или пустой.",
                rule_ref="4.1",
                hint_for_intern="Проверь наличие версии, даты, ФИО и описания изменений.",
            )
        )

    terms_glossary_score = 0.0
    term_names: list[str] = []
    for raw_line in terms_lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        first_part = raw_line.split("—")[0].split("-")[0].split(":")[0].strip()
        if _is_glossary_term_line(first_part):
            term_names.append(_normalize(first_part))
    alpha_order_ok = True
    if len(term_names) >= 2:
        alpha_order_ok = term_names == sorted(term_names)

    glossary_required_markers = [
        "mvp",
        "csv",
        "дашборд",
        "логин",
        "пароль",
        "администратор",
        "transaction",
        "stock",
        "product",
        "category",
        "brand",
        "аппг",
        "drill down",
        "drill-down",
        "категорийный менеджер",
        "отдел закупок",
        "выручка",
        "остатки",
        "скидка",
    ]
    terms_norm = _normalize(terms_text)
    used_but_missing_terms = [
        marker
        for marker in glossary_required_markers
        if _contains_keyword(normalized_text, marker) and not _contains_keyword(terms_norm, marker)
    ]

    if terms_text.strip() and len(term_names) >= 2 and alpha_order_ok and len(used_but_missing_terms) <= 1:
        terms_glossary_score = 100.0
    else:
        issues.append(
            Issue(
                code="RULE_TERMS_001",
                category="structure",
                severity="medium",
                message="Раздел «Термины и сокращения» заполнен с нарушениями.",
                evidence=(
                    f"Не раскрыты используемые термины: {', '.join(used_but_missing_terms[:8])}."
                    if used_but_missing_terms
                    else (_clip(terms_text) if terms_text else "Раздел не найден или пустой.")
                ),
                rule_ref="4.2",
                hint_for_intern="Добавь используемые термины/сокращения и отсортируй список.",
            )
        )

    table_of_contents_score = 0.0
    toc_row_hits = 0
    _, extracted_toc_lines = _split_document_body_and_toc(lines, required_sections, section_aliases)
    toc_lines_for_check = extracted_toc_lines if len(extracted_toc_lines) > len(toc_lines) else toc_lines
    for toc_line in toc_lines:
        if (
            re.search(r"\.{2,}\s*\d{1,3}\s*$", toc_line)
            or re.search(r"\s\d{1,3}\s*$", toc_line)
            or re.search(r"[А-Яа-яA-Za-z)\]]\d{1,3}\s*$", toc_line)
        ):
            toc_row_hits += 1
    has_toc_heading = "оглавление" in normalized_text or "содержание" in normalized_text
    toc_row_hits_global = 0
    for raw_line in lines:
        norm_line = _normalize(raw_line)
        if re.search(r"^\d+(?:\.\d+)*\.?[а-яa-z ]+\d{1,3}$", norm_line):
            toc_row_hits_global += 1

    toc_page_numbers: list[str] = []
    for toc_line in toc_lines_for_check:
        match = re.search(r"(?<!\d)(\d{1,3})\s*$", toc_line.strip())
        if match and re.search(r"[А-Яа-яA-Za-z]", toc_line):
            toc_page_numbers.append(match.group(1))
    repeated_page_problem = False
    if len(toc_page_numbers) >= 4:
        max_same_page = max(toc_page_numbers.count(page) for page in set(toc_page_numbers))
        repeated_page_problem = max_same_page >= 4 and max_same_page / len(toc_page_numbers) >= 0.6
    open_questions_in_body = "открытые вопросы" in normalized_text or "вопросы для уточнения" in normalized_text
    open_questions_in_toc = "открытые вопросы" in _normalize("\n".join(toc_lines_for_check))

    if has_toc_heading and max(toc_row_hits, toc_row_hits_global) >= 3 and not repeated_page_problem and not (open_questions_in_body and not open_questions_in_toc):
        table_of_contents_score = 100.0
    else:
        toc_reason = "Раздел не найден или пустой."
        if repeated_page_problem:
            toc_reason = "В оглавлении много разделов с одним и тем же номером страницы."
        elif open_questions_in_body and not open_questions_in_toc:
            toc_reason = "В документе есть раздел открытых вопросов, но он не отражен в оглавлении."
        elif toc_text:
            toc_reason = _clip(toc_text)
        issues.append(
            Issue(
                code="RULE_TOC_001",
                category="structure",
                severity="medium",
                message="Оглавление отсутствует или заполнено некорректно.",
                evidence=toc_reason,
                rule_ref="4.3",
                hint_for_intern="Проверь, что в оглавлении есть разделы и номера страниц.",
            )
        )

    system_naming_score = 0.0
    naming_norm = _normalize(naming_text)
    has_full_name = "полное наименование" in naming_norm or len(naming_norm) >= 20
    has_conditional_name = any(
        marker in naming_norm for marker in ["условн", "обознач", "сокращ", "аббрев", "кратк"]
    )
    company_as_short_name = bool(
        re.search(r"кратк\w*\s+наимен\w*\s+систем\w*\s+ао\s+стройторг\b", naming_norm)
        or re.search(r"кратк\w*\s+наимен\w*\s+систем\w*\s+ооо\s+стройторг\b", naming_norm)
    )
    if has_full_name and has_conditional_name and not company_as_short_name:
        system_naming_score = 100.0
    else:
        issues.append(
            Issue(
                code="RULE_SYSTEMNAME_001",
                category="content",
                severity="medium",
                message="Нет полного или условного наименования системы.",
                evidence=(
                    "Краткое наименование выглядит как название организации, а не условное обозначение системы."
                    if company_as_short_name
                    else (_clip(naming_text) if naming_text else "Раздел не найден или пустой.")
                ),
                rule_ref="4.4",
                hint_for_intern="Укажи полное наименование системы и ее условное обозначение.",
            )
        )

    goals_norm = _normalize(goals_text)
    business_markers = rules_cfg.get(
        "business_goal_markers",
        ["увелич", "сниз", "рост", "сократ", "повыс", "бизнес", "прибыл", "выруч"],
    )
    has_business_goal = any(marker in goals_norm for marker in business_markers)
    has_automation_word = bool(re.search(r"\b(автоматизац|внедрен)\w*", goals_norm))
    has_allowed_automation_phrase = bool(re.search(r"за счет (автоматизац|внедрен)", goals_norm))
    automation_as_goal = has_automation_word and not has_allowed_automation_phrase and not has_business_goal

    business_goals_score = 100.0 if has_business_goal and not automation_as_goal else 0.0
    if business_goals_score == 0:
        issues.append(
            Issue(
                code="RULE_GOALS_001",
                category="content",
                severity="high",
                message="Цели не выглядят как бизнес-цели или не соответствуют критериям.",
                evidence=_clip(goals_text) if goals_text else "Раздел не найден или пустой.",
                rule_ref="4.5",
                hint_for_intern="Сформулируй цели через измеримый бизнес-результат, а не через внедрение решения.",
            )
        )

    current_norm = _normalize(current_text)
    role_markers = ["роль", "пользоват", "менеджер", "аналитик", "директор", "закуп", "категорийн"]
    action_markers = ["формирует", "проверяет", "сводит", "выгружает", "делает", "согласует", "анализирует"]
    time_markers = ["ежеднев", "еженед", "в день", "в месяц", "период", "срок", "час", "минут"]
    result_markers = ["результат", "отчет", "ошиб", "решени", "вывод", "метрик", "показател"]

    role_hit = any(marker in current_norm for marker in role_markers)
    action_hit = any(marker in current_norm for marker in action_markers)
    time_hit = any(marker in current_norm for marker in time_markers)
    result_hit = any(marker in current_norm for marker in result_markers)
    process_coverage = sum(int(item) for item in [role_hit, action_hit, time_hit, result_hit])
    bpmn_hit = bool(re.search(r"\bbpmn\b|swimlane|дорожк|->|→", current_norm))

    process_model_hit = bpmn_hit or "рисунок" in current_norm or "схема" in current_norm
    if process_coverage >= 3 and process_model_hit:
        current_situation_score = 100.0
    elif process_coverage >= 3:
        current_situation_score = 50.0
    else:
        current_situation_score = 0.0

    if current_situation_score == 0:
        issues.append(
            Issue(
                code="RULE_ASIS_001",
                category="content",
                severity="high",
                message="Описание текущей ситуации неполное или не подтверждено контекстом.",
                evidence=_clip(current_text) if current_text else "Раздел не найден или пустой.",
                rule_ref="4.6",
                hint_for_intern="Добавь роли, действия, временные параметры и результаты текущего процесса.",
            )
        )

    data_requirement_keywords = rules_cfg.get("data_requirement_keywords", [])
    visualization_keywords = rules_cfg.get(
        "visualization_keywords",
        rules_cfg.get("data_visualization_keywords", []),
    )

    data_requirement_hits = sum(
        1 for keyword in data_requirement_keywords if _normalize(keyword) in _normalize(data_text)
    )
    visualization_hits = sum(
        1 for keyword in visualization_keywords if _normalize(keyword) in _normalize(visualization_text)
    )

    data_requirement_coverage = 0.0
    if data_requirement_keywords:
        data_requirement_coverage = 100.0 * data_requirement_hits / len(data_requirement_keywords)

    visualization_coverage = 0.0
    if visualization_keywords:
        visualization_coverage = 100.0 * visualization_hits / len(visualization_keywords)

    data_norm = _normalize(data_text)
    data_source_file_hits = sum(
        1 for marker in ["brand", "category", "product", "stock", "transaction"] if _contains_keyword(data_norm, marker)
    )
    data_has_source_set = data_source_file_hits >= 4 and _has_any(data_norm, ["csv", "файл", "выгруз", "источник"])
    data_has_update_deadline = _has_any(data_norm, ["07 00", "07:00", "08 00", "08:00", "8 утра", "не позднее 8"])
    data_has_calculation_rules = (
        _has_any(data_norm, ["алгоритм", "формул", "расчет", "агрегац"])
        and _has_any(data_norm, ["выруч", "продаж"])
        and _has_any(data_norm, ["остат", "stock"])
    )
    data_has_discount_context = _has_any(data_norm, ["скид", "полная цена", "price full", "price_full", "глубин"])
    data_has_blocking_uncertainty = _has_any(
        data_norm,
        [
            "конкретные системы источники не названы",
            "необходимо определить",
            "подлежат уточнению",
            "на этапе обследования",
        ],
    )
    data_has_manual_customer_update = bool(re.search(r"\bобновля\w+\s+вручн\w+\s+заказчик", data_norm))
    data_quality_groups = sum(
        int(item)
        for item in [
            data_has_source_set,
            data_has_update_deadline,
            data_has_calculation_rules,
            data_has_discount_context,
        ]
    )

    data_requirements_score = (
        100.0
        if data_quality_groups == 4 and not data_has_blocking_uncertainty and not data_has_manual_customer_update
        else 0.0
    )
    if data_requirements_score == 0:
        issues.append(
            Issue(
                code="RULE_DATA_001",
                category="content",
                severity="medium",
                message="Требования к данным описаны недостаточно полно.",
                evidence=(
                    "Не хватает обязательных блоков: источники-файлы, срок обновления, алгоритмы/агрегации, скидки/полная цена."
                    if data_text
                    else "Раздел не найден или пустой."
                ),
                rule_ref="4.7",
                hint_for_intern="Уточни источники, показатели, обновление и правила агрегации/детализации.",
            )
        )

    vis_text_norm = _normalize(visualization_text)
    strong_visual_hits = sum(
        1
        for marker in ["топ 5", "топ5", "дашборд", "фильтр", "drill down", "дрилл"]
        if marker in vis_text_norm
    )
    visualization_quality_groups = _count_groups(
        vis_text_norm,
        [
            ["дашборд", "визуал", "график", "диаграм"],
            ["выруч", "продаж", "динамик"],
            ["топ 5", "топ5", "top 5", "лучших", "худших"],
            ["фильтр", "период", "бренд", "категор", "магазин", "точк"],
            ["остат"],
            ["скид", "полная цена", "price full", "price_full", "глубин"],
            ["детал", "drill", "разрез"],
        ],
    )
    visualization_has_unbacked_factor_block = _has_any(
        vis_text_norm,
        ["сезонность", "маркетинговый эффект", "цена материалов", "другое"],
    )
    visualization_requirements_score = (
        100.0 if visualization_quality_groups >= 6 and not visualization_has_unbacked_factor_block else 0.0
    )
    if visualization_requirements_score == 0:
        issues.append(
            Issue(
                code="RULE_VIS_001",
                category="content",
                severity="medium",
                message="Требования к визуализации не покрывают критерии качества.",
                evidence=(
                    "Не хватает состава дашборда: продажи/выручка, остатки, скидки/полная цена, топ-5, фильтры и детализация."
                    if visualization_text
                    else "Раздел не найден или пустой."
                ),
                rule_ref="4.8",
                hint_for_intern="Добавь сценарии визуализации, фильтры, разрезы и связь с данными.",
            )
        )

    section_expectations = rules_cfg.get("section_expectations", {})
    nfr_expected_keywords = section_expectations.get(
        "Нефункциональные требования",
        ["время", "надежность", "доступность"],
    )
    nfr_norm = _normalize(nfr_text)
    nfr_hits = sum(1 for keyword in nfr_expected_keywords if _contains_keyword(nfr_norm, keyword))

    functional_markers = rules_cfg.get(
        "functional_markers",
        ["кнопк", "форма", "экран", "пользователь может", "отчет должен показывать"],
    )
    functional_hits = sum(1 for marker in functional_markers if _contains_keyword(nfr_norm, marker))

    nfr_has_response_time = _has_any(nfr_norm, ["10 секунд", "время загрузки", "время открытия", "время отклика"])
    nfr_has_reliability_or_accessibility = _has_any(
        nfr_norm,
        ["доступность", "отказоустойчив", "резерв", "восстанов", "мониторинг", "подлежат уточнению"],
    )
    nfr_mixes_security = _has_any(
        nfr_norm,
        ["разграничение доступа", "ограничение доступа", "аутентификац", "роли", "круг лиц"],
    )
    nfr_has_unconfirmed_usability = _has_any(
        nfr_norm,
        ["3 клика", "2 3 действия", "автоматически сохранять", "одновременную работу пользователей"],
    )

    non_functional_requirements_score = (
        100.0
        if nfr_has_response_time
        and functional_hits == 0
        and not nfr_mixes_security
        and not nfr_has_unconfirmed_usability
        else 0.0
    )
    if non_functional_requirements_score == 0:
        issues.append(
            Issue(
                code="RULE_NFR_001",
                category="content",
                severity="medium",
                message="Нефункциональные требования не соответствуют критериям.",
                evidence=(
                    "НФТ должны содержать проверяемое время отклика и доступность/отказоустойчивость, без смешения с функциями и доступами."
                    if nfr_text
                    else "Раздел не найден или пустой."
                ),
                rule_ref="4.9",
                hint_for_intern="Проверь, что НФТ описывают качество системы и не дублируют функциональные требования.",
            )
        )

    security_markers = rules_cfg.get("required_content_groups", {}).get(
        "доступы",
        ["доступ", "роль", "права", "разграничение", "безопасность"],
    )
    security_norm = _normalize(security_text)
    security_hits = sum(1 for marker in security_markers if _contains_keyword(security_norm, marker))
    has_role_marker = bool(re.search(r"\bрол\w*\b", security_norm)) or _has_any(
        security_norm,
        ["категорийный менеджер", "отдел закупок", "руковод", "генеральный директор", "группы пользователей"],
    )
    has_core_roles = _has_any(security_norm, ["категорийный менеджер"]) and _has_any(security_norm, ["отдел закупок"])
    has_management_role = _has_any(security_norm, ["руковод", "генеральный директор"])
    has_permission_detail = _has_any(security_norm, ["просмотр", "доступ к данным", "отчет", "дашборд", "выгруз"])
    has_unsupported_admin = _has_any(
        security_norm,
        ["администратор системы", "управления доступом", "контроля загрузки", "обновления данных в системе"],
    )
    has_unconfirmed_editing = _has_any(security_norm, ["редактирован"])
    security_access_score = (
        100.0
        if security_hits >= 2
        and has_role_marker
        and has_core_roles
        and has_management_role
        and has_permission_detail
        and not has_unsupported_admin
        and not has_unconfirmed_editing
        else 0.0
    )
    if security_access_score == 0:
        issues.append(
            Issue(
                code="RULE_SECURITY_001",
                category="content",
                severity="medium",
                message="Раздел безопасности и разграничения доступа заполнен не полностью.",
                evidence=(
                    "Нужны роли категорийного менеджера, закупок и руководства с проверяемыми правами; неподтвержденные роли/права снижают критерий."
                    if security_text
                    else "Раздел не найден или пустой."
                ),
                rule_ref="4.10",
                hint_for_intern="Укажи роли пользователей и права доступа к данным/отчетам.",
            )
        )

    (
        structure_template_score,
        structure_template_rationale,
        structure_template_evidence,
        structure_template_details,
    ) = _structure_template_assessment(
        required_sections=required_sections,
        lines=lines,
        aliases=section_aliases,
    )
    formatting_instruction_facts = _formatting_checklist_facts(
        formatting_metadata=formatting_metadata,
        template_hint_evidence=template_hint_evidence,
    )
    formatting_decision_hint = formatting_instruction_facts.get("decision_hint", {})
    if not isinstance(formatting_decision_hint, dict):
        formatting_decision_hint = {}
    formatting_systemic_rule_ids = {
        str(item)
        for item in formatting_decision_hint.get("systemic_rule_ids", [])
        if str(item).strip()
    }
    strong_formatting_failure_rules = {
        "list_font_and_markers",
        "toc_format",
        "heading_numbering",
        "page_numbering_footer",
        "no_template_explanations",
    }
    metadata_summary = formatting_instruction_facts.get("metadata_summary", {})
    if not isinstance(metadata_summary, dict):
        metadata_summary = {}
    font_checked_runs = int(metadata_summary.get("font_checked_text_runs", 0) or 0)
    non_verdana_count = int(metadata_summary.get("non_verdana_count", 0) or 0)
    font_ratio = non_verdana_count / font_checked_runs if font_checked_runs else 0.0
    title_placeholder_left = any("наименование проекта системы" in _normalize(line) for line in lines[:30])
    font_is_dominant_failure = "font_verdana" in formatting_systemic_rule_ids and font_ratio >= 0.5
    drawing_count = int(metadata_summary.get("drawing_count", 0) or 0)
    figure_caption_count = int(metadata_summary.get("figure_caption_count", 0) or 0)
    figures_without_any_captions = (
        "figure_numbering_caption_and_references" in formatting_systemic_rule_ids
        and drawing_count >= 3
        and figure_caption_count == 0
    )
    formatting_has_strong_failure = (
        bool(formatting_systemic_rule_ids & strong_formatting_failure_rules)
        or font_is_dominant_failure
        or figures_without_any_captions
    )
    formatting_instruction_score = (
        100.0 if template_hint_hits == 0 and not title_placeholder_left and not formatting_has_strong_failure else 0.0
    )
    language_error_count = len(language_error_hints) + punctuation_noise
    literacy_punctuation_score = 100.0 if language_error_count < 3 else 0.0

    ai_phrase_over_limit = ai_marker_hits > 2
    em_dash_over_limit = em_dash_hits > max(5, len(lines) // 8)
    symbol_over_limit = symbol_hits > 20
    independent_work_score = (
        100.0 if not ai_phrase_over_limit and not symbol_over_limit and not em_dash_over_limit and polite_hits == 0 else 0.0
    )
    if independent_work_score == 0:
        issues.append(
            Issue(
                code="RULE_AI_001",
                category="style",
                severity="medium",
                message="Есть признаки, что текст может быть подготовлен с заметным влиянием ИИ.",
                evidence=f"ИИ-маркеры: {ai_marker_hits}, символы: {symbol_hits}, длинное тире: {em_dash_hits}",
                rule_ref="4.11",
                hint_for_intern="Сделай стиль более предметным и убери шаблонные обобщения.",
            )
        )

    score_map = {
        "structure_template": structure_template_score,
        "formatting_instruction": formatting_instruction_score,
        "literacy_punctuation": literacy_punctuation_score,
        "document_versioning": document_versioning_score,
        "terms_glossary": terms_glossary_score,
        "table_of_contents": table_of_contents_score,
        "system_naming": system_naming_score,
        "business_goals": business_goals_score,
        "current_situation": current_situation_score,
        "data_requirements": data_requirements_score,
        "visualization_requirements": visualization_requirements_score,
        "non_functional_requirements": non_functional_requirements_score,
        "security_access": security_access_score,
        "independent_work": independent_work_score,
    }

    criteria: list[CriterionScore] = []
    for criterion in criteria_cfg:
        criterion_id = criterion["id"]
        if criterion_id == "structure_template":
            rationale = structure_template_rationale
            evidence = structure_template_evidence
        elif criterion_id == "formatting_instruction":
            decision_hint = formatting_instruction_facts.get("decision_hint", {})
            if not isinstance(decision_hint, dict):
                decision_hint = {}
            estimated_errors = int(decision_hint.get("estimated_error_count", 0) or 0)
            if template_hint_hits:
                rationale = (
                    "Оформление не соответствует инструкции: в документе остались служебные пояснения "
                    f"или примеры из шаблона ({template_hint_hits}). По критерию достаточно одного "
                    "такого пояснения, чтобы поставить 0 баллов."
                )
            else:
                rationale = (
                    "По текстовым rule-based признакам служебные пояснения и примеры из шаблона не найдены. "
                    "Полная проверка оформления выполняется отдельным LLM-проходом по чеклисту инструкции "
                    f"и DOCX-метаданным; предварительная оценка по чеклисту: {estimated_errors} потенциальных "
                    "ошибок оформления."
                )
            evidence = template_hint_evidence
        elif criterion_id == "literacy_punctuation":
            rationale = f"Найдено {language_error_count} языковых/редакторских ошибок."
            evidence = language_error_hints
        else:
            rationale = f"Rule-based оценка по критерию '{criterion['title']}'."
            evidence = []
        criteria.append(
            CriterionScore(
                criterion_id=criterion_id,
                title=criterion["title"],
                weight=int(criterion["weight"]),
                score=round(score_map.get(criterion_id, 0.0), 2),
                rationale=rationale,
                evidence=evidence,
            )
        )

    if missing_sections:
        intern_tips.append("Проверь структуру ФТТ: все обязательные разделы из шаблона должны присутствовать.")
    if template_hint_hits:
        intern_tips.append("Удали из документа служебные подсказки и примеры из шаблона.")
    if document_versioning_score < 100:
        intern_tips.append("Уточни раздел «Сведения о документе»: версия, дата, ФИО, описание изменений.")
    if terms_glossary_score < 100:
        intern_tips.append("Проверь раздел терминов: полнота списка и сортировку.")
    if table_of_contents_score < 100:
        intern_tips.append("Сверь оглавление с фактической структурой документа и номерами страниц.")
    if business_goals_score < 100:
        intern_tips.append("Сформулируй цели как бизнес-результат, а не как внедрение системы.")
    if current_situation_score < 100:
        intern_tips.append("Доработай описание текущей ситуации: роли, действия, периодичность и результаты.")
    if data_requirements_score < 100:
        intern_tips.append("Уточни требования к данным: источники, формулы, обновление, агрегации и детализацию.")
    if visualization_requirements_score < 100:
        intern_tips.append("Уточни требования к визуализации и их связь с показателями из блока данных.")
    if non_functional_requirements_score < 100:
        intern_tips.append("Проверь, что нефункциональные требования не содержат функциональных сценариев.")
    if security_access_score < 100:
        intern_tips.append("Добавь явную матрицу ролей и прав доступа к данным и отчетам.")
    if independent_work_score < 100:
        intern_tips.append("Снизь количество шаблонных обобщений и стилистических ИИ-маркеров.")
    if not intern_tips:
        intern_tips.append("Работа выглядит цельной. Проверь только мелкие формулировки и единообразие стиля.")

    coverage_summary = {
        "missing_sections": missing_sections,
        "missing_content_groups": missing_groups,
        "section_presence": section_presence,
        "data_requirements_coverage": round(data_requirement_coverage, 2),
        "visualization_coverage": round(visualization_coverage, 2),
        "ai_marker_hits": ai_marker_hits,
        "symbol_hits": symbol_hits,
        "polite_hits": polite_hits,
        "language_error_count": language_error_count,
        "language_errors": language_error_hints,
        "structure_template": structure_template_details,
        "formatting_instruction": {
            "template_hint_hits": template_hint_hits,
            "template_hint_evidence": template_hint_evidence,
            "checklist_facts": formatting_instruction_facts,
        },
        "visualization_requirements": {
            "coverage": round(visualization_coverage, 2),
            "strong_visual_hits": strong_visual_hits,
            "quality_groups": visualization_quality_groups,
            "has_unbacked_factor_block": visualization_has_unbacked_factor_block,
            "section_present": bool(visualization_text.strip()),
        },
        "non_functional_requirements": {
            "expected_keyword_hits": nfr_hits,
            "functional_hits": functional_hits,
            "has_response_time": nfr_has_response_time,
            "has_reliability_or_accessibility": nfr_has_reliability_or_accessibility,
            "mixes_security": nfr_mixes_security,
            "has_unconfirmed_usability": nfr_has_unconfirmed_usability,
            "section_present": bool(nfr_text.strip()),
        },
    }

    return AnalysisResult(
        criteria=criteria,
        mentor_issues=issues,
        intern_tips=intern_tips,
        ai_risk_signals=risk_signals,
        coverage_summary=coverage_summary,
    )
