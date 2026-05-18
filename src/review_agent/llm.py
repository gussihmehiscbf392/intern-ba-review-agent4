from __future__ import annotations

import json
import os
import re
from typing import Any


_DEFAULT_PROMPT_TEMPLATE = """
Роль: строгий эксперт по проверке ФТТ стажеров-аналитиков.
Оцени документ стажера только по критериям и верни только валидный JSON.

Критерии:
{criteria_lines}

Контекст кейса:
{case_text}

Фрагмент интервью:
{interview_text}

Правила и шкала:
{criteria_text}

Методология оценки:
{evaluation_methodology_text}

Краткая rule-based сводка:
{rule_summary_json}

Текст ответа стажера:
{submission_text}

Требования к JSON:
{{
  "criterion_scores": [
    {{
      "criterion_id": "structure_template",
      "score": 1,
      "rationale": "кратко",
      "evidence": ["фрагмент 1", "фрагмент 2"]
    }}
  ],
  "total_score": 0,
  "max_score": 15,
  "language_check": {{
    "error_count": 0,
    "errors": []
  }},
  "mentor_issues": [
    {{
      "code": "ISSUE_001",
      "category": "content|structure|quality|factual|style|system",
      "severity": "low|medium|high",
      "message": "описание",
      "evidence": "фрагмент",
      "rule_ref": "ссылка на критерий/правило"
    }}
  ],
  "intern_tips": ["проверь ...", "уточни ..."]
}}
""".strip()


def _normalize_llm_payload(payload: dict[str, Any]) -> dict[str, Any]:
    criteria_scores = payload.get("criterion_scores", [])
    if not isinstance(criteria_scores, list):
        criteria_scores = []

    normalized_scores: list[dict[str, Any]] = []
    for item in criteria_scores:
        if not isinstance(item, dict):
            continue
        criterion_id = str(item.get("criterion_id", "")).strip()
        if not criterion_id:
            continue
        raw_score = item.get("score", 0)
        try:
            score = int(raw_score)
        except Exception:
            score = 0
        rationale = str(item.get("rationale", "")).strip()
        evidence_raw = item.get("evidence", [])
        if not isinstance(evidence_raw, list):
            evidence_raw = []
        evidence = [str(x).strip() for x in evidence_raw if str(x).strip()]
        normalized_scores.append(
            {
                "criterion_id": criterion_id,
                "score": score,
                "rationale": rationale,
                "evidence": evidence,
            }
        )

    total_score = sum(int(item.get("score", 0)) for item in normalized_scores)
    max_score = 15

    language_check_raw = payload.get("language_check", {})
    if not isinstance(language_check_raw, dict):
        language_check_raw = {}
    try:
        error_count = max(0, int(language_check_raw.get("error_count", 0)))
    except Exception:
        error_count = 0
    errors_raw = language_check_raw.get("errors", [])
    if not isinstance(errors_raw, list):
        errors_raw = []
    normalized_errors: list[dict[str, str]] = []
    for item in errors_raw:
        if not isinstance(item, dict):
            continue
        fragment = str(item.get("fragment", "")).strip()
        error_type = str(item.get("error_type", "")).strip().lower()
        comment = str(item.get("comment", "")).strip()
        if error_type not in {"spelling", "punctuation", "grammar", "typo", "editing"}:
            error_type = "editing"
        if fragment:
            normalized_errors.append(
                {
                    "fragment": fragment,
                    "error_type": error_type,
                    "comment": comment,
                }
            )

    mentor_issues = payload.get("mentor_issues", [])
    if not isinstance(mentor_issues, list):
        mentor_issues = []
    intern_tips = payload.get("intern_tips", [])
    if not isinstance(intern_tips, list):
        intern_tips = []

    return {
        "criterion_scores": normalized_scores,
        "total_score": total_score,
        "max_score": max_score,
        "language_check": {
            "error_count": error_count,
            "errors": normalized_errors,
        },
        "mentor_issues": mentor_issues,
        "intern_tips": [str(x).strip() for x in intern_tips if str(x).strip()],
    }


