from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _expected_points_discrete(max_points: int, score_pct: float) -> int:
    raw_points = max_points * score_pct / 100.0
    rounded = int(raw_points + 0.5)
    return max(0, min(max_points, rounded))


def _escape_table_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def _short_evidence(evidence: Any, limit: int = 3) -> list[str]:
    if not isinstance(evidence, list):
        return []
    prepared: list[str] = []
    for item in evidence:
        text = _escape_table_cell(str(item))
        if not text:
            continue
        if len(text) > 180:
            text = text[:177].rstrip() + "..."
        prepared.append(text)
        if limit > 0 and len(prepared) >= limit:
            break
    return prepared


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in markers)


def _format_evidence_item(item: str, is_literacy: bool) -> str:
    if is_literacy and " — " in item:
        fragment, comment = item.split(" — ", 1)
        return f"«{fragment}» — {comment}"
    return f"«{item}»"


def _mentor_comment(
    max_points: int,
    awarded_points: int,
    rationale: str,
    evidence: Any,
    criterion_id: str = "",
) -> str:
    rationale = _escape_table_cell(rationale)
    if criterion_id == "structure_template" and rationale:
        return rationale

    is_literacy = criterion_id == "literacy_punctuation" or "языков" in rationale.lower()
    evidence_limit = 0 if is_literacy else (7 if awarded_points == 0 else 3)
    evidence_items = _short_evidence(evidence, limit=evidence_limit)

    negative_markers = (
        "не соответствует",
        "не указ",
        "отсутств",
        "не выполн",
        "не заполн",
        "снижен",
        "снят",
        "ошиб",
        "недостаточ",
        "непол",
    )
    positive_markers = (
        "соответствует",
        "коррект",
        "выполн",
        "указан",
        "заполнен",
        "полно",
        "детализ",
    )

    if max_points == 2:
        if awarded_points == 2:
            lead = "Поставлено 2 балла"
        elif awarded_points == 1:
            lead = "Поставлен 1 балл"
        else:
            lead = "Балл снят"
    else:
        lead = "Балл поставлен" if awarded_points == max_points else "Балл снят"

    is_positive_score = awarded_points == max_points
    is_zero_score = awarded_points == 0
    rationale_conflicts = (
        (is_positive_score and _has_marker(rationale, negative_markers))
        or (is_zero_score and _has_marker(rationale, positive_markers))
        or (max_points == 2 and awarded_points == 1 and _has_marker(rationale, ("полно", "детализ")))
    )

    if rationale_conflicts:
        comment = (
            f"{lead} по итоговой оценке критерия. "
            "Автоматическое пояснение конфликтует с баллом; это место стоит перепроверить вручную."
        )
        if evidence_items:
            if is_literacy:
                comment += "\nОшибки:\n" + "\n".join(
                    f"- {_format_evidence_item(item, is_literacy=True)}" for item in evidence_items
                )
            else:
                comment += " Фрагменты для проверки: " + "; ".join(
                    _format_evidence_item(item, is_literacy=False) for item in evidence_items
                ) + "."
        return comment
    elif rationale:
        reason = rationale.rstrip(".")
        reason = reason[:1].lower() + reason[1:] if reason else "критерий оценен"
    else:
        reason = "критерий оценен по материалам работы"

    comment = f"{lead}, потому что {reason}."
    if evidence_items:
        if is_literacy:
            comment += "\nОшибки:\n" + "\n".join(
                f"- {_format_evidence_item(item, is_literacy=True)}" for item in evidence_items
            )
        else:
            comment += " Примеры из текста: " + "; ".join(
                _format_evidence_item(item, is_literacy=False) for item in evidence_items
            ) + "."
    else:
        comment += " Конкретные фрагменты в автоматической проверке не выделены."
    if awarded_points == 0 and is_literacy:
        comment += "\nПо критериям при 3 и более ошибках ставится 0."
    return comment


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _formatting_comment(
    result: dict[str, Any],
    expected_points: int,
    max_points: int,
    fallback_comment: str,
) -> str:
    details = result.get("coverage_summary", {}).get("formatting_instruction", {})
    if not isinstance(details, dict):
        return fallback_comment

    facts = details.get("checklist_facts", {})
    if not isinstance(facts, dict):
        return fallback_comment

    checklist = facts.get("checklist", [])
    if not isinstance(checklist, list) or not checklist:
        return fallback_comment

    prepared_rules = [item for item in checklist if isinstance(item, dict)]
    failed = [item for item in prepared_rules if item.get("status") == "fail"]
    warned = [item for item in prepared_rules if item.get("status") == "warn"]
    needs_review = [item for item in prepared_rules if item.get("status") == "needs_review"]
    systemic = [item for item in prepared_rules if item.get("systemic")]
    blocking = [item for item in prepared_rules if item.get("blocking")]

    if expected_points == max_points:
        comment = (
            "Балл поставлен: по критерию 2 оформление принято как соответствующее инструкции. "
            "Блокирующих пояснений из шаблона не выявлено; найденные автоматические сигналы не выглядят "
            "как 3+ ошибки или системное нарушение."
        )
        if warned or needs_review:
            comment += (
                f" Для контроля: предупреждений по чеклисту - {len(warned)}, "
                f"правил для ручной сверки - {len(needs_review)}."
            )
        return comment

    reasons = blocking or systemic or failed or warned
    reason_texts: list[str] = []
    for item in reasons[:5]:
        rule = str(item.get("rule", "")).strip()
        rule_id = str(item.get("id", "")).strip()
        evidence = _as_text_list(item.get("evidence"))
        label = rule or rule_id or "правило чеклиста"
        text = label.rstrip(".")
        if evidence:
            text += f": {evidence[0]}"
        reason_texts.append(text)

    if reason_texts:
        return (
            "Балл снят: по чеклисту критерия 2 найдены блокирующие или системные нарушения оформления. "
            "Ключевые причины: " + "; ".join(reason_texts) + "."
        )

    return (
        "Балл снят: итоговая проверка критерия 2 выявила нарушения оформления по инструкции. "
        "Подробные факты доступны в JSON-отчете в блоке `coverage_summary.formatting_instruction`."
    )


