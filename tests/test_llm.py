from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from review_agent.config import load_profile
from review_agent.llm import _build_criterion_prompt
from review_agent.llm import _build_formatting_instruction_prompt
from review_agent.llm import _build_language_check_prompt
from review_agent.llm import _build_prompt
from review_agent.llm import _build_structure_template_assist_prompt
from review_agent.llm import _normalize_criterion_payload
from review_agent.llm import _normalize_formatting_instruction_payload
from review_agent.llm import _normalize_language_payload
from review_agent.llm import _normalize_structure_template_assist_payload
from review_agent.llm import _openai_client_kwargs
from review_agent.llm import _openai_key_candidates
from review_agent.llm import _parse_llm_payload
from review_agent.llm import run_llm_assessment


def test_openai_client_kwargs_uses_configured_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", " https://proxy.example/v1/ ")
    monkeypatch.setattr("review_agent.llm._system_trust_http_client", lambda: None)

    kwargs = _openai_client_kwargs("test-key")

    assert kwargs["api_key"] == "test-key"
    assert kwargs["base_url"] == "https://proxy.example/v1"


def test_openai_client_kwargs_omits_empty_base_url(monkeypatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr("review_agent.llm._system_trust_http_client", lambda: None)

    assert _openai_client_kwargs("test-key") == {"api_key": "test-key"}


def test_openai_client_kwargs_uses_key_specific_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.delenv("OPENAI_EDU_BASE_URL", raising=False)
    monkeypatch.setattr("review_agent.llm._system_trust_http_client", lambda: None)

    assert _openai_client_kwargs(
        "edu-key",
        base_url_env="OPENAI_EDU_BASE_URL",
        default_base_url="https://api.openai.com/v1",
    ) == {"api_key": "edu-key", "base_url": "https://api.openai.com/v1"}


def test_openai_client_kwargs_prefers_explicit_edu_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("OPENAI_EDU_BASE_URL", " https://edu-proxy.example/v1/ ")
    monkeypatch.setattr("review_agent.llm._system_trust_http_client", lambda: None)

    assert _openai_client_kwargs(
        "edu-key",
        base_url_env="OPENAI_EDU_BASE_URL",
        default_base_url="https://api.openai.com/v1",
    ) == {"api_key": "edu-key", "base_url": "https://edu-proxy.example/v1"}


def test_openai_key_candidates_prefers_edu_key(monkeypatch):
    monkeypatch.setenv("OPENAI_EDU_API_KEY", "edu-key")
    monkeypatch.setenv("OPENAI_API_KEY", "commercial-key")

    assert _openai_key_candidates() == [
        {"name": "OPENAI_EDU_API_KEY", "api_key": "edu-key"},
        {"name": "OPENAI_API_KEY", "api_key": "commercial-key"},
    ]


def test_openai_key_candidates_deduplicates_same_key(monkeypatch):
    monkeypatch.setenv("OPENAI_EDU_API_KEY", "same-key")
    monkeypatch.setenv("OPENAI_API_KEY", "same-key")

    assert _openai_key_candidates() == [{"name": "OPENAI_EDU_API_KEY", "api_key": "same-key"}]


def test_run_llm_assessment_falls_back_to_commercial_key(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.api_key = kwargs["api_key"]

    calls = []

    def fake_run_with_client(client, model, profile, submission_text, rule_summary):
        calls.append(client.api_key)
        if client.api_key == "edu-key":
            return {"status": "error", "reason": "quota exceeded", "retry_with_next_key": True}
        return {"status": "ok", "model": model, "payload": {"criterion_scores": []}}

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr("review_agent.llm._run_llm_assessment_with_client", fake_run_with_client)
    monkeypatch.setenv("OPENAI_EDU_API_KEY", "edu-key")
    monkeypatch.setenv("OPENAI_API_KEY", "commercial-key")

    result = run_llm_assessment(profile={}, submission_text="", rule_summary={})

    assert calls == ["edu-key", "commercial-key"]
    assert result["status"] == "ok"
    assert result["api_key_source"] == "OPENAI_API_KEY"
    assert result["fallback_attempts"] == [
        {"api_key_source": "OPENAI_EDU_API_KEY", "reason": "quota exceeded"}
    ]


def test_parse_llm_payload_plain_json():
    payload = _parse_llm_payload('{"criterion_scores":[],"mentor_issues":[],"intern_tips":[]}')
    assert payload["criterion_scores"] == []


def test_parse_llm_payload_markdown_fence():
    payload = _parse_llm_payload(
        """```json
{
  "criterion_scores": [],
  "mentor_issues": [],
  "intern_tips": []
}
```"""
    )
    assert payload["mentor_issues"] == []


def test_parse_llm_payload_wrapped_text():
    payload = _parse_llm_payload(
        """Вот результат:

```json
{"criterion_scores":[],"mentor_issues":[],"intern_tips":[]}
```

Готово."""
    )
    assert payload["intern_tips"] == []


def test_parse_llm_payload_empty_raises():
    with pytest.raises(ValueError, match="Пустой ответ модели"):
        _parse_llm_payload("   ")


def test_build_prompt_includes_evaluation_methodology():
    profile = load_profile("analysts_2026_requirements")

    prompt = _build_prompt(
        profile=profile,
        submission_text="Текст работы стажера",
        rule_summary={},
    )

    assert "Методология оценки по эталонному примеру Дубровской" in prompt
    assert "Не снижать балл за отсутствие ER-диаграммы" in prompt
    assert "ПРАВИЛА ОЦЕНКИ ПО ПРОБЛЕМНЫМ КРИТЕРИЯМ" in prompt
    assert "Нужно сравнить используемые в работе специфические термины" in prompt


def test_build_criterion_prompt_is_focused_on_one_criterion():
    profile = load_profile("analysts_2026_requirements")
    criterion = {
        "id": "data_requirements",
        "title": "4.7 Требования к данным",
        "weight": 1,
    }

    prompt = _build_criterion_prompt(
        profile=profile,
        criterion=criterion,
        submission_text="Текст работы стажера",
        rule_summary={"data_requirements": {"score": 0}},
    )

    assert "Проверь ТОЛЬКО один критерий" in prompt
    assert "data_requirements" in prompt
    assert '"criterion_score"' in prompt
    assert '"criterion_scores"' not in prompt


def test_normalize_criterion_payload_clamps_score_to_weight():
    payload = _normalize_criterion_payload(
        {
            "criterion_score": {
                "criterion_id": "current_situation",
                "score": 5,
                "rationale": "ok",
                "evidence": [" фрагмент "],
            },
            "mentor_issues": [{"code": "ISSUE"}],
            "intern_tips": [" проверь раздел "],
        },
        {"id": "current_situation", "weight": 2},
    )

    assert payload["criterion_score"]["score"] == 2
    assert payload["criterion_score"]["evidence"] == ["фрагмент"]
    assert payload["mentor_issues"] == [{"code": "ISSUE"}]
    assert payload["intern_tips"] == ["проверь раздел"]


def test_language_check_prompt_requires_fresh_russian_check():
    prompt = _build_language_check_prompt("Текст работы")

    assert "Ищи ошибки заново в данном тексте по правилам русского языка" in prompt
    assert "Не используй эталонные ответы" in prompt
    assert "Что НЕ считать ошибкой критерия 3" in prompt


def test_structure_template_assist_prompt_does_not_ask_for_score():
    profile = load_profile("analysts_2026_requirements")
    prompt = _build_structure_template_assist_prompt(
        profile=profile,
        submission_text="Раздел: Роли и доступы",
        rule_summary={"missing_sections": ["Требования к безопасности и разграничению доступа"]},
    )

    assert "ты НЕ выставляешь балл" in prompt
    assert "синонимом раздела шаблона" in prompt
    assert "possible_synonym" in prompt
    assert "Не ставь балл" in prompt
    assert "structure_template.added_sections" in prompt
    assert "Запрещено писать, что добавленных" in prompt


def test_formatting_instruction_prompt_uses_dedicated_scale_and_instruction():
    profile = load_profile("analysts_2026_requirements")
    criterion = {
        "id": "formatting_instruction",
        "title": "2. Оформление соответствует инструкции и шаблону",
        "weight": 1,
    }

    prompt = _build_formatting_instruction_prompt(
        profile=profile,
        criterion=criterion,
        submission_text="Текст работы стажера",
        rule_summary={"formatting_instruction": {"template_hint_hits": 1}},
    )

    assert "Проверь ТОЛЬКО критерий 2" in prompt
    assert "1-2 единичные ошибки" in prompt
    assert "3 и более ошибок оформления" in prompt
    assert "хотя бы одно пояснение" in prompt
    assert "Инструкция к шаблону" in prompt
    assert "Чеклист критерия 2" in prompt
    assert "Компактная таблица фактов" in prompt
    assert '"formatting_findings"' in prompt
    assert "Не оценивай структуру как критерий 1" in prompt
    assert "Полный текст работы в этот проход намеренно не передается" in prompt
    assert "Текст работы стажера" not in prompt


def test_normalize_formatting_instruction_payload_deduplicates_findings():
    payload = _normalize_formatting_instruction_payload(
        {
            "criterion_score": {
                "criterion_id": "formatting_instruction",
                "score": 1,
                "rationale": "Есть только единичная ошибка.",
                "evidence": [" фрагмент "],
            },
            "rule_decisions": [
                {
                    "rule_id": "font_verdana",
                    "status": "bad",
                    "error_count": "2",
                    "systemic": False,
                    "comment": "единичные фрагменты",
                },
                {"rule_id": "font_verdana", "status": "fail"},
            ],
            "formatting_findings": [
                {
                    "kind": "template_explanation",
                    "fragment": "Укажите цели раздела",
                    "rule": "Нет пояснений шаблона",
                    "scope": "systemic",
                    "comment": "Осталась подсказка",
                },
                {
                    "kind": "bad",
                    "fragment": "Укажите цели раздела",
                    "rule": "Нет пояснений шаблона",
                    "scope": "bad",
                    "comment": "дубль",
                },
            ],
            "mentor_issues": [{"code": "ISSUE_formatting_instruction"}],
            "intern_tips": [" проверь шаблонные подсказки "],
        },
        {"id": "formatting_instruction", "weight": 1},
    )

    assert payload["criterion_score"]["score"] == 1
    assert payload["criterion_score"]["evidence"] == ["фрагмент"]
    assert payload["rule_decisions"] == [
        {
            "rule_id": "font_verdana",
            "status": "needs_review",
            "error_count": 2,
            "systemic": False,
            "comment": "единичные фрагменты",
        }
    ]
    assert payload["formatting_findings"] == [
        {
            "kind": "template_explanation",
            "fragment": "Укажите цели раздела",
            "rule": "Нет пояснений шаблона",
            "scope": "systemic",
            "comment": "Осталась подсказка",
        }
    ]
    assert payload["intern_tips"] == ["проверь шаблонные подсказки"]


def test_normalize_structure_template_assist_payload():
    payload = _normalize_structure_template_assist_payload(
        {
            "section_mappings": [
                {
                    "template_section": "Требования к безопасности и разграничению доступа",
                    "found_heading": "Роли и доступы",
                    "status": "possible_synonym",
                    "comment": "По смыслу похоже на раздел безопасности",
                },
                {
                    "template_section": "Требования к данным",
                    "found_heading": "",
                    "status": "unexpected_status",
                    "comment": "",
                },
                {"template_section": ""},
            ],
            "added_sections": ["  Приложение А  ", "Приложение А"],
            "manual_review_notes": [" Проверить, не дублирует ли раздел шаблон. "],
            "summary": "  Есть спорный синоним.  ",
        }
    )

    assert payload["section_mappings"][0]["status"] == "possible_synonym"
    assert payload["section_mappings"][1]["status"] == "unclear"
    assert payload["added_sections"] == ["Приложение А"]
    assert payload["manual_review_notes"] == ["Проверить, не дублирует ли раздел шаблон."]
    assert payload["summary"] == "Есть спорный синоним."


def test_normalize_language_payload_deduplicates_and_counts_errors():
    payload = _normalize_language_payload(
        {
            "error_count": 4,
            "errors": [
                {"fragment": "требованияавтоматизация", "error_type": "editing", "comment": "артефакт"},
                {"fragment": "в Таблица 1.", "error_type": "grammar", "comment": "слабый фрагмент"},
                {"fragment": "должны производится", "error_type": "grammar", "comment": "форма глагола"},
                {"fragment": "должны производится", "error_type": "grammar", "comment": "дубль"},
                {"fragment": "доступ к ко всем данным", "error_type": "editing", "comment": "лишний предлог"},
            ],
        }
    )

    assert payload["error_count"] == 2
    assert [item["fragment"] for item in payload["errors"]] == [
        "должны производится",
        "доступ к ко всем данным",
    ]


def test_normalize_language_payload_filters_docx_extraction_artifacts():
    payload = _normalize_language_payload(
        {
            "error_count": 6,
            "errors": [
                {
                    "fragment": "Функционально-технические требованияНаименование проекта/системы",
                    "error_type": "typo",
                    "comment": "Отсутствует пробел",
                },
                {
                    "fragment": "Версии документа представлены в Таблица 1.",
                    "error_type": "grammar",
                    "comment": "Неверная форма слова",
                },
                {
                    "fragment": "1.1.Полное наименование системы и ее условное обозначение4",
                    "error_type": "punctuation",
                    "comment": "Нет пробела",
                },
                {
                    "fragment": "сокращение времени на сбор данных;",
                    "error_type": "punctuation",
                    "comment": "Отсутствует точка",
                },
                {
                    "fragment": "в ручную по запросу",
                    "error_type": "spelling",
                    "comment": "Нужно писать слитно",
                },
            ],
        }
    )

    assert payload["error_count"] == 1
    assert payload["errors"][0]["fragment"] == "в ручную по запросу"


def test_normalize_language_payload_does_not_limit_errors():
    payload = _normalize_language_payload(
        {
            "error_count": 0,
            "errors": [
                {"fragment": f"ошибка номер {idx}", "error_type": "editing", "comment": "x"}
                for idx in range(15)
            ],
        }
    )

    assert payload["error_count"] == 15
    assert len(payload["errors"]) == 15