def _is_weak_language_fragment(fragment: str) -> bool:
    normalized = re.sub(r"\s+", " ", fragment).strip().lower()
    if not normalized:
        return True

    weak_patterns = [
        r"^в таблица \d+\.?$",
        r"^в таблица \d+\b",
        r"^версии документа представлены в таблица \d+\.?$",
        r"^в \d{1,2}:\d{2}\b",
        r"^требованияавтоматизац",
        r"^функционально-технические требования",
        r"^\d+(?:\.\d+)*\.?\s*[а-яёa-z].*\d+$",
        r"^\d+(?:\.\d+)*\.?[а-яёa-z].*\d+$",
        r"^требования к визуализации$",
    ]
    return any(re.search(pattern, normalized) for pattern in weak_patterns)


def _is_weak_language_error(fragment: str, comment: str) -> bool:
    if _is_weak_language_fragment(fragment):
        return True

    normalized_fragment = re.sub(r"\s+", " ", fragment).strip().lower()
    normalized_comment = re.sub(r"\s+", " ", comment).strip().lower()
    if normalized_fragment.endswith(";") and "отсутств" in normalized_comment and "точк" in normalized_comment:
        return True
    if normalized_comment and "двоеточи" in normalized_comment and len(normalized_fragment.split()) <= 4:
        return True
    return False


def _normalize_language_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    try:
        error_count = max(0, int(payload.get("error_count", 0)))
    except Exception:
        error_count = 0

    errors_raw = payload.get("errors", [])
    if not isinstance(errors_raw, list):
        errors_raw = []

    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in errors_raw:
        if not isinstance(item, dict):
            continue
        fragment = re.sub(r"\s+", " ", str(item.get("fragment", "")).strip())
        if not fragment:
            continue
        comment = str(item.get("comment", "")).strip()
        if _is_weak_language_error(fragment, comment):
            continue
        key = fragment.lower()
        if key in seen:
            continue
        seen.add(key)
        error_type = str(item.get("error_type", "")).strip().lower()
        if error_type not in {"spelling", "punctuation", "grammar", "typo", "editing"}:
            error_type = "editing"
        errors.append(
            {
                "fragment": fragment,
                "error_type": error_type,
                "comment": comment,
            }
        )

    return {
        "error_count": len(errors) if errors_raw else error_count,
        "errors": errors,
    }


def _normalize_structure_template_assist_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    allowed_statuses = {"found", "possible_synonym", "missing", "duplicate", "unclear"}
    mappings_raw = payload.get("section_mappings", [])
    if not isinstance(mappings_raw, list):
        mappings_raw = []

    mappings: list[dict[str, str]] = []
    for item in mappings_raw:
        if not isinstance(item, dict):
            continue
        template_section = re.sub(r"\s+", " ", str(item.get("template_section", "")).strip())
        found_heading = re.sub(r"\s+", " ", str(item.get("found_heading", "")).strip())
        status = str(item.get("status", "")).strip().lower()
        comment = re.sub(r"\s+", " ", str(item.get("comment", "")).strip())
        if not template_section:
            continue
        if status not in allowed_statuses:
            status = "unclear"
        mappings.append(
            {
                "template_section": template_section,
                "found_heading": found_heading,
                "status": status,
                "comment": comment,
            }
        )

    def _string_list(value: Any, limit: int = 12) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        seen: set[str] = set()
        for raw in value:
            text = re.sub(r"\s+", " ", str(raw).strip())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(text)
            if len(result) >= limit:
                break
        return result

    return {
        "section_mappings": mappings,
        "added_sections": _string_list(payload.get("added_sections", [])),
        "manual_review_notes": _string_list(payload.get("manual_review_notes", [])),
        "summary": re.sub(r"\s+", " ", str(payload.get("summary", "")).strip()),
    }