def _rule_short_name(rule_id: str, rule: str) -> str:
    labels = {
        "no_template_explanations": "Пояснения шаблона",
        "font_verdana": "Шрифт Verdana",
        "body_font_size_9": "Основной текст 9 пт",
        "body_single_line_no_before_spacing": "Интервалы основного текста",
        "body_no_indent": "Отступы основного текста",
        "heading_level1_format": "Заголовки 1 уровня",
        "heading_level2_format": "Заголовки 2 уровня",
        "heading_level3_plus_format": "Заголовки 3+ уровней",
        "numbered_heading_indents": "Выступы нумерованных заголовков",
        "unnumbered_heading_indent": "Отступы ненумерованных заголовков",
        "list_font_and_markers": "Списки: шрифт и маркеры",
        "list_indents": "Отступы списков",
        "list_capitalization_punctuation": "Пункты списков",
        "template_tables": "Таблицы Word",
        "table_sequential_numbering_and_caption": "Нумерация и подписи таблиц",
        "table_caption_formatting": "Оформление подписей таблиц",
        "table_body_and_header_formatting": "Оформление текста таблиц",
        "table_references_and_placement": "Ссылки и размещение таблиц",
        "table_borders_no_indents": "Границы таблиц",
        "figure_numbering_caption_and_references": "Подписи и ссылки на рисунки",
        "figure_caption_formatting_and_readability": "Читаемость рисунков",
        "toc_format": "Оглавление",
        "heading_numbering": "Нумерация заголовков",
        "structured_sections": "Визуальная структура",
        "page_numbering_footer": "Нумерация страниц",
        "footer_document_name": "Нижний колонтитул",
        "systemic_formatting_errors": "Системность ошибок",
    }
    return labels.get(rule_id, rule.split(".")[0][:80] if rule else rule_id)


