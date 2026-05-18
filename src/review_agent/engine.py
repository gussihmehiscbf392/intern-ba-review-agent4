from __future__ import annotations

import re
import os
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from .config import load_calibration_examples, load_profile
from .extractors import ExtractionError, extract_submission
from .llm import run_llm_assessment
from .models import CriterionScore, Issue
from .rules import analyze_rule_based


def _normalize_llm_score_to_percent(raw_score: Any, weight: int) -> float:
    try:
        score = float(raw_score)
    except Exception:
        return 0.0

    # LLM scale: 0/1 for weight=1 and 0/1/2 for weight=2.
    if weight == 1 and score in {0.0, 1.0}:
        return 100.0 if score == 1.0 else 0.0
    if weight == 2 and score in {0.0, 1.0, 2.0}:
        if score == 2.0:
            return 100.0
        if score == 1.0:
            return 50.0
        return 0.0

    return 0.0


def _normalize_language_check(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("language_check")
    if not isinstance(raw, dict):
        return {}

    error_count = raw.get("error_count", 0)
    try:
        normalized_count = max(0, int(error_count))
    except Exception:
        normalized_count = 0

    normalized_errors: list[dict[str, str]] = []
    for item in raw.get("errors", []):
        if not isinstance(item, dict):
            continue
        fragment = str(item.get("fragment", "")).strip()
        error_type = str(item.get("error_type", "")).strip().lower()
        comment = str(item.get("comment", "")).strip()
        if not fragment:
            continue
        if error_type not in {"spelling", "punctuation", "grammar", "typo", "editing"}:
            error_type = "editing"
        normalized_errors.append(
            {
                "fragment": fragment,
                "error_type": error_type,
                "comment": comment,
            }
        )

    return {
        "error_count": normalized_count,
        "errors": normalized_errors,
    }


def _dedupe_fragments(*groups: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for raw in group:
            fragment = str(raw).strip()
            if not fragment:
                continue
            key = re.sub(r"\s+", " ", fragment.split(" — ", 1)[0].lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(fragment)
    return result


def _is_weak_language_fragment(fragment: str) -> bool:
    normalized = re.sub(r"\s+", " ", fragment).strip().lower()
    weak_patterns = [
        r"^в таблица \d+\.?$",
        r"^в \d{1,2}:\d{2}\b",
        r"^требованияавтоматизац",
    ]
    return any(re.search(pattern, normalized) for pattern in weak_patterns)


def _language_fragments(errors: list[Any]) -> list[str]:
    fragments: list[str] = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        fragment = str(err.get("fragment", "")).strip()
        if not fragment or _is_weak_language_fragment(fragment):
            continue
        comment = str(err.get("comment", "")).strip()
        fragments.append(f"{fragment} — {comment}" if comment else fragment)
    return fragments


def _structure_template_assist_note(payload: dict[str, Any]) -> str:
    assist = payload.get("structure_template_assist", {})
    if not isinstance(assist, dict):
        return ""

    pieces: list[str] = []
    summary = str(assist.get("summary", "")).strip()
    if summary:
        pieces.append("Свободное резюме LLM: " + summary.rstrip("."))

    mappings = assist.get("section_mappings", [])
    if isinstance(mappings, list):
        notable: list[str] = []
        for item in mappings:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "")).strip()
            if status not in {"possible_synonym", "missing", "duplicate", "unclear"}:
                continue
            section = str(item.get("template_section", "")).strip()
            heading = str(item.get("found_heading", "")).strip()
            comment = str(item.get("comment", "")).strip()
            if not section:
                continue
            if heading:
                text = f"{section}: возможно «{heading}»"
            else:
                text = f"{section}: {status}"
            if comment:
                text += f" ({comment})"
            notable.append(text)
            if len(notable) >= 4:
                break
        if notable:
            pieces.append("Спорные места: " + "; ".join(notable))

    notes = assist.get("manual_review_notes", [])
    if isinstance(notes, list):
        prepared_notes = [str(note).strip().rstrip(".") for note in notes if str(note).strip()]
        if prepared_notes:
            pieces.append("Для ручной проверки: " + "; ".join(prepared_notes[:3]))

    added_sections = assist.get("added_sections", [])
    if isinstance(added_sections, list):
        prepared_added = [str(section).strip().rstrip(".") for section in added_sections if str(section).strip()]
        if prepared_added:
            pieces.append("Добавленные разделы: " + "; ".join(prepared_added[:5]))

    if not pieces:
        return ""
    return "LLM-подсказка для наставника (на балл не влияет): " + ". ".join(pieces) + "."


def _merge_scores(
    rule_scores: list[CriterionScore],
    llm_result: dict[str, Any],
) -> list[CriterionScore]:
    if llm_result.get("status") != "ok":
        return rule_scores

    llm_payload = llm_result.get("payload", {})
    llm_score_mode = os.getenv("LLM_SCORE_MODE", "hybrid").strip().lower()
    use_direct_llm_scores = llm_score_mode == "direct"
    use_hybrid_llm_scores = llm_score_mode == "hybrid"
    language_check = _normalize_language_check(llm_payload)
    llm_scores_raw = {
        item.get("criterion_id"): item.get("score", 0)
        for item in llm_payload.get("criterion_scores", [])
        if item.get("criterion_id")
    }
    llm_rationales = {
        item.get("criterion_id"): item.get("rationale", "")
        for item in llm_payload.get("criterion_scores", [])
        if item.get("criterion_id")
    }
    llm_evidence = {
        item.get("criterion_id"): item.get("evidence", [])
        for item in llm_payload.get("criterion_scores", [])
        if item.get("criterion_id")
    }

    merged: list[CriterionScore] = []
    for item in rule_scores:
        if item.criterion_id == "structure_template":
            assist_note = _structure_template_assist_note(llm_payload)
            rationale = f"{item.rationale}\n{assist_note}" if assist_note else item.rationale
            merged.append(
                CriterionScore(
                    criterion_id=item.criterion_id,
                    title=item.title,
                    weight=item.weight,
                    score=item.score,
                    rationale=rationale,
                    evidence=item.evidence,
                )
            )
        elif item.criterion_id == "literacy_punctuation" and language_check and (
            use_direct_llm_scores or use_hybrid_llm_scores
        ):
            language_errors = language_check.get("errors", [])
            llm_fragments = _language_fragments(language_errors)
            combined_fragments = _dedupe_fragments(item.evidence, llm_fragments)
            if use_hybrid_llm_scores and item.score >= 100.0 and len(item.evidence) < 2:
                merged.append(item)
                continue
            errors_count = max(
                int(language_check.get("error_count", 0)),
                len(combined_fragments),
            )
            merged_score = 0.0 if errors_count >= 3 else 100.0
            merged.append(
                CriterionScore(
                    criterion_id=item.criterion_id,
                    title=item.title,
                    weight=item.weight,
                    score=merged_score,
                    rationale=f"Найдено {errors_count} языковых/редакторских ошибок.",
                    evidence=combined_fragments,
                )
            )
        elif item.criterion_id == "formatting_instruction" and item.criterion_id in llm_scores_raw and (
            use_direct_llm_scores or use_hybrid_llm_scores
        ):
            llm_score = _normalize_llm_score_to_percent(llm_scores_raw[item.criterion_id], item.weight)
            merged.append(
                CriterionScore(
                    criterion_id=item.criterion_id,
                    title=item.title,
                    weight=item.weight,
                    score=round(llm_score, 2),
                    rationale=llm_rationales.get(item.criterion_id) or item.rationale,
                    evidence=llm_evidence.get(item.criterion_id, []),
                )
            )
        elif not use_direct_llm_scores:
            merged.append(item)
        elif item.criterion_id in llm_scores_raw:
            llm_score = _normalize_llm_score_to_percent(llm_scores_raw[item.criterion_id], item.weight)
            if item.criterion_id == "literacy_punctuation":
                merged_score = llm_score
                merged_rationale = llm_rationales.get(item.criterion_id) or item.rationale
                merged_evidence = _dedupe_fragments(item.evidence, llm_evidence.get(item.criterion_id, []))
            else:
                # Keep the visible score aligned with the explanation/evidence that will be shown
                # to the mentor. Averaging 0/1 criteria as percentages can round a failed LLM
                # criterion back to 1 point while keeping a negative rationale.
                merged_score = llm_score
                merged_rationale = llm_rationales.get(item.criterion_id) or item.rationale
                merged_evidence = llm_evidence.get(item.criterion_id, [])
            merged.append(
                CriterionScore(
                    criterion_id=item.criterion_id,
                    title=item.title,
                    weight=item.weight,
                    score=round(merged_score, 2),
                    rationale=merged_rationale,
                    evidence=merged_evidence,
                )
            )
        else:
            merged.append(item)
    return merged


def _weighted_overall(criteria: list[CriterionScore]) -> float:
    weight_sum = sum(item.weight for item in criteria) or 1
    weighted = sum(item.score * item.weight for item in criteria) / weight_sum
    return round(weighted, 2)


def _expected_points_discrete(max_points: int, score_pct: float) -> int:
    raw_points = max_points * score_pct / 100.0
    rounded = int(raw_points + 0.5)
    return max(0, min(max_points, rounded))


def _overall_points(criteria: list[CriterionScore]) -> tuple[int, int]:
    total_expected = sum(_expected_points_discrete(item.weight, item.score) for item in criteria)
    total_max = sum(item.weight for item in criteria)
    return total_expected, total_max


def _detect_level(overall_points: int, max_points: int, calibration_examples: list[dict[str, Any]]) -> tuple[str, str]:
    if calibration_examples:
        grouped: dict[str, list[float]] = {"strong": [], "medium": [], "weak": []}
        for example in calibration_examples:
            level = str(example.get("level", "")).lower()
            score = example.get("overall_score")
            if level in grouped and isinstance(score, (int, float)):
                grouped[level].append(float(score))

        centers = {k: mean(v) for k, v in grouped.items() if v}
        if centers:
            # Backward compatibility: when calibration still stores 0..100 scores.
            percent_points = (overall_points / max_points) * 100 if max_points else 0
            nearest = min(centers.keys(), key=lambda key: abs(percent_points - centers[key]))
            return nearest, "Уровень определен по калибровке."

    if overall_points >= 12:
        return "strong", "Граница по умолчанию: strong >= 12 из 15"
    if overall_points >= 8:
        return "medium", "Граница по умолчанию: 8 <= medium < 12 из 15"
    return "weak", "Граница по умолчанию: weak < 8 из 15"


def _llm_issues_to_model(issues_payload: list[dict[str, Any]]) -> list[Issue]:
    result: list[Issue] = []
    for issue in issues_payload:
        result.append(
            Issue(
                code=str(issue.get("code", "LLM_ISSUE")),
                category=str(issue.get("category", "content")),
                severity=str(issue.get("severity", "medium")),
                message=str(issue.get("message", "Замечание по результатам LLM-анализа")),
                evidence=str(issue.get("evidence", "")),
                rule_ref=str(issue.get("rule_ref", "LLM")),
                hint_for_intern="Проверь этот фрагмент и уточни формулировки.",
            )
        )
    return result


def _extract_fio_from_filename(input_path: str) -> str:
    filename = str(input_path).replace("\\", "/").rsplit("/", 1)[-1]
    stem = re.sub(r"\.[^.]+$", "", filename)
    stem = re.sub(r"^[0-9a-f]{16,}_", "", stem, flags=re.IGNORECASE)
    prepared = re.sub(r"[_\-]+", " ", stem)
    prepared = re.sub(r"\s+", " ", prepared).strip()

    initials_match = re.search(
        r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ])\.?\s*([А-ЯЁ])\.?\b",
        prepared,
    )
    if initials_match:
        return f"{initials_match.group(1)} {initials_match.group(2)}.{initials_match.group(3)}."

    full_match = re.search(
        r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\b",
        prepared,
    )
    if full_match:
        return " ".join(full_match.groups())

    two_word_match = re.search(r"\b([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)\b", prepared)
    if two_word_match:
        return " ".join(two_word_match.groups())

    return ""