def _normalize_formatting_instruction_payload(payload: dict[str, Any], criterion: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_criterion_payload(payload, criterion)

    decisions_raw = payload.get("rule_decisions", [])
    if not isinstance(decisions_raw, list):
        decisions_raw = []

    rule_decisions: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    for item in decisions_raw:
        if not isinstance(item, dict):
            continue
        rule_id = re.sub(r"\s+", " ", str(item.get("rule_id", "")).strip())
        if not rule_id:
            continue
        key = rule_id.lower()
        if key in seen_rules:
            continue
        seen_rules.add(key)
        status = str(item.get("status", "")).strip().lower()
        if status not in {"pass", "warn", "fail", "needs_review"}:
            status = "needs_review"
        try:
            error_count = max(0, int(item.get("error_count", 0)))
        except Exception:
            error_count = 0
        rule_decisions.append(
            {
                "rule_id": rule_id,
                "status": status,
                "error_count": error_count,
                "systemic": bool(item.get("systemic", False)),
                "comment": re.sub(r"\s+", " ", str(item.get("comment", "")).strip()),
            }
        )

    findings_raw = payload.get("formatting_findings", [])
    if not isinstance(findings_raw, list):
        findings_raw = []

    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in findings_raw:
        if not isinstance(item, dict):
            continue
        fragment = re.sub(r"\s+", " ", str(item.get("fragment", "")).strip())
        rule = re.sub(r"\s+", " ", str(item.get("rule", "")).strip())
        scope = str(item.get("scope", "")).strip().lower()
        kind = str(item.get("kind", "")).strip().lower()
        comment = re.sub(r"\s+", " ", str(item.get("comment", "")).strip())
        if not fragment and not rule:
            continue
        if scope not in {"isolated", "systemic", "unknown"}:
            scope = "unknown"
        if kind not in {"template_explanation", "formatting_error"}:
            kind = "formatting_error"
        key = (fragment.lower(), rule.lower())
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "kind": kind,
                "fragment": fragment,
                "rule": rule,
                "scope": scope,
                "comment": comment,
            }
        )

    normalized["formatting_findings"] = findings
    normalized["rule_decisions"] = rule_decisions
    return normalized


def _normalize_criterion_payload(payload: dict[str, Any], criterion: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}

    criterion_id = str(criterion.get("id", "")).strip()
    raw_score_payload = payload.get("criterion_score")
    if not isinstance(raw_score_payload, dict):
        raw_scores = payload.get("criterion_scores", [])
        if isinstance(raw_scores, list):
            raw_score_payload = next(
                (
                    item
                    for item in raw_scores
                    if isinstance(item, dict) and str(item.get("criterion_id", "")).strip() == criterion_id
                ),
                {},
            )
        else:
            raw_score_payload = {}

    try:
        score = int(raw_score_payload.get("score", 0))
    except Exception:
        score = 0
    max_score = int(criterion.get("weight", 1) or 1)
    score = max(0, min(max_score, score))

    evidence_raw = raw_score_payload.get("evidence", [])
    if not isinstance(evidence_raw, list):
        evidence_raw = []

    mentor_issues = payload.get("mentor_issues", [])
    if not isinstance(mentor_issues, list):
        mentor_issues = []

    intern_tips = payload.get("intern_tips", [])
    if not isinstance(intern_tips, list):
        intern_tips = []

    return {
        "criterion_score": {
            "criterion_id": criterion_id,
            "score": score,
            "rationale": str(raw_score_payload.get("rationale", "")).strip(),
            "evidence": [str(item).strip() for item in evidence_raw if str(item).strip()],
        },
        "mentor_issues": [item for item in mentor_issues if isinstance(item, dict)],
        "intern_tips": [str(item).strip() for item in intern_tips if str(item).strip()],
    }


def _build_prompt(profile: dict[str, Any], submission_text: str, rule_summary: dict[str, Any]) -> str:
    criteria = profile.get("criteria", [])
    criteria_lines = "\n".join(
        f"- {item['id']}: {item['title']} (вес {item['weight']})" for item in criteria
    )

    materials_text = profile.get("materials_text", {})
    case_text = materials_text.get("case", "")[:5000]
    interview_text = materials_text.get("interview", "")[:5000]
    criteria_text = materials_text.get("criteria_2026", "")[:4000]
    evaluation_methodology_text = materials_text.get("evaluation_methodology", "")[:5000]
    template = materials_text.get("llm_prompt_template", "").strip() or _DEFAULT_PROMPT_TEMPLATE

    values = {
        "criteria_lines": criteria_lines,
        "case_text": case_text,
        "interview_text": interview_text,
        "criteria_text": criteria_text,
        "evaluation_methodology_text": evaluation_methodology_text,
        "rule_summary_json": json.dumps(rule_summary, ensure_ascii=False),
        "submission_text": submission_text[:14000],
    }

    def _render(raw_template: str) -> str:
        rendered = raw_template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
        return rendered

    try:
        return _render(template)
    except Exception:
        return _render(_DEFAULT_PROMPT_TEMPLATE)


def _criterion_rule_summary(criterion_id: str, rule_summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rule_summary, dict):
        return {}
    value = rule_summary.get(criterion_id)
    return value if isinstance(value, dict) else {}