def _manual_check_hint(rule_id: str, rule: str) -> str:
    hints = {
        "font_verdana": (
            "Быстро найти в Word: Ctrl+H -> Больше -> Формат -> Шрифт, выбрать найденный шрифт "
            "из примеров ниже и нажимать «Найти далее». Можно также искать точный фрагмент через Ctrl+F."
        ),
        "body_font_size_9": "Открыть указанные фрагменты в Word и проверить размер основного текста: по инструкции должен быть 9 пт.",
        "body_no_indent": "Выделить указанный абзац в Word и открыть настройки абзаца: у основного текста не должно быть отступа или выступа.",
        "list_font_and_markers": "Проверить списки рядом с указанными фрагментами: маркированные пункты должны быть дефисами, текст - Verdana 9.",
        "toc_format": (
            "Это диагностическая метрика, а не самостоятельная ошибка: строки оглавления нужны, чтобы понять, "
            "похоже ли оглавление на настоящее и можно ли сверять его с разделами/страницами."
        ),
        "numbered_heading_indents": "Открыть 2-3 нумерованных заголовка и сверить выступы с инструкцией.",
        "unnumbered_heading_indent": "Проверить ненумерованные заголовки: есть ли отступ 1,25 мм.",
        "list_indents": "Посмотреть списки разных уровней: не съехали ли отступы.",
        "list_capitalization_punctuation": "Проверить единообразие начала и конца пунктов списка.",
        "table_caption_formatting": "Проверить 1-2 подписи таблиц: справа, Verdana 9, без лишних отступов.",
        "table_body_and_header_formatting": "Открыть таблицу: заголовок должен быть Verdana 11 bold по центру, тело Verdana 9 слева.",
        "table_borders_no_indents": "Визуально проверить границы и внутренние отступы таблиц.",
        "figure_caption_formatting_and_readability": "Проверить, читаемы ли рисунки и правильно ли оформлены подписи.",
    }
    return hints.get(rule_id, rule.rstrip(".") or "Сверить правило вручную по инструкции.")


def _formatting_rule_view(item: dict[str, Any]) -> dict[str, Any]:
    rule_id = str(item.get("id", "")).strip()
    rule = str(item.get("rule", "")).strip()
    evidence = _as_text_list(item.get("evidence"))
    evidence_limit = 8 if rule_id == "font_verdana" else 3
    prepared_evidence = [_formatting_evidence_item(rule_id, value) for value in evidence[:evidence_limit]]
    return {
        "id": rule_id,
        "title": _rule_short_name(rule_id, rule),
        "rule": rule,
        "status": str(item.get("status", "")).strip(),
        "error_count": int(item.get("error_count", 0) or 0),
        "systemic": bool(item.get("systemic")),
        "blocking": bool(item.get("blocking")),
        "evidence": prepared_evidence,
        "raw_evidence": evidence[:evidence_limit],
        "impact_reason": _formatting_impact_reason(
            rule_id=rule_id,
            status=str(item.get("status", "")).strip(),
            error_count=int(item.get("error_count", 0) or 0),
            systemic=bool(item.get("systemic")),
            blocking=bool(item.get("blocking")),
        ),
        "manual_hint": _manual_check_hint(rule_id, rule),
    }


def _formatting_evidence_item(rule_id: str, value: str) -> str:
    text = _escape_table_cell(value)
    if not text:
        return ""
    if rule_id == "systemic_formatting_errors" and re.fullmatch(r"[a-z0-9_]+", text):
        return f"Сработавшее правило: {_rule_short_name(text, '')}"
    if rule_id == "body_font_size_9":
        match = re.match(r"^([0-9]+(?:\.[0-9]+)?):\s*(.+)$", text)
        if match:
            return f"размер {match.group(1)} пт: {match.group(2)}"
    if rule_id == "body_no_indent":
        match = re.match(r"^\{[^}]+\}:\s*(.+)$", text)
        if match:
            return f"фрагмент с нестандартным отступом: {match.group(1)}"
    return text


def _ru_plural(value: int, one: str, few: str, many: str) -> str:
    value_abs = abs(value)
    if value_abs % 10 == 1 and value_abs % 100 != 11:
        return one
    if value_abs % 10 in {2, 3, 4} and value_abs % 100 not in {12, 13, 14}:
        return few
    return many


def _formatting_impact_reason(
    *,
    rule_id: str,
    status: str,
    error_count: int,
    systemic: bool,
    blocking: bool,
) -> str:
    if rule_id == "systemic_formatting_errors":
        return (
            "Итоговое правило: балл снимается, если нарушения повторяются по документу "
            "или суммарно набирается 3+ ошибок оформления."
        )
    if blocking:
        return "Блокирующее нарушение: одного такого сигнала достаточно для незачета критерия."
    if systemic:
        count = max(error_count, 1)
        return f"Повторяющееся нарушение: найдено {count} {_ru_plural(count, 'ошибка', 'ошибки', 'ошибок')} этого типа."
    if status == "fail":
        count = max(error_count, 1)
        return f"Нарушение чеклиста: найдено {count} {_ru_plural(count, 'ошибка', 'ошибки', 'ошибок')}."
    if status == "warn":
        return "Предупреждение: само по себе не снимает балл, но помогает наставнику перепроверить место."
    if status == "needs_review":
        return "Нужна ручная сверка: автопроверка собрала данные, но не снижает балл самостоятельно."
    return "Нарушений по правилу не найдено."


