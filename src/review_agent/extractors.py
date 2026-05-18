from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


class ExtractionError(Exception):
    pass


@dataclass
class ExtractedSubmission:
    text: str
    lines: list[str]
    extension: str
    formatting_metadata: dict[str, Any] = field(default_factory=dict)


def _clean_lines(lines: list[str]) -> list[str]:
    cleaned = []
    for line in lines:
        prepared = re.sub(r"\s+", " ", line).strip()
        if prepared:
            cleaned.append(prepared)
    return cleaned


_DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DRAWING_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def _w_attr(node: ET.Element | None, name: str) -> str:
    if node is None:
        return ""
    return str(node.attrib.get(f"{_W_NS}{name}", "")).strip()


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join((node.text or "") for node in paragraph.findall(".//w:t", _DOCX_NS))


def _clip_metadata_text(text: str, limit: int = 180) -> str:
    prepared = re.sub(r"\s+", " ", text).strip()
    if len(prepared) <= limit:
        return prepared
    return prepared[: limit - 1].rstrip() + "…"


def _has_visual_hinting(paragraph: ET.Element) -> bool:
    if paragraph.find(".//w:shd", _DOCX_NS) is not None:
        return True
    highlight = paragraph.find(".//w:highlight", _DOCX_NS)
    if highlight is not None and _w_attr(highlight, "val"):
        return True
    color = paragraph.find(".//w:color", _DOCX_NS)
    if color is not None and _w_attr(color, "val").lower() in {"808080", "a6a6a6", "7f7f7f"}:
        return True
    return False