def _build_criterion_prompt(
    profile: dict[str, Any],
    criterion: dict[str, Any],
    submission_text: str,
    rule_summary: dict[str, Any],
) -> str:
    materials_text = profile.get("materials_text", {})
    criterion_id = str(criterion.get("id", "")).strip()
    title = str(criterion.get("title", "")).strip()
    weight = int(criterion.get("weight", 1) or 1)

    template_context = ""
    if criterion_id in {
        "structure_template",
        "formatting_instruction",
        "document_versioning",
        "terms_glossary",
        "table_of_contents",
    }:
        template_context = f"""

Структура шаблона:
{materials_text.get("ftt_template", "")[:3500]}

Инструкция к шаблону:
{materials_text.get("template_instruction", "")[:3500]}
""".rstrip()

    return f"""
Роль: строгий эксперт по проверке ФТТ стажеров-аналитиков.

Проверь ТОЛЬКО один критерий. Не оценивай другие критерии и не переноси замечания между критериями.

Критерий:
- id: {criterion_id}
- название: {title}
- максимум баллов: {weight}

Контекст кейса:
{materials_text.get("case", "")[:4500]}

Фрагмент интервью:
{materials_text.get("interview", "")[:4500]}

Правила и шкала:
{materials_text.get("criteria_2026", "")[:4500]}

Методология оценки:
{materials_text.get("evaluation_methodology", "")[:4500]}
{template_context}

Rule-based сводка по этому критерию:
{json.dumps(_criterion_rule_summary(criterion_id, rule_summary), ensure_ascii=False)}

Верни только валидный JSON:
{{
  "criterion_score": {{
    "criterion_id": "{criterion_id}",
    "score": 0,
    "rationale": "краткое объяснение оценки именно по этому критерию",
    "evidence": ["дословный фрагмент из работы 1", "дословный фрагмент из работы 2"]
  }},
  "mentor_issues": [
    {{
      "code": "ISSUE_{criterion_id}",
      "category": "content|structure|quality|factual|style|system",
      "severity": "low|medium|high",
      "message": "замечание для наставника",
      "evidence": "дословный фрагмент",
      "rule_ref": "{criterion_id}"
    }}
  ],
  "intern_tips": ["проверь ...", "уточни ..."]
}}

Шкала: для критерия с максимумом 1 балл используй только 0 или 1; для критерия с максимумом 2 балла используй только 0, 1 или 2.
Evidence должен быть только из текста работы стажера. Если доказательства нет, оставь список пустым.

Текст работы стажера:
{submission_text[:18000]}
""".strip()


def _build_language_check_prompt(submission_text: str) -> str:
    return f"""
Роль: корректор русского языка.

Проверь текст ФТТ стажера ТОЛЬКО по критерию 3 «Орфография и пунктуация».
Ищи ошибки заново в данном тексте по правилам русского языка. Не используй эталонные ответы и не переноси ошибки из других работ.
Эталонный пример задает только стиль оформления ответа; список ошибок всегда ищи заново.

Что считать ошибкой:
- орфография и опечатки;
- пунктуация;
- грамматические ошибки;
- нарушения согласования и управления;
- неверная форма слова;
- лишние/повторяющиеся предлоги или слова;
- отсутствие пробела после знака препинания;
- редакторские ошибки, которые нарушают норму русского языка.

Что НЕ считать ошибкой критерия 3:
- спорные содержательные требования;
- неполный раздел;
- неточные формулы;
- стиль, если он грамматически корректен;
- отличие от эталонного ответа.
- технические артефакты извлечения текста из DOCX: склеенные титульные заголовки, колонтитулы, подписи и ссылки вида «в Таблица 1», если они не являются содержательной фразой работы.

Верни только валидный JSON:
{{
  "error_count": 0,
  "errors": [
    {{
      "fragment": "точный фрагмент из текста с ошибкой",
      "error_type": "spelling|punctuation|grammar|typo|editing",
      "comment": "кратко, что нарушено по русскому языку"
    }}
  ]
}}

Если ошибок 3 и более, приведи все найденные конкретные фрагменты.
Фрагменты должны быть дословно из текста работы и содержать саму ошибку.
Приоритет отдавай содержательным фразам с нарушением управления, согласования, формы слова, повтором предлога/слова или опечаткой. Не возвращай только короткий служебный обрывок, если можно привести полную фразу с ошибкой.

Текст работы:
{submission_text[:18000]}
""".strip()