def _expand_systemic_formatting_reasons(rules: list[dict[str, Any]], reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in rules if item.get("id")}
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()

    for reason in reasons:
        if reason.get("id") == "systemic_formatting_errors":
            raw_ids = [
                value
                for value in reason.get("raw_evidence", [])
                if isinstance(value, str) and value in by_id and value != "systemic_formatting_errors"
            ]
            for raw_id in raw_ids:
                item = dict(by_id[raw_id])
                item["impact_reason"] = (
                    item.get("impact_reason")
                    or "Это правило входит в системные нарушения оформления, которые повлияли на балл."
                )
                if item["id"] not in seen:
                    expanded.append(item)
                    seen.add(item["id"])
            if raw_ids:
                continue

        item_id = str(reason.get("id", ""))
        if item_id not in seen:
            expanded.append(reason)
            seen.add(item_id)

    return expanded


def _formatting_toc_note(metadata_summary: dict[str, Any]) -> str:
    try:
        count = int(metadata_summary.get("toc_like_line_count", 0) or 0)
    except Exception:
        count = 0
    if count >= 3:
        return (
            f"Строк оглавления: {count}. Это хорошо как технический сигнал: оглавление распознано. "
            "Само число не снижает балл; проверять нужно совпадение оглавления с реальными разделами и страницами."
        )
    if count > 0:
        return (
            f"Строк оглавления: {count}. Это предупреждение: строк мало, поэтому оглавление стоит открыть "
            "и сверить вручную."
        )
    return "Строк оглавления: 0. Автопроверка не распознала оглавление; его нужно проверить вручную."


def _formatting_view(
    result: dict[str, Any],
    expected_points: int,
    max_points: int,
    fallback_comment: str,
) -> dict[str, Any]:
    details = result.get("coverage_summary", {}).get("formatting_instruction", {})
    facts = details.get("checklist_facts", {}) if isinstance(details, dict) else {}
    checklist = facts.get("checklist", []) if isinstance(facts, dict) else []
    if not isinstance(checklist, list):
        checklist = []

    rules = [_formatting_rule_view(item) for item in checklist if isinstance(item, dict)]
    blocking = [item for item in rules if item["blocking"]]
    systemic = [item for item in rules if item["systemic"]]
    failed = [item for item in rules if item["status"] == "fail" and not item["blocking"] and not item["systemic"]]
    warned = [item for item in rules if item["status"] == "warn"]
    needs_review = [item for item in rules if item["status"] == "needs_review"]
    passed = [item for item in rules if item["status"] == "pass"]

    is_awarded = expected_points == max_points
    actionable_issues = blocking or systemic or failed
    score_reasons = _expand_systemic_formatting_reasons(rules, actionable_issues)
    if not score_reasons:
        score_reasons = [{"title": "не выявлены", "evidence": [], "manual_hint": ""}]

    if not warned:
        warned = [{"title": "не выявлены", "evidence": [], "manual_hint": ""}]
    visible_manual_checks = [] if is_awarded else needs_review
    if not visible_manual_checks:
        needs_review = [{"title": "не требуются", "evidence": [], "manual_hint": ""}]
    else:
        needs_review = visible_manual_checks

    decision_hint = facts.get("decision_hint", {}) if isinstance(facts, dict) else {}
    metadata_summary = facts.get("metadata_summary", {}) if isinstance(facts, dict) else {}
    font_counts = metadata_summary.get("font_counts", {}) if isinstance(metadata_summary, dict) else {}
    if not isinstance(font_counts, dict):
        font_counts = {}
    font_summary_text = ", ".join(f"{font}: {count}" for font, count in sorted(font_counts.items()))
    font_rule = next((item for item in rules if item["id"] == "font_verdana"), None)
    font_examples = font_rule["evidence"] if font_rule and int(metadata_summary.get("non_verdana_count", 0) or 0) else []
    impactful_rule_ids = {
        item["id"]
        for item in score_reasons
        if item.get("id") and item.get("id") != "systemic_formatting_errors"
    }
    impactful_count = 0 if is_awarded else len(impactful_rule_ids or {item["id"] for item in actionable_issues if item.get("id")})
    estimated_error_count = (
        int(decision_hint.get("estimated_error_count", 0) or 0) if isinstance(decision_hint, dict) else 0
    )
    if is_awarded:
        decision_summary = (
            "Балл зачтен: автопроверка не нашла блокирующих или системных нарушений оформления. "
            "Предупреждения ниже нужны только для быстрой ручной сверки."
        )
    else:
        rule_word = _ru_plural(impactful_count, "правило", "правила", "правил")
        error_word = _ru_plural(estimated_error_count, "ошибка", "ошибки", "ошибок")
        rule_phrase = (
            f"{impactful_count} сработавшее правило"
            if rule_word == "правило"
            else f"{impactful_count} сработавших {rule_word}"
        )
        decision_summary = (
            f"Балл снят: найдено {rule_phrase} оформления "
            f"и примерно {estimated_error_count} {error_word} по чеклисту. "
            "Ниже показаны только причины, которые влияют на балл; полный технический список спрятан в сводке."
        )

    return {
        "status": "ok" if is_awarded else "bad",
        "status_label": "Зачтено" if is_awarded else "Не зачтено",
        "passed_count": len(passed),
        "total_count": len(rules),
        "failed_count": impactful_count,
        "warning_count": len([item for item in rules if item["status"] == "warn"]),
        "manual_count": 0 if is_awarded else len([item for item in rules if item["status"] == "needs_review"]),
        "score_reasons": score_reasons[:7],
        "warnings": warned[:6],
        "manual_checks": needs_review[:8],
        "decision_summary": decision_summary,
        "checklist_explanation": (
            "Это автоматический чеклист оформления по инструкции: шрифты, интервалы, отступы, списки, таблицы, "
            "рисунки, оглавление и колонтитулы. Не все 27 пунктов напрямую снимают балл: часть является "
            "предупреждениями или данными для ручной сверки."
        ),
        "metadata_summary": metadata_summary,
        "font_summary_text": font_summary_text,
        "font_examples": font_examples,
        "toc_note": _formatting_toc_note(metadata_summary),
        "has_actionable_issues": bool(actionable_issues),
        "estimated_error_count": estimated_error_count,
        "fallback_comment": fallback_comment,
    }