def _looks_like_template_hint(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    if not normalized:
        return False
    patterns = [
        r"\bпример заполнения\b",
        r"\bзаполните раздел\b",
        r"\bподсказка\b",
        r"\bудалите этот текст\b",
        r"\bукажите\b.{0,80}\b(раздел|таблиц|документ|требован)",
        r"\bдолжно быть указано\b",
        r"\[пример\]",
        r"<пример>",
    ]
    return any(re.search(pattern, normalized) for pattern in patterns)


def _looks_like_toc_line(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return False
    if re.search(r"\.{2,}\s*\d{1,3}$", normalized):
        return True
    if re.search(r"^\d+(?:\.\d+)*\.?\s+.{3,}\s+\d{1,3}$", normalized):
        return True
    # Word TOC text can be extracted without separators: "1.ОБЩИЕ СВЕДЕНИЯ4".
    return bool(
        re.search(
            r"^\d+(?:\.\d+)*\.?\s*"
            r"(?:[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z ,()/-]{3,})"
            r"\d{1,3}$",
            normalized,
        )
    )


def _looks_like_heading(text: str, style_id: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized or len(normalized) > 140:
        return False
    style_norm = style_id.lower()
    if "heading" in style_norm or "заголов" in style_norm:
        return True
    letters = [char for char in normalized if char.isalpha()]
    upper_ratio = sum(1 for char in letters if char == char.upper()) / len(letters) if letters else 0
    if upper_ratio >= 0.75 and len(normalized.split()) <= 8:
        return True
    return bool(re.search(r"^\d+(?:\.\d+)*\.?\s+\S", normalized))


def _theme_font_values(theme_root: ET.Element | None) -> dict[str, str]:
    if theme_root is None:
        return {}

    result: dict[str, str] = {}
    mapping = {
        "majorFont": (("majorHAnsi", "latin"), ("majorEastAsia", "ea"), ("majorBidi", "cs")),
        "minorFont": (("minorHAnsi", "latin"), ("minorEastAsia", "ea"), ("minorBidi", "cs")),
    }
    for font_node_name, targets in mapping.items():
        font_node = theme_root.find(f".//a:fontScheme/a:{font_node_name}", _DRAWING_NS)
        if font_node is None:
            continue
        for theme_key, child_name in targets:
            child = font_node.find(f"a:{child_name}", _DRAWING_NS)
            if child is None:
                continue
            typeface = str(child.get("typeface", "")).strip()
            if typeface:
                result[theme_key] = typeface
    return result


def _font_values(r_fonts: ET.Element | None, theme_fonts: dict[str, str] | None = None) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    theme_fonts = theme_fonts or {}
    # Theme attributes override inherited style values in Word. For these Russian submissions,
    # visible Cyrillic/Latin text uses HAnsi/ASCII. Ignore EastAsia and `cs`: those buckets
    # produce false positives when Word/R7 shows Verdana for the selected Russian text.
    attr_order = (
        ("hAnsiTheme", True),
        ("asciiTheme", True),
        ("hAnsi", False),
        ("ascii", False),
    )
    for attr, is_theme in attr_order:
        raw_value = _w_attr(r_fonts, attr)
        value = theme_fonts.get(raw_value, "") if is_theme else raw_value
        if not value or value.startswith("+"):
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _extract_style_fonts(
    styles_root: ET.Element | None,
    theme_fonts: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, list[str]]]:
    if styles_root is None:
        return [], {}

    default_fonts = _font_values(
        styles_root.find(".//w:docDefaults/w:rPrDefault/w:rPr/w:rFonts", _DOCX_NS),
        theme_fonts,
    )
    style_fonts: dict[str, list[str]] = {}
    for style in styles_root.findall(".//w:style", _DOCX_NS):
        style_id = _w_attr(style, "styleId")
        if not style_id:
            continue
        fonts = _font_values(style.find("w:rPr/w:rFonts", _DOCX_NS), theme_fonts)
        if fonts:
            style_fonts[style_id] = fonts
    return default_fonts, style_fonts


def _font_is_verdana(font_name: str) -> bool:
    return font_name.strip().lower() == "verdana"


def _bool_prop(node: ET.Element | None) -> bool:
    if node is None:
        return False
    value = _w_attr(node, "val").lower()
    return value not in {"0", "false", "off", "none"}


def _half_points(value: str) -> float | None:
    try:
        return int(value) / 2
    except Exception:
        return None


def _twips(value: str) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _run_formatting(
    run: ET.Element,
    style_id: str,
    style_fonts: dict[str, list[str]],
    default_fonts: list[str],
    theme_fonts: dict[str, str] | None = None,
) -> dict[str, Any]:
    r_pr = run.find("w:rPr", _DOCX_NS)
    fonts = _font_values(r_pr.find("w:rFonts", _DOCX_NS) if r_pr is not None else None, theme_fonts)
    font_source = "run"
    if not fonts and style_id:
        fonts = style_fonts.get(style_id, [])
        font_source = "paragraph_style"
    if not fonts:
        fonts = default_fonts
        font_source = "document_default"

    size = _half_points(_w_attr(r_pr.find("w:sz", _DOCX_NS), "val") if r_pr is not None else "")
    bold = _bool_prop(r_pr.find("w:b", _DOCX_NS)) if r_pr is not None else False
    return {
        "fonts": fonts,
        "font_source": font_source,
        "size_pt": size,
        "bold": bold,
    }


def _spacing_fact(p_pr: ET.Element | None) -> dict[str, Any]:
    spacing = p_pr.find("w:spacing", _DOCX_NS) if p_pr is not None else None
    return {
        "before_twips": _twips(_w_attr(spacing, "before")),
        "after_twips": _twips(_w_attr(spacing, "after")),
        "line": _twips(_w_attr(spacing, "line")),
        "line_rule": _w_attr(spacing, "lineRule"),
    }


def _indent_fact(p_pr: ET.Element | None) -> dict[str, Any]:
    indent = p_pr.find("w:ind", _DOCX_NS) if p_pr is not None else None
    return {
        "left_twips": _twips(_w_attr(indent, "left")),
        "first_line_twips": _twips(_w_attr(indent, "firstLine")),
        "hanging_twips": _twips(_w_attr(indent, "hanging")),
    }


def _paragraph_format_details(
    paragraph: ET.Element,
    style_fonts: dict[str, list[str]],
    default_fonts: list[str],
    theme_fonts: dict[str, str] | None = None,
) -> dict[str, Any]:
    p_pr = paragraph.find("w:pPr", _DOCX_NS)
    p_style = p_pr.find("w:pStyle", _DOCX_NS) if p_pr is not None else None
    style_id = _w_attr(p_style, "val")
    alignment = _w_attr(p_pr.find("w:jc", _DOCX_NS), "val") if p_pr is not None else ""
    spacing = _spacing_fact(p_pr)
    indent = _indent_fact(p_pr)

    run_count = 0
    bold_runs = 0
    run_fonts: list[str] = []
    run_sizes: list[float] = []
    for run in paragraph.findall("w:r", _DOCX_NS):
        run_text = "".join((node.text or "") for node in run.findall(".//w:t", _DOCX_NS))
        if not re.sub(r"\s+", " ", run_text).strip():
            continue
        run_count += 1
        formatting = _run_formatting(run, style_id, style_fonts, default_fonts, theme_fonts)
        run_fonts.extend(formatting["fonts"])
        size_pt = formatting["size_pt"]
        if size_pt is not None:
            run_sizes.append(size_pt)
        if formatting["bold"]:
            bold_runs += 1

    return {
        "style_id": style_id,
        "alignment": alignment,
        "spacing": spacing,
        "indent": indent,
        "run_count": run_count,
        "bold_run_count": bold_runs,
        "fonts": sorted(set(run_fonts), key=str.lower),
        "sizes_pt": sorted(set(run_sizes)),
        "all_bold": bool(run_count and bold_runs == run_count),
    }


def _paragraph_role(text: str, style_id: str, is_numbered: bool) -> str:
    prepared = re.sub(r"\s+", " ", text).strip()
    low = prepared.lower()
    if _looks_like_template_hint(prepared):
        return "template_hint"
    if re.match(r"^таблица\s+\d+\s*[-–]", low):
        return "table_caption"
    if re.match(r"^продолжение таблицы\s+\d+", low):
        return "table_continuation_caption"
    if re.match(r"^рисунок\s+\d+\s*[-–]", low):
        return "figure_caption"
    if _looks_like_toc_line(prepared):
        return "toc_line"
    if _looks_like_heading(prepared, style_id):
        return "heading"
    if is_numbered or prepared.startswith("-"):
        return "list_item"
    return "body"


def _extract_numbering_map(numbering_root: ET.Element | None) -> dict[tuple[str, str], dict[str, str]]:
    if numbering_root is None:
        return {}

    abstract_levels: dict[tuple[str, str], dict[str, str]] = {}
    for abstract in numbering_root.findall("w:abstractNum", _DOCX_NS):
        abstract_id = _w_attr(abstract, "abstractNumId")
        if not abstract_id:
            continue
        for level in abstract.findall("w:lvl", _DOCX_NS):
            ilvl = _w_attr(level, "ilvl") or "0"
            num_fmt = _w_attr(level.find("w:numFmt", _DOCX_NS), "val")
            lvl_text = _w_attr(level.find("w:lvlText", _DOCX_NS), "val")
            abstract_levels[(abstract_id, ilvl)] = {
                "num_fmt": num_fmt,
                "lvl_text": lvl_text,
            }

    numbering_map: dict[tuple[str, str], dict[str, str]] = {}
    for num in numbering_root.findall("w:num", _DOCX_NS):
        num_id = _w_attr(num, "numId")
        abstract_ref = num.find("w:abstractNumId", _DOCX_NS)
        abstract_id = _w_attr(abstract_ref, "val")
        if not num_id or not abstract_id:
            continue
        for (candidate_abstract_id, ilvl), info in abstract_levels.items():
            if candidate_abstract_id == abstract_id:
                numbering_map[(num_id, ilvl)] = dict(info)
    return numbering_map


def _numbering_marker_info(
    numbering_map: dict[tuple[str, str], dict[str, str]],
    num_id: str,
    ilvl: str,
) -> dict[str, str]:
    if not num_id:
        return {}
    return numbering_map.get((num_id, ilvl or "0")) or numbering_map.get((num_id, "0")) or {}


def _is_front_matter_body_paragraph(
    *,
    text: str,
    paragraph_index: int,
    run_sizes: list[float],
    alignment: str,
) -> bool:
    if paragraph_index > 12:
        return False

    prepared = re.sub(r"\s+", " ", text).strip()
    low = prepared.lower()
    if not prepared:
        return False

    looks_like_title = any(
        marker in low
        for marker in (
            "функционально-технические требования",
            "функциональные технические требования",
            "технические требования",
        )
    )
    has_title_size = bool(run_sizes and max(run_sizes) >= 14)
    is_centered = alignment in {"center", "both"}
    return looks_like_title or has_title_size or (is_centered and paragraph_index <= 6)


def _extract_docx_formatting_metadata(
    root: ET.Element,
    styles_root: ET.Element | None = None,
    footer_roots: list[ET.Element] | None = None,
    theme_root: ET.Element | None = None,
    numbering_root: ET.Element | None = None,
) -> dict[str, Any]:
    theme_fonts = _theme_font_values(theme_root)
    default_fonts, style_fonts = _extract_style_fonts(styles_root, theme_fonts)
    numbering_map = _extract_numbering_map(numbering_root)
    paragraphs: list[dict[str, Any]] = []
    template_hint_candidates: list[dict[str, Any]] = []
    heading_candidates: list[dict[str, Any]] = []
    toc_candidates: list[str] = []
    numbered_samples: list[str] = []
    block_sequence: list[dict[str, Any]] = []
    style_counts: dict[str, int] = {}
    font_counts: dict[str, int] = {}
    font_source_counts: dict[str, int] = {}
    font_total_text_runs = 0
    font_checked_text_runs = 0
    font_unknown_text_runs = 0
    non_verdana_samples: list[dict[str, str]] = []
    non_verdana_samples_by_font: dict[str, list[dict[str, str]]] = {}
    size_counts: dict[str, int] = {}
    non_body_size_samples: list[dict[str, str]] = []
    body_spacing_issues: list[dict[str, str]] = []
    body_indent_issues: list[dict[str, str]] = []
    heading_format_samples: list[dict[str, Any]] = []
    list_format_samples: list[dict[str, Any]] = []
    table_caption_format_samples: list[dict[str, Any]] = []
    figure_caption_format_samples: list[dict[str, Any]] = []
    shaded_or_highlighted_count = 0
    body_child_index = 0
    table_paragraph_ids = {
        id(paragraph)
        for table in root.findall(".//w:tbl", _DOCX_NS)
        for paragraph in table.findall(".//w:p", _DOCX_NS)
    }

    for child in root.findall("w:body/*", _DOCX_NS):
        body_child_index += 1
        if child.tag == f"{_W_NS}tbl":
            block_sequence.append({"type": "table", "index": body_child_index})

    for idx, paragraph in enumerate(root.findall(".//w:p", _DOCX_NS), start=1):
        text = _paragraph_text(paragraph)
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            continue

        p_pr = paragraph.find("w:pPr", _DOCX_NS)
        p_style = p_pr.find("w:pStyle", _DOCX_NS) if p_pr is not None else None
        style_id = _w_attr(p_style, "val")
        if style_id:
            style_counts[style_id] = style_counts.get(style_id, 0) + 1

        num_pr = p_pr.find("w:numPr", _DOCX_NS) if p_pr is not None else None
        ilvl = _w_attr(num_pr.find("w:ilvl", _DOCX_NS), "val") if num_pr is not None else ""
        num_id = _w_attr(num_pr.find("w:numId", _DOCX_NS), "val") if num_pr is not None else ""
        marker_info = _numbering_marker_info(numbering_map, num_id, ilvl)
        num_fmt = marker_info.get("num_fmt", "")
        lvl_text = marker_info.get("lvl_text", "")
        is_numbered = bool(num_id or re.search(r"^\d+(?:\.\d+)*\.?\s+\S", cleaned))
        has_visual_hinting = _has_visual_hinting(paragraph)
        if has_visual_hinting:
            shaded_or_highlighted_count += 1
        spacing = _spacing_fact(p_pr)
        indent = _indent_fact(p_pr)
        alignment = _w_attr(p_pr.find("w:jc", _DOCX_NS), "val") if p_pr is not None else ""
        is_table_paragraph = id(paragraph) in table_paragraph_ids
        role = "table_cell" if is_table_paragraph else _paragraph_role(cleaned, style_id, is_numbered)

        paragraph_fact = {
            "index": idx,
            "text": _clip_metadata_text(cleaned),
            "style_id": style_id,
            "numbering_level": ilvl,
            "numbering_id": num_id,
            "numbering_format": num_fmt,
            "numbering_text": lvl_text,
            "is_numbered": is_numbered,
            "has_visual_hinting": has_visual_hinting,
            "role": role,
            "alignment": alignment,
            "spacing": spacing,
            "indent": indent,
        }
        paragraphs.append(paragraph_fact)
        block_sequence.append({"type": "paragraph", "index": idx, "role": role, "text": _clip_metadata_text(cleaned)})

        run_count = 0
        bold_runs = 0
        run_fonts: list[str] = []
        run_sizes: list[float] = []
        for run in paragraph.findall("w:r", _DOCX_NS):
            run_text = "".join((node.text or "") for node in run.findall(".//w:t", _DOCX_NS))
            run_text_clean = re.sub(r"\s+", " ", run_text).strip()
            if not run_text_clean:
                continue
            font_total_text_runs += 1
            run_count += 1
            formatting = _run_formatting(run, style_id, style_fonts, default_fonts, theme_fonts)
            fonts = formatting["fonts"]
            font_source = formatting["font_source"]
            size_pt = formatting["size_pt"]
            is_bold = bool(formatting["bold"])
            if is_bold:
                bold_runs += 1
            if size_pt is not None:
                run_sizes.append(size_pt)
                size_key = str(int(size_pt)) if float(size_pt).is_integer() else str(size_pt)
                size_counts[size_key] = size_counts.get(size_key, 0) + 1

            if not fonts:
                font_unknown_text_runs += 1
                continue

            font_name = fonts[0]
            run_fonts.append(font_name)
            font_checked_text_runs += 1
            font_counts[font_name] = font_counts.get(font_name, 0) + 1
            font_source_counts[font_source] = font_source_counts.get(font_source, 0) + 1
            if not _font_is_verdana(font_name):
                sample = {
                    "paragraph_index": str(idx),
                    "font": font_name,
                    "source": font_source,
                    "text": _clip_metadata_text(run_text_clean, 140),
                }
                if len(non_verdana_samples) < 20:
                    non_verdana_samples.append(sample)
                font_samples = non_verdana_samples_by_font.setdefault(font_name, [])
                if len(font_samples) < 4:
                    font_samples.append(sample)

        paragraph_fact["run_count"] = run_count
        paragraph_fact["bold_run_count"] = bold_runs
        paragraph_fact["fonts"] = sorted(set(run_fonts), key=str.lower)
        paragraph_fact["sizes_pt"] = sorted(set(run_sizes))
        paragraph_fact["all_bold"] = bool(run_count and bold_runs == run_count)
        paragraph_fact["all_caps"] = cleaned == cleaned.upper() and any(char.isalpha() for char in cleaned)
        if role == "body" and _is_front_matter_body_paragraph(
            text=cleaned,
            paragraph_index=idx,
            run_sizes=run_sizes,
            alignment=alignment,
        ):
            role = "front_matter"
            paragraph_fact["role"] = role
            block_sequence[-1]["role"] = role

        if role == "body":
            if run_sizes and any(abs(size - 9) > 0.1 for size in run_sizes) and len(non_body_size_samples) < 20:
                non_body_size_samples.append(
                    {
                        "size": ", ".join(str(size) for size in sorted(set(run_sizes))),
                        "text": _clip_metadata_text(cleaned, 140),
                    }
                )
            if spacing.get("before_twips") not in {None, 0} and len(body_spacing_issues) < 20:
                body_spacing_issues.append(
                    {
                        "before_twips": str(spacing.get("before_twips")),
                        "text": _clip_metadata_text(cleaned, 140),
                    }
                )
            if any(indent.get(key) not in {None, 0} for key in ("left_twips", "first_line_twips", "hanging_twips")):
                if len(body_indent_issues) < 20:
                    body_indent_issues.append(
                        {
                            "indent": str(indent),
                            "text": _clip_metadata_text(cleaned, 140),
                        }
                    )

        if role == "heading" and len(heading_format_samples) < 25:
            heading_format_samples.append(
                {
                    "text": _clip_metadata_text(cleaned, 140),
                    "style_id": style_id,
                    "fonts": sorted(set(run_fonts), key=str.lower),
                    "sizes_pt": sorted(set(run_sizes)),
                    "all_bold": bool(run_count and bold_runs == run_count),
                    "all_caps": paragraph_fact["all_caps"],
                    "spacing": spacing,
                    "indent": indent,
                    "numbering_level": ilvl,
                }
            )
        if role == "list_item" and len(list_format_samples) < 25:
            list_format_samples.append(
                {
                    "text": _clip_metadata_text(cleaned, 140),
                    "starts_with_hyphen": cleaned.startswith("-") or lvl_text in {"-", "–", "—"},
                    "starts_with_arabic_dot": bool(re.match(r"^\d+\.", cleaned)),
                    "numbering_format": num_fmt,
                    "numbering_text": lvl_text,
                    "fonts": sorted(set(run_fonts), key=str.lower),
                    "sizes_pt": sorted(set(run_sizes)),
                    "spacing": spacing,
                    "indent": indent,
                    "numbering_level": ilvl,
                }
            )
        if role == "table_caption" and len(table_caption_format_samples) < 20:
            table_caption_format_samples.append(
                {
                    "text": _clip_metadata_text(cleaned, 140),
                    "fonts": sorted(set(run_fonts), key=str.lower),
                    "sizes_pt": sorted(set(run_sizes)),
                    "all_bold": bool(run_count and bold_runs == run_count),
                    "alignment": alignment,
                    "spacing": spacing,
                    "indent": indent,
                }
            )
        if role == "figure_caption" and len(figure_caption_format_samples) < 20:
            figure_caption_format_samples.append(
                {
                    "text": _clip_metadata_text(cleaned, 140),
                    "fonts": sorted(set(run_fonts), key=str.lower),
                    "sizes_pt": sorted(set(run_sizes)),
                    "all_bold": bool(run_count and bold_runs == run_count),
                    "alignment": alignment,
                    "spacing": spacing,
                    "indent": indent,
                }
            )

        if _looks_like_template_hint(cleaned):
            template_hint_candidates.append(paragraph_fact)
        if _looks_like_heading(cleaned, style_id):
            heading_candidates.append(paragraph_fact)
        if _looks_like_toc_line(cleaned):
            toc_candidates.append(_clip_metadata_text(cleaned))
        if is_numbered and len(numbered_samples) < 12:
            numbered_samples.append(_clip_metadata_text(cleaned))

    tables: list[dict[str, Any]] = []
    table_cell_format_samples: list[dict[str, Any]] = []
    table_border_samples: list[dict[str, Any]] = []
    for idx, table in enumerate(root.findall(".//w:tbl", _DOCX_NS), start=1):
        rows = table.findall("w:tr", _DOCX_NS)
        row_facts: list[list[str]] = []
        tbl_pr = table.find("w:tblPr", _DOCX_NS)
        tbl_borders = tbl_pr.find("w:tblBorders", _DOCX_NS) if tbl_pr is not None else None
        table_border_samples.append(
            {
                "index": idx,
                "has_borders": tbl_borders is not None and len(list(tbl_borders)) > 0,
            }
        )
        for row_idx, row in enumerate(rows[:4], start=1):
            cells = row.findall("w:tc", _DOCX_NS)
            row_facts.append([
                _clip_metadata_text(" ".join(_paragraph_text(p) for p in cell.findall(".//w:p", _DOCX_NS)), 90)
                for cell in cells[:5]
            ])
            for cell_idx, cell in enumerate(cells[:5], start=1):
                tc_pr = cell.find("w:tcPr", _DOCX_NS)
                tc_mar = tc_pr.find("w:tcMar", _DOCX_NS) if tc_pr is not None else None
                margin_values = []
                if tc_mar is not None:
                    margin_values = [
                        _twips(_w_attr(node, "w"))
                        for node in list(tc_mar)
                        if _w_attr(node, "w")
                    ]
                for paragraph in cell.findall(".//w:p", _DOCX_NS)[:2]:
                    text = _paragraph_text(paragraph)
                    cleaned_cell = re.sub(r"\s+", " ", text).strip()
                    if not cleaned_cell:
                        continue
                    details = _paragraph_format_details(paragraph, style_fonts, default_fonts, theme_fonts)
                    table_cell_format_samples.append(
                        {
                            "table_index": idx,
                            "row_index": row_idx,
                            "cell_index": cell_idx,
                            "is_header": row_idx == 1,
                            "text": _clip_metadata_text(cleaned_cell, 120),
                            "cell_margins_twips": [value for value in margin_values if value is not None],
                            **details,
                        }
                    )
                    if len(table_cell_format_samples) >= 40:
                        break
                if len(table_cell_format_samples) >= 40:
                    break
            if len(table_cell_format_samples) >= 40:
                break
        texts = [cell for row in row_facts for cell in row if cell]
        tables.append(
            {
                "index": idx,
                "row_count": len(rows),
                "sample_rows": row_facts,
                "sample_text": _clip_metadata_text(" | ".join(texts), 260),
            }
        )

    all_text = "\n".join(item["text"] for item in paragraphs)
    table_captions = [item["text"] for item in paragraphs if item.get("role") in {"table_caption", "table_continuation_caption"}]
    figure_captions = [item["text"] for item in paragraphs if item.get("role") == "figure_caption"]
    table_refs = re.findall(r"см\.\s*таблица\s+\d+|таблица\s+\d+", all_text, flags=re.IGNORECASE)
    figure_refs = re.findall(r"см\.\s*рисунок\s+\d+|рисунок\s+\d+", all_text, flags=re.IGNORECASE)
    drawing_count = len(root.findall(".//w:drawing", _DOCX_NS)) + len(root.findall(".//w:pict", _DOCX_NS))
    footer_texts: list[str] = []
    page_field_count = 0
    numpages_field_count = 0
    footer_font_counts: dict[str, int] = {}
    footer_size_counts: dict[str, int] = {}
    footer_bold_runs = 0
    footer_text_runs = 0
    for footer_root in footer_roots or []:
        footer_text = "\n".join(
            _clip_metadata_text(_paragraph_text(paragraph), 160)
            for paragraph in footer_root.findall(".//w:p", _DOCX_NS)
            if _paragraph_text(paragraph).strip()
        )
        if footer_text.strip():
            footer_texts.append(footer_text)
        for instr in footer_root.findall(".//w:instrText", _DOCX_NS):
            instr_text = (instr.text or "").upper()
            if "NUMPAGES" in instr_text:
                numpages_field_count += 1
            elif "PAGE" in instr_text:
                page_field_count += 1
        for run in footer_root.findall(".//w:r", _DOCX_NS):
            run_text = "".join((node.text or "") for node in run.findall(".//w:t", _DOCX_NS)).strip()
            if not run_text:
                continue
            footer_text_runs += 1
            formatting = _run_formatting(run, "", style_fonts, default_fonts, theme_fonts)
            fonts = formatting["fonts"]
            if fonts:
                footer_font_counts[fonts[0]] = footer_font_counts.get(fonts[0], 0) + 1
            size_pt = formatting["size_pt"]
            if size_pt is not None:
                size_key = str(int(size_pt)) if float(size_pt).is_integer() else str(size_pt)
                footer_size_counts[size_key] = footer_size_counts.get(size_key, 0) + 1
            if formatting["bold"]:
                footer_bold_runs += 1

    return {
        "format": "docx",
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "tables": tables[:12],
        "style_counts": style_counts,
        "heading_style_count": sum(
            count
            for style, count in style_counts.items()
            if "heading" in style.lower() or "заголов" in style.lower()
        ),
        "heading_candidates": heading_candidates[:20],
        "paragraph_format_summary": {
            "size_counts": size_counts,
            "non_body_size_samples": non_body_size_samples,
            "body_spacing_before_issues": body_spacing_issues,
            "body_indent_issues": body_indent_issues,
            "heading_format_samples": heading_format_samples,
            "list_format_samples": list_format_samples,
            "table_caption_format_samples": table_caption_format_samples,
            "figure_caption_format_samples": figure_caption_format_samples,
            "table_cell_format_samples": table_cell_format_samples,
            "table_border_samples": table_border_samples,
        },
        "font_summary": {
            "expected_font": "Verdana",
            "total_text_runs": font_total_text_runs,
            "checked_text_runs": font_checked_text_runs,
            "unknown_text_runs": font_unknown_text_runs,
            "font_counts": font_counts,
            "font_source_counts": font_source_counts,
            "non_verdana_count": sum(
                count for font, count in font_counts.items() if not _font_is_verdana(font)
            ),
            "non_verdana_samples": non_verdana_samples,
            "non_verdana_samples_by_font": non_verdana_samples_by_font,
            "default_fonts": default_fonts,
            "theme_fonts": theme_fonts,
        },
        "numbered_paragraph_count": sum(1 for item in paragraphs if item["is_numbered"]),
        "numbered_samples": numbered_samples,
        "toc_like_line_count": len(toc_candidates),
        "toc_like_samples": toc_candidates[:12],
        "template_hint_candidates": template_hint_candidates[:20],
        "template_hint_candidate_count": len(template_hint_candidates),
        "shaded_or_highlighted_count": shaded_or_highlighted_count,
        "table_reference_summary": {
            "table_caption_count": len(table_captions),
            "table_captions": table_captions[:20],
            "table_reference_count": len(table_refs),
            "table_references": table_refs[:20],
        },
        "figure_summary": {
            "drawing_count": drawing_count,
            "figure_caption_count": len(figure_captions),
            "figure_captions": figure_captions[:20],
            "figure_reference_count": len(figure_refs),
            "figure_references": figure_refs[:20],
        },
        "footer_summary": {
            "footer_count": len(footer_roots or []),
            "footer_texts": footer_texts[:6],
            "page_field_count": page_field_count,
            "numpages_field_count": numpages_field_count,
            "font_counts": footer_font_counts,
            "size_counts": footer_size_counts,
            "bold_text_runs": footer_bold_runs,
            "text_runs": footer_text_runs,
        },
        "block_sequence": block_sequence[:80],
    }


def _extract_docx(path: Path) -> ExtractedSubmission:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
            styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in archive.namelist() else b""
            theme_xml = archive.read("word/theme/theme1.xml") if "word/theme/theme1.xml" in archive.namelist() else b""
            numbering_xml = archive.read("word/numbering.xml") if "word/numbering.xml" in archive.namelist() else b""
            footer_xmls = [
                archive.read(name)
                for name in archive.namelist()
                if re.match(r"word/footer\d+\.xml$", name)
            ]
    except Exception as exc:
        raise ExtractionError(f"Не удалось прочитать DOCX: {exc}") from exc

    root = ET.fromstring(xml)
    styles_root = ET.fromstring(styles_xml.strip()) if styles_xml else None
    theme_root = ET.fromstring(theme_xml.strip()) if theme_xml else None
    numbering_root = ET.fromstring(numbering_xml.strip()) if numbering_xml else None
    footer_roots = [ET.fromstring(raw.strip()) for raw in footer_xmls]

    lines: list[str] = []
    for paragraph in root.findall(".//w:p", _DOCX_NS):
        text = _paragraph_text(paragraph)
        if text.strip():
            lines.append(text)

    cleaned = _clean_lines(lines)
    return ExtractedSubmission(
        text="\n".join(cleaned),
        lines=cleaned,
        extension=".docx",
        formatting_metadata=_extract_docx_formatting_metadata(root, styles_root, footer_roots, theme_root, numbering_root),
    )


def _extract_pdf(path: Path) -> ExtractedSubmission:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise ExtractionError(
            "Для чтения PDF нужен пакет pypdf. Установите зависимости: pip install -r requirements.txt"
        ) from exc

    try:
        reader = PdfReader(str(path))
        lines: list[str] = []
        for page in reader.pages:
            page_text = page.extract_text() or ""
            lines.extend(page_text.splitlines())
    except Exception as exc:
        raise ExtractionError(f"Не удалось прочитать PDF: {exc}") from exc

    cleaned = _clean_lines(lines)
    return ExtractedSubmission(
        text="\n".join(cleaned),
        lines=cleaned,
        extension=".pdf",
        formatting_metadata={"format": "pdf"},
    )


def extract_submission(path: str) -> ExtractedSubmission:
    file_path = Path(path)
    if not file_path.exists():
        raise ExtractionError(f"Файл не найден: {path}")

    ext = file_path.suffix.lower()
    if ext == ".docx":
        return _extract_docx(file_path)
    if ext == ".pdf":
        return _extract_pdf(file_path)

    raise ExtractionError("Поддерживаются только форматы DOCX и PDF.")