def _build_structure_template_assist_prompt(
    profile: dict[str, Any],
    submission_text: str,
    rule_summary: dict[str, Any],
) -> str:
    required_sections = profile.get("required_sections", [])
    if not isinstance(required_sections, list):
        required_sections = []
    required_lines = "\n".join(f"- {section}" for section in required_sections)

    aliases = profile.get("rules", {}).get("section_aliases", {})
    if not isinstance(aliases, dict):
        aliases = {}

    return f"""
Роль: помощник наставника по проверке структуры ФТТ.

Проверь ТОЛЬКО критерий 1 «Структура соответствует шаблону или обоснованно дополнена».
Важно: ты НЕ выставляешь балл. Балл уже выставляет rule-based проверка по правилу:
- 1 балл: все разделы шаблона присутствуют, добавленные разделы не дублируют шаблон;
- 0 баллов: отсутствует хотя бы один обязательный раздел.

Твоя задача как вспомогательного слоя:
- найти, является ли нестандартное название раздела синонимом раздела шаблона;
- отметить спорные переименования, дубли разделов и добавленные разделы;
- дать короткие подсказки для ручной проверки, если rule-based мог не узнать синоним.
- сверить свое резюме с rule-based сводкой. Если в rule-based сводке `structure_template.added_sections`
  непустой, в `summary` обязательно упомяни эти добавленные разделы. Запрещено писать, что добавленных
  или дублирующих разделов нет, если rule-based сводка уже нашла добавленные разделы.
- если rule-based сводка содержит `structure_template.neutral_notes`, отрази эти особенности в `summary`
  как особенности, не влияющие на балл.

Обязательные разделы шаблона:
{required_lines}

Уже известные допустимые варианты названий:
{json.dumps(aliases, ensure_ascii=False, indent=2)}

Rule-based сводка:
{json.dumps(rule_summary, ensure_ascii=False)}

Верни только валидный JSON:
{{
  "section_mappings": [
    {{
      "template_section": "раздел шаблона",
      "found_heading": "точная строка/название из работы или пусто",
      "status": "found|possible_synonym|missing|duplicate|unclear",
      "comment": "краткое пояснение"
    }}
  ],
  "added_sections": ["добавленный раздел, если есть"],
  "manual_review_notes": ["короткая подсказка для наставника"],
  "summary": "1-2 предложения. Не ставь балл."
}}

Текст работы стажера:
{submission_text[:18000]}
""".strip()