def _criterion_manual_hint(criterion_id: str, title: str, awarded_points: int, max_points: int) -> str:
    hints = {
        "literacy_punctuation": "Просмотреть найденные языковые фрагменты: при 3 и более ошибках по критерию ставится 0.",
        "document_versioning": "Сверить таблицу сведений о документе: версия, дата, ФИО автора и описание изменений.",
        "terms_glossary": "Проверить, что термины реально используются в работе, имеют определения и идут в алфавитном порядке.",
        "table_of_contents": "Сверить оглавление с фактическими заголовками и номерами страниц в документе.",
        "system_naming": "Проверить раздел с полным и условным наименованием системы: должны быть оба варианта.",
        "business_goals": "Сверить цели с кейсом и интервью: это должны быть бизнес-результаты, а не просто внедрение системы.",
        "current_situation": "Проверить AS-IS: роли, действия, периодичность, артефакты/результаты и проблемы текущего процесса.",
        "data_requirements": "Сверить требования к данным: источники, показатели, формулы, обновление, агрегации и детализация.",
        "visualization_requirements": "Проверить отчеты/дашборды: фильтры, разрезы, drill-down, поведение и связь с показателями.",
        "non_functional_requirements": "Проверить, что НФТ описывают качество системы, а не функциональные сценарии.",
        "security_access": "Сверить роли, права, аутентификацию и разграничение доступа к данным/отчетам.",
        "independent_work": "Просмотреть ИИ-маркеры как риск-сигналы: они не являются доказательством, но требуют внимания.",
    }
    if awarded_points == max_points:
        return hints.get(criterion_id, f"Быстро сверить ключевое условие критерия: {title}.")
    return hints.get(criterion_id, f"Открыть соответствующий раздел работы и сверить его с критерием: {title}.")


def _criterion_examples(evidence: Any, criterion_id: str, awarded_points: int, max_points: int) -> list[str]:
    is_literacy = criterion_id == "literacy_punctuation"
    limit = 8 if is_literacy and awarded_points < max_points else 3
    return [_format_evidence_item(item, is_literacy=is_literacy) for item in _short_evidence(evidence, limit=limit)]