def _extract_intern_fio(lines: list[str], input_path: str = "") -> str:
    strict_patterns = [
        r"\b([А-ЯЁ][а-яё]+ [А-ЯЁ]\.\s*[А-ЯЁ]\.)",
        r"\b([А-ЯЁ][а-яё]+ [А-ЯЁ]\.[А-ЯЁ]\.)",
        r"\b([А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+)\b",
    ]
    weak_pattern = r"\b([А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+)\b"

    def _search_in_text(text: str, allow_weak: bool = False) -> str:
        for pattern in strict_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        if allow_weak:
            match = re.search(weak_pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    focus = lines[:80]
    for idx, line in enumerate(focus):
        line_norm = line.lower()
        if any(marker in line_norm for marker in ("автор", "фио", "исполнитель")):
            window = "\n".join(focus[max(0, idx - 2) : idx + 6])
            fio = _search_in_text(window, allow_weak=True)
            if fio:
                return fio

    for line in focus:
        fio = _search_in_text(line, allow_weak=False)
        if fio:
            return fio

    if input_path:
        return _extract_fio_from_filename(input_path)

    return ""


def _hr_style_feedback(raw_tips: list[str], level: str) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in raw_tips:
        prepared = str(item).strip()
        if not prepared:
            continue
        key = prepared.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(prepared)

    def _rewrite_tip(tip: str) -> str:
        tip_clean = tip.rstrip(".")
        lower = tip_clean.lower()
        if lower.startswith("проверь"):
            tail = tip_clean[7:].strip()
            return f"Рекомендую проверить {tail}." if tail else "Рекомендую проверить этот блок еще раз."
        if lower.startswith("уточни"):
            tail = tip_clean[6:].strip()
            return f"Стоит уточнить {tail}." if tail else "Стоит уточнить формулировки."
        if lower.startswith("сверь"):
            tail = tip_clean[5:].strip()
            return f"Полезно сверить {tail}." if tail else "Полезно сверить этот раздел с материалами кейса."
        if lower.startswith("добавь"):
            tail = tip_clean[6:].strip()
            return f"Рекомендую добавить {tail}." if tail else "Рекомендую добавить недостающие детали."
        if lower.startswith("доработай"):
            tail = tip_clean[9:].strip()
            return f"Стоит доработать {tail}." if tail else "Стоит доработать этот раздел."
        if lower.startswith("снизь"):
            tail = tip_clean[5:].strip()
            return f"Лучше снизить {tail}." if tail else "Лучше снизить количество шаблонных формулировок."
        return f"Обратите внимание: {tip_clean[:1].lower() + tip_clean[1:]}."

    if level == "strong":
        lead = "Работа уже сильная. Чтобы сделать результат еще увереннее:"
    elif level == "medium":
        lead = "База хорошая. Чтобы выйти на более высокий уровень:"
    else:
        lead = "Есть потенциал для роста. Рекомендую сфокусироваться на следующем:"

    rewritten = [_rewrite_tip(item) for item in unique]
    if not rewritten:
        return []
    return [lead] + rewritten


def run_review(
    input_path: str,
    profile_id: str,
    enable_llm: bool = True,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        profile = load_profile(profile_id)
    except Exception as exc:
        return {
            "status": "error",
            "error": f"Ошибка загрузки профиля: {exc}",
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        extracted = extract_submission(input_path)
    except ExtractionError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "profile_id": profile_id,
            "input_path": input_path,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }

    rule_result = analyze_rule_based(
        extracted.text,
        extracted.lines,
        profile,
        formatting_metadata=extracted.formatting_metadata,
    )

    llm_result: dict[str, Any] = {"status": "skipped", "reason": "LLM отключен параметром"}
    if enable_llm:
        llm_result = run_llm_assessment(
            profile=profile,
            submission_text=extracted.text,
            rule_summary=rule_result.coverage_summary,
        )

    merged_criteria = _merge_scores(rule_result.criteria, llm_result)
    overall_score = _weighted_overall(merged_criteria)
    overall_points, max_points = _overall_points(merged_criteria)

    mentor_issues = list(rule_result.mentor_issues)
    intern_tips = list(rule_result.intern_tips)
    if llm_result.get("status") == "ok":
        payload = llm_result.get("payload", {})
        mentor_issues.extend(_llm_issues_to_model(payload.get("mentor_issues", [])))
        for tip in payload.get("intern_tips", []):
            tip_text = str(tip).strip()
            if tip_text:
                intern_tips.append(tip_text)
    elif llm_result.get("status") == "error":
        mentor_issues.append(
            Issue(
                code="LLM_RUNTIME_001",
                category="system",
                severity="low",
                message="LLM-слой недоступен, оценка выполнена rule-based логикой.",
                evidence=str(llm_result.get("reason", "")),
                rule_ref="SYSTEM_RESILIENCE",
                hint_for_intern="",
            )
        )

    calibration_examples = load_calibration_examples(profile.get("calibration_dir", ""))
    level, level_note = _detect_level(overall_points, max_points, calibration_examples)
    intern_fio = _extract_intern_fio(extracted.lines, input_path=input_path)

    mentor_issues_sorted = sorted(
        mentor_issues,
        key=lambda issue: {"high": 0, "medium": 1, "low": 2}.get(issue.severity, 3),
    )
    hr_feedback = _hr_style_feedback(intern_tips, level)

    return {
        "status": "ok",
        "profile_id": profile_id,
        "input_path": input_path,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "overall_score": overall_score,
        "overall_points": overall_points,
        "max_points": max_points,
        "level": level,
        "level_note": level_note,
        "intern_fio": intern_fio,
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "title": c.title,
                "weight": c.weight,
                "score": c.score,
                "rationale": c.rationale,
                "evidence": c.evidence,
            }
            for c in merged_criteria
        ],
        "mentor_block": {
            "summary": f"Уровень работы: {level}, итоговый балл: {overall_points}/{max_points}",
            "issues": [
                {
                    "code": i.code,
                    "category": i.category,
                    "severity": i.severity,
                    "message": i.message,
                    "evidence": i.evidence,
                    "rule_ref": i.rule_ref,
                }
                for i in mentor_issues_sorted
            ],
        },
        "intern_block": {
            "summary": "Рекомендации по доработке в стиле наставнической обратной связи.",
            "feedback": hr_feedback,
        },
        "ai_risk_signals": [
            {
                "signal": item.signal,
                "evidence": item.evidence,
                "explanation": item.explanation,
            }
            for item in rule_result.ai_risk_signals
        ],
        "coverage_summary": rule_result.coverage_summary,
        "llm_status": llm_result,
    }