def _build_formatting_instruction_prompt(
    profile: dict[str, Any],
    criterion: dict[str, Any],
    submission_text: str,
    rule_summary: dict[str, Any],
) -> str:
    materials_text = profile.get("materials_text", {})
    criterion_id = str(criterion.get("id", "formatting_instruction")).strip() or "formatting_instruction"
    title = str(criterion.get("title", "2. Оформление соответствует инструкции и шаблону")).strip()
    criterion_summary = _criterion_rule_summary(criterion_id, rule_summary)

    return f"""
Роль: строгий эксперт по оформлению ФТТ стажеров-аналитиков.

Проверь ТОЛЬКО критерий 2. Не оценивай структуру как критерий 1, содержание разделов как критерии 4.x,
орфографию как критерий 3 и самостоятельность как критерий 4.11.

Критерий:
- id: {criterion_id}
- название: {title}
- максимум баллов: 1

Шкала критерия 2:
- 1 балл: оформление соответствует инструкции; допускаются 1-2 единичные ошибки по документу, если они не системные;
  нет пояснений инструкции к шаблону о том, что должно быть указано в разделе.
- 0 баллов: оформление не соответствует инструкции, если найдено 3 и более ошибок оформления,
  или ошибки системные и встречаются по всему документу,
  или присутствует хотя бы одно пояснение/пример/служебная подсказка из инструкции к шаблону.

Что проверять по инструкции и шаблону:
- не остались ли служебные пояснения, серые подсказки, примеры заполнения, фразы вида "укажите", "заполните",
  "пример", "удалите этот текст", если это не авторский текст стажера;
- соответствует ли документ ожидаемой форме шаблона: таблицы там, где инструкция требует таблицу,
  оглавление похоже на оглавление, разделы оформлены как разделы, нет хаотичной нумерации и смешения уровней;
- есть ли системные нарушения оформления, которые повторяются по всему документу.

Важно:
- Если видишь только 1-2 единичные ошибки оформления и нет пояснений шаблона, поставь 1.
- Если есть хотя бы одно пояснение из шаблона о том, что должно быть указано в разделе, поставь 0.
- Не снимай балл за содержательную неполноту раздела, если это относится к критериям 4.x.
- Evidence должен быть только дословным фрагментом из работы стажера.

Инструкция к шаблону:
{materials_text.get("template_instruction", "")[:6500]}

Чеклист критерия 2:
{materials_text.get("formatting_checklist", "")[:6500]}

Структура шаблона:
{materials_text.get("ftt_template", "")[:5000]}

Компактная таблица фактов по инструкции и DOCX-метаданным:
{json.dumps(criterion_summary, ensure_ascii=False, indent=2)}

Используй для evidence только фрагменты из `template_hint_evidence`, `checklist_facts.checklist[*].evidence`
и `checklist_facts.compact_samples`. Полный текст работы в этот проход намеренно не передается, чтобы
не размывать проверку критерия 2 и не перегружать контекст.

Верни только валидный JSON:
{{
  "criterion_score": {{
    "criterion_id": "{criterion_id}",
    "score": 0,
    "rationale": "краткое объяснение оценки именно по критерию 2",
    "evidence": ["дословный фрагмент из работы 1", "дословный фрагмент из работы 2"]
  }},
  "rule_decisions": [
    {{
      "rule_id": "font_verdana",
      "status": "pass|warn|fail|needs_review",
      "error_count": 0,
      "systemic": false,
      "comment": "краткий вывод по правилу"
    }}
  ],
  "formatting_findings": [
    {{
      "kind": "template_explanation|formatting_error",
      "fragment": "дословный фрагмент из работы или пусто",
      "rule": "какое правило инструкции нарушено",
      "scope": "isolated|systemic|unknown",
      "comment": "кратко почему это ошибка оформления"
    }}
  ],
  "mentor_issues": [
    {{
      "code": "ISSUE_{criterion_id}",
      "category": "structure|quality|system",
      "severity": "low|medium|high",
      "message": "замечание для наставника",
      "evidence": "дословный фрагмент",
      "rule_ref": "{criterion_id}"
    }}
  ],
  "intern_tips": ["проверь оформление ..."]
}}

Если фактов недостаточно для уверенного вывода, используй `score: 1` только когда нет блокирующих сигналов
и нет 3+ потенциальных ошибок в `decision_hint`; в rationale явно укажи, что проверка основана на метаданных DOCX.
""".strip()


def _response_text(response: Any) -> str:
    text = (getattr(response, "output_text", "") or "").strip()
    if text:
        return text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_text = getattr(content, "text", None)
            if isinstance(content_text, str) and content_text.strip():
                chunks.append(content_text.strip())
    return "\n".join(chunks).strip()


def _parse_llm_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Пустой ответ модели")

    candidates = [stripped]
    fenced_blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", stripped, flags=re.IGNORECASE)
    for block in fenced_blocks:
        block = block.strip()
        if block:
            candidates.append(block)

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for marker in ("{", "["):
        idx = stripped.find(marker)
        while idx != -1:
            try:
                payload, _ = decoder.raw_decode(stripped[idx:])
                if isinstance(payload, dict):
                    return payload
            except json.JSONDecodeError:
                pass
            idx = stripped.find(marker, idx + 1)

    raise ValueError("Ответ модели не удалось разобрать как JSON-объект")


def _system_trust_http_client() -> Any | None:
    try:
        import ssl

        import httpx
        import truststore
    except Exception:
        return None

    return httpx.Client(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


def _openai_client_kwargs(
    api_key: str,
    *,
    base_url_env: str = "OPENAI_BASE_URL",
    default_base_url: str | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"api_key": api_key}
    http_client = _system_trust_http_client()
    if http_client is not None:
        kwargs["http_client"] = http_client
    base_url = os.getenv(base_url_env, "").strip() or (default_base_url or "")
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")
    return kwargs


def _openai_key_candidates() -> list[dict[str, str]]:
    candidates: list[tuple[str, str | None]] = [
        ("OPENAI_EDU_API_KEY", os.getenv("OPENAI_EDU_API_KEY")),
        ("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY")),
    ]
    result: list[dict[str, str]] = []
    seen_values: set[str] = set()
    for name, raw_value in candidates:
        value = (raw_value or "").strip()
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        result.append({"name": name, "api_key": value})
    return result