def _generic_criterion_view(
    *,
    criterion_id: str,
    title: str,
    expected_points: int,
    max_points: int,
    rationale: str,
    evidence: Any,
    mentor_comment: str,
) -> dict[str, Any]:
    is_awarded = expected_points == max_points
    is_partial = 0 < expected_points < max_points
    examples = _criterion_examples(evidence, criterion_id, expected_points, max_points)
    clean_rationale = _escape_table_cell(rationale).rstrip(".")

    if is_awarded:
        score_notes = ["не выявлены"]
        neutral_notes = [clean_rationale or "Критерий зачтен по фактам работы."]
        if examples:
            neutral_notes.append("Примеры: " + "; ".join(examples))
    else:
        if is_partial:
            score_notes = [
                clean_rationale
                or f"Поставлен частичный балл: {expected_points} из {max_points}."
            ]
        else:
            score_notes = [clean_rationale or "Критерий не выполнен по итоговой оценке."]
        if examples:
            score_notes.append("Примеры: " + "; ".join(examples))
        neutral_notes = ["дополнительных предупреждений не выделено"]

    return {
        "status": "ok" if is_awarded else "bad",
        "status_label": "Зачтено" if is_awarded else ("Частично" if is_partial else "Не зачтено"),
        "score_notes": score_notes,
        "neutral_notes": neutral_notes,
        "manual_checks": [_criterion_manual_hint(criterion_id, title, expected_points, max_points)],
        "examples": examples,
        "fallback_comment": mentor_comment,
    }


def _structure_view(
    result: dict[str, Any],
    expected_points: int,
    max_points: int,
    fallback_comment: str,
) -> dict[str, Any]:
    details = result.get("coverage_summary", {}).get("structure_template", {})
    if not isinstance(details, dict):
        details = {}

    found_sections = _as_text_list(details.get("found_sections"))
    found_section_headings = _as_text_list(details.get("found_section_headings"))
    missing_sections = _as_text_list(details.get("missing_sections"))
    critical_deviations = _as_text_list(details.get("critical_deviations"))
    neutral_notes = _as_text_list(details.get("neutral_notes"))
    added_sections = _as_text_list(details.get("added_sections"))
    pre_toc_sections = _as_text_list(details.get("pre_toc_sections_in_toc"))
    body_not_in_toc = _as_text_list(details.get("body_not_in_toc"))
    toc_not_in_body = _as_text_list(details.get("toc_not_in_body"))

    llm_assist = result.get("llm_status", {}).get("payload", {}).get("structure_template_assist", {})
    if not isinstance(llm_assist, dict):
        llm_assist = {}
    llm_summary = str(llm_assist.get("summary", "")).strip()
    llm_notes = _as_text_list(llm_assist.get("manual_review_notes"))

    quick_checks: list[str] = []
    if missing_sections or toc_not_in_body:
        quick_checks.append("Сверить отсутствующие разделы с телом документа, не только с оглавлением.")
    if body_not_in_toc:
        quick_checks.append("Проверить, нужно ли обновить оглавление под фактическую структуру.")
    if pre_toc_sections:
        quick_checks.append("Проверить корректность страниц для разделов до оглавления.")
    if added_sections:
        quick_checks.append("Убедиться, что добавленные разделы не дублируют разделы шаблона.")
    if not quick_checks:
        quick_checks.append("Быстро сверить названия разделов и номера страниц в оглавлении.")

    required_count = len(found_sections) + len(missing_sections)
    if required_count == 0:
        required_count = len(found_sections)

    return {
        "status": "ok" if expected_points == max_points else "bad",
        "status_label": "Зачтено" if expected_points == max_points else "Не зачтено",
        "found_count": len(found_sections),
        "required_count": required_count,
        "found_sections": found_section_headings or found_sections,
        "critical_deviations": critical_deviations or ["не выявлены"],
        "neutral_notes": neutral_notes or ["не выявлены"],
        "added_sections": added_sections,
        "llm_summary": llm_summary,
        "llm_notes": llm_notes,
        "quick_checks": quick_checks,
        "fallback_comment": fallback_comment,
    }