def _is_likely_key_or_quota_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403, 429}:
        return True

    code = str(getattr(exc, "code", "") or "").lower()
    text = f"{code} {exc}".lower()
    fallback_markers = [
        "invalid_api_key",
        "insufficient_quota",
        "billing_hard_limit",
        "rate_limit",
        "quota",
        "permission",
        "unauthorized",
        "forbidden",
    ]
    return any(marker in text for marker in fallback_markers)


def _run_llm_assessment_with_client(
    client: Any,
    model: str,
    profile: dict[str, Any],
    submission_text: str,
    rule_summary: dict[str, Any],
) -> dict[str, Any]:
    try:
        criteria = profile.get("criteria", [])
        if not isinstance(criteria, list):
            criteria = []

        payload: dict[str, Any] = {
            "criterion_scores": [],
            "total_score": 0,
            "max_score": sum(int(item.get("weight", 0) or 0) for item in criteria if isinstance(item, dict)),
            "language_check": {"error_count": 0, "errors": []},
            "mentor_issues": [],
            "intern_tips": [],
            "assessment_mode": "staged_by_criterion",
            "assessment_sequence": [],
            "stage_errors": [],
        }

        criteria_by_id = {
            str(item.get("id", "")).strip(): item
            for item in criteria
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        }

        try:
            structure_response = client.responses.create(
                model=model,
                input=_build_structure_template_assist_prompt(profile, submission_text, rule_summary),
                temperature=0,
            )
            structure_text = _response_text(structure_response)
            payload["structure_template_assist"] = _normalize_structure_template_assist_payload(
                _parse_llm_payload(structure_text)
            )
            payload["assessment_sequence"].append("structure_template_assist")
        except Exception as exc:
            if _is_likely_key_or_quota_error(exc):
                return {"status": "error", "reason": f"LLM-сбой: {exc}", "retry_with_next_key": True}
            # The structure score remains deterministic; this focused pass only adds hints.
            payload["stage_errors"].append(
                {
                    "criterion_id": "structure_template_assist",
                    "reason": str(exc),
                }
            )

        formatting_criterion = criteria_by_id.get("formatting_instruction")
        if formatting_criterion:
            try:
                formatting_response = client.responses.create(
                    model=model,
                    input=_build_formatting_instruction_prompt(
                        profile,
                        formatting_criterion,
                        submission_text,
                        rule_summary,
                    ),
                    temperature=0,
                )
                formatting_text = _response_text(formatting_response)
                formatting_payload = _normalize_formatting_instruction_payload(
                    _parse_llm_payload(formatting_text),
                    formatting_criterion,
                )
                payload["criterion_scores"].append(formatting_payload["criterion_score"])
                payload["formatting_instruction_check"] = {
                    "rule_decisions": formatting_payload["rule_decisions"],
                    "formatting_findings": formatting_payload["formatting_findings"],
                }
                payload["mentor_issues"].extend(formatting_payload["mentor_issues"])
                payload["intern_tips"].extend(formatting_payload["intern_tips"])
                payload["assessment_sequence"].append("formatting_instruction")
            except Exception as exc:
                if _is_likely_key_or_quota_error(exc):
                    return {"status": "error", "reason": f"LLM-сбой: {exc}", "retry_with_next_key": True}
                payload["stage_errors"].append(
                    {
                        "criterion_id": "formatting_instruction",
                        "reason": str(exc),
                    }
                )

        for criterion in criteria:
            if not isinstance(criterion, dict):
                continue
            criterion_id = str(criterion.get("id", "")).strip()
            if not criterion_id:
                continue
            if criterion_id in {"structure_template", "formatting_instruction"}:
                continue
            try:
                response = client.responses.create(
                    model=model,
                    input=_build_criterion_prompt(profile, criterion, submission_text, rule_summary),
                    temperature=0.1,
                )
                text = _response_text(response)
                criterion_payload = _normalize_criterion_payload(_parse_llm_payload(text), criterion)
                score = criterion_payload["criterion_score"]
                payload["criterion_scores"].append(score)
                payload["mentor_issues"].extend(criterion_payload["mentor_issues"])
                payload["intern_tips"].extend(criterion_payload["intern_tips"])
                payload["assessment_sequence"].append(criterion_id)
            except Exception as exc:
                if _is_likely_key_or_quota_error(exc):
                    return {"status": "error", "reason": f"LLM-сбой: {exc}", "retry_with_next_key": True}
                payload["stage_errors"].append(
                    {
                        "criterion_id": criterion_id,
                        "reason": str(exc),
                    }
                )

        payload["total_score"] = sum(int(item.get("score", 0)) for item in payload["criterion_scores"])

        try:
            language_response = client.responses.create(
                model=model,
                input=_build_language_check_prompt(submission_text),
                temperature=0,
            )
            language_text = _response_text(language_response)
            payload["language_check"] = _normalize_language_payload(_parse_llm_payload(language_text))
            payload["assessment_sequence"].append("literacy_punctuation_language_check")
        except Exception as exc:
            if _is_likely_key_or_quota_error(exc):
                return {"status": "error", "reason": f"LLM-сбой: {exc}", "retry_with_next_key": True}
            payload["stage_errors"].append(
                {
                    "criterion_id": "literacy_punctuation_language_check",
                    "reason": "Отдельная языковая проверка не выполнена",
                }
            )
        if not payload["criterion_scores"] and payload["stage_errors"]:
            return {
                "status": "error",
                "reason": f"LLM-сбой: не удалось выполнить ни один этап ({len(payload['stage_errors'])} ошибок)",
                "model": model,
                "assessment_sequence": payload["assessment_sequence"],
                "stage_errors": payload["stage_errors"],
            }
        return {"status": "ok", "model": model, "payload": payload}
    except Exception as exc:
        return {"status": "error", "reason": f"LLM-сбой: {exc}"}


def run_llm_assessment(
    profile: dict[str, Any],
    submission_text: str,
    rule_summary: dict[str, Any],
) -> dict[str, Any]:
    key_candidates = _openai_key_candidates()
    if not key_candidates:
        return {"status": "skipped", "reason": "OPENAI_EDU_API_KEY и OPENAI_API_KEY не заданы"}

    try:
        from openai import OpenAI
    except Exception:
        return {"status": "skipped", "reason": "Пакет openai не установлен"}

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    failed_attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {}

    for key_candidate in key_candidates:
        key_name = key_candidate["name"]
        client_kwargs: dict[str, Any] = {}
        try:
            base_url_env = "OPENAI_EDU_BASE_URL" if key_name == "OPENAI_EDU_API_KEY" else "OPENAI_BASE_URL"
            default_base_url = "https://api.openai.com/v1" if key_name == "OPENAI_EDU_API_KEY" else None
            client_kwargs = _openai_client_kwargs(
                key_candidate["api_key"],
                base_url_env=base_url_env,
                default_base_url=default_base_url,
            )
            client = OpenAI(**client_kwargs)
            result = _run_llm_assessment_with_client(
                client=client,
                model=model,
                profile=profile,
                submission_text=submission_text,
                rule_summary=rule_summary,
            )
        except Exception as exc:
            result = {"status": "error", "reason": f"LLM-сбой: {exc}"}

        last_result = result
        result["api_key_source"] = key_name
        if "base_url" in client_kwargs:
            result["base_url"] = client_kwargs["base_url"]

        if result.get("status") == "ok":
            if failed_attempts:
                result["fallback_attempts"] = failed_attempts
            return result

        failed_attempt: dict[str, Any] = {"api_key_source": key_name, "reason": str(result.get("reason", ""))}
        if result.get("stage_errors"):
            failed_attempt["stage_errors"] = result["stage_errors"]
        if result.get("assessment_sequence"):
            failed_attempt["assessment_sequence"] = result["assessment_sequence"]
        if result.get("base_url"):
            failed_attempt["base_url"] = result["base_url"]
        failed_attempts.append(failed_attempt)
        if not result.get("retry_with_next_key") and key_name == "OPENAI_API_KEY":
            break

    final_result = {
        "status": "error",
        "reason": failed_attempts[-1]["reason"] if failed_attempts else "LLM-сбой: ключи не сработали",
        "fallback_attempts": failed_attempts,
    }
    for key in ("model", "api_key_source", "base_url", "assessment_sequence", "stage_errors"):
        if key in last_result:
            final_result[key] = last_result[key]
    return final_result