def build_criteria_view(result: dict[str, Any]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in result.get("criteria", []):
        max_points = int(item.get("weight", 0))
        expected = _expected_points_discrete(max_points, float(item.get("score", 0)))
        criterion_id = str(item.get("criterion_id", ""))
        title = str(item.get("title", ""))
        is_formatting = criterion_id == "formatting_instruction" or title.startswith("2. ")
        base_mentor_comment = _mentor_comment(
            max_points=max_points,
            awarded_points=expected,
            rationale=str(item.get("rationale", "")),
            evidence=item.get("evidence", []),
            criterion_id=criterion_id,
        )
        mentor_comment = (
            _formatting_comment(
                result=result,
                expected_points=expected,
                max_points=max_points,
                fallback_comment=base_mentor_comment,
            )
            if is_formatting
            else base_mentor_comment
        )
        row = {
            "criterion_id": criterion_id,
            "title": title,
            "max_points": max_points,
            "expected_points": expected,
            "mentor_comment": mentor_comment,
        }
        if criterion_id == "structure_template":
            row["structure_view"] = _structure_view(
                result=result,
                expected_points=expected,
                max_points=max_points,
                fallback_comment=mentor_comment,
            )
        if is_formatting:
            row["formatting_view"] = _formatting_view(
                result=result,
                expected_points=expected,
                max_points=max_points,
                fallback_comment=mentor_comment,
            )
        if criterion_id != "structure_template" and not is_formatting:
            row["criterion_view"] = _generic_criterion_view(
                criterion_id=criterion_id,
                title=title,
                expected_points=expected,
                max_points=max_points,
                rationale=str(item.get("rationale", "")),
                evidence=item.get("evidence", []),
                mentor_comment=mentor_comment,
            )
        prepared.append(
            row
        )
    return prepared


def build_markdown_report(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return "\n".join(
            [
                "# Отчет проверки",
                "",
                "## Статус",
                "Ошибка",
                "",
                "## Детали",
                str(result.get("error", "Неизвестная ошибка")),
            ]
        )

    criteria = result.get("criteria", [])
    max_total_points = sum(int(item.get("weight", 0)) for item in criteria)
    criteria_rows = build_criteria_view(result)
    total_expected_points = sum(row["expected_points"] for row in criteria_rows)

    lines: list[str] = []
    lines.append("# Отчет проверки")
    lines.append("")
    lines.append("## Общее резюме")
    lines.append(f"- Профиль: `{result.get('profile_id')}`")
    lines.append(f"- ФИО из работы: **{result.get('intern_fio') or 'не определено'}**")
    lines.append(f"- Итоговый балл: **{total_expected_points} / {max_total_points}**")
    lines.append(f"- Уровень: **{result.get('level')}**")
    lines.append(f"- Примечание по калибровке: {result.get('level_note')}")
    lines.append("")

    lines.append("## Оценка по критериям")
    lines.append("| Критерий | Макс. балл | Балл | Почему такая оценка с примерами из текста |")
    lines.append("|---|---:|---:|---|")
    for row in criteria_rows:
        mentor_comment = str(row["mentor_comment"]).replace("\n", "<br>")
        lines.append(
            f"| {_escape_table_cell(row['title'])} | {row['max_points']} | "
            f"{row['expected_points']} | {mentor_comment} |"
        )
    lines.append(
        f"| **ИТОГО** | **{max_total_points}** | **{total_expected_points}** | "
        "**Сумма баллов по критериям** |"
    )
    lines.append("")

    lines.append("## Фокус для наставника")
    issues = result.get("mentor_block", {}).get("issues", [])
    if not issues:
        lines.append("- Критичных замечаний не найдено.")
    else:
        for issue in issues:
            lines.append(
                f"- [{issue['severity']}] {issue['message']} "
                f"(доказательство: `{issue['evidence']}`, правило: `{issue['rule_ref']}`)"
            )
    lines.append("")

    lines.append("## Обратная связь стажеру")
    feedback = result.get("intern_block", {}).get("feedback", [])
    if not feedback:
        lines.append("- Проверь полноту и конкретику решения.")
    else:
        for tip in feedback:
            lines.append(f"- {tip}")
    lines.append("")

    lines.append("## Риск-сигналы ИИ")
    ai_signals = result.get("ai_risk_signals", [])
    if not ai_signals:
        lines.append("- Не выявлены.")
    else:
        for signal in ai_signals:
            lines.append(f"- {signal['signal']}: `{signal['evidence']}`. {signal['explanation']}")
    lines.append("")

    return "\n".join(lines)


def save_outputs(result: dict[str, Any], output_dir: str, base_name: str) -> tuple[Path, Path]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    json_path = target_dir / f"{base_name}.json"
    md_path = target_dir / f"{base_name}.md"

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_markdown_report(result), encoding="utf-8")
    return json_path, md_path
