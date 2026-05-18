from __future__ import annotations

import json

from review_agent.reporting import build_criteria_view
from review_agent.reporting import build_llm_status_view
from review_agent.reporting import build_markdown_report
from review_agent.reporting import save_outputs


def test_save_outputs_writes_json_and_markdown(tmp_path):
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "overall_score": 88.0,
        "level": "strong",
        "level_note": "test",
        "criteria": [{"title": "Полнота", "weight": 20, "score": 90}],
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": ["Уточни метрики."]},
        "ai_risk_signals": [],
    }

    json_path, md_path = save_outputs(payload, str(tmp_path), "report")

    assert json_path.exists()
    assert md_path.exists()
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "ok"
    assert "# Отчет проверки" in md_path.read_text(encoding="utf-8")


def test_llm_status_view_explains_ok_status():
    view = build_llm_status_view(
        {
            "llm_status": {
                "status": "ok",
                "model": "gpt-4o-mini",
                "api_key_source": "OPENAI_API_KEY",
                "base_url": "https://api.proxyapi.ru/openai/v1",
                "payload": {
                    "assessment_sequence": [
                        "structure_template_assist",
                        "formatting_instruction",
                        "business_goals",
                        "literacy_punctuation_language_check",
                    ],
                    "stage_errors": [],
                },
                "fallback_attempts": [
                    {"api_key_source": "OPENAI_EDU_API_KEY", "reason": "quota exceeded"}
                ],
            }
        }
    )

    assert view["status_label"] == "Работает"
    assert "выполнено 4 из 4" in view["headline"]
    assert "Модель: gpt-4o-mini" in view["checks"]
    assert "Ключ: OPENAI_API_KEY" in view["checks"]
    assert "Ошибок LLM-этапов нет" in view["checks"]
    assert view["successful_stages"][-1] == "Язык и пунктуация"
    assert view["fallback_attempts"][0]["api_key_source"] == "OPENAI_EDU_API_KEY"


def test_llm_status_view_explains_stage_errors():
    view = build_llm_status_view(
        {
            "llm_status": {
                "status": "error",
                "reason": "LLM-сбой: не удалось выполнить ни один этап (2 ошибок)",
                "model": "gpt-4o-mini",
                "api_key_source": "OPENAI_API_KEY",
                "stage_errors": [
                    {"criterion_id": "formatting_instruction", "reason": "Connection error"},
                    {"criterion_id": "business_goals", "reason": "Connection error"},
                ],
            }
        }
    )

    assert view["status_label"] == "Ошибка"
    assert "успешно 0 из 2" in view["headline"]
    assert view["failed_count"] == 2
    assert view["stage_errors"][0] == {
        "stage_id": "formatting_instruction",
        "stage": "Оформление",
        "reason": "Connection error",
    }


def test_markdown_uses_mentor_style_comment_with_evidence():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "intern_fio": "Дубровская А. В.",
        "level": "strong",
        "level_note": "test",
        "criteria": [
            {
                "title": "4.5 Цели создания системы",
                "weight": 1,
                "score": 100,
                "rationale": "Цели сформулированы через бизнес-результат",
                "evidence": [
                    "сокращение времени на подготовку отчетности",
                    "ускорение принятия решений",
                ],
            }
        ],
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": []},
        "ai_risk_signals": [],
    }

    report = build_markdown_report(payload)

    assert "Почему такая оценка с примерами из текста" in report
    assert "Балл поставлен, потому что цели сформулированы через бизнес-результат." in report
    assert "Примеры из текста: «сокращение времени на подготовку отчетности»" in report


def test_structure_template_comment_is_not_wrapped_in_generic_phrase():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "level": "strong",
        "level_note": "test",
        "criteria": [
            {
                "criterion_id": "structure_template",
                "title": "1. Структура соответствует шаблону",
                "weight": 1,
                "score": 100,
                "rationale": (
                    "Структура соответствует шаблону: есть «Сведения о документе»; "
                    "«Термины, понятия и сокращения». Разделы шаблона не потеряны. "
                    "Критичные отклонения, влияющие на балл: не выявлены. "
                    "Особенности структуры, не влияющие на балл: не выявлены."
                ),
                "evidence": ["Сведения о документе", "Термины, понятия и сокращения"],
            }
        ],
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": []},
        "ai_risk_signals": [],
    }

    report = build_markdown_report(payload)

    assert "Структура соответствует шаблону: есть «Сведения о документе»" in report
    assert "Критичные отклонения, влияющие на балл: не выявлены." in report
    assert "Особенности структуры, не влияющие на балл: не выявлены." in report
    assert "Балл поставлен, потому что структура соответствует шаблону" not in report


def test_structure_template_view_groups_mentor_comment_for_web():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "criteria": [
            {
                "criterion_id": "structure_template",
                "title": "1. Структура соответствует шаблону",
                "weight": 1,
                "score": 100,
                "rationale": "Структура соответствует шаблону.",
                "evidence": ["Сведения о документе"],
            }
        ],
        "coverage_summary": {
            "structure_template": {
                "found_sections": ["Сведения о документе", "Оглавление"],
                "found_section_headings": ["СВЕДЕНИЯ О ДОКУМЕНТЕ", "ОГЛАВЛЕНИЕ"],
                "missing_sections": [],
                "critical_deviations": [],
                "neutral_notes": [
                    "добавлены разделы вне шаблона: «Открытые вопросы»",
                ],
                "added_sections": ["Открытые вопросы"],
                "pre_toc_sections_in_toc": ["Сведения о документе"],
                "body_not_in_toc": [],
                "toc_not_in_body": [],
            }
        },
        "llm_status": {
            "status": "ok",
            "payload": {
                "structure_template_assist": {
                    "summary": "Добавленный раздел не дублирует шаблон.",
                    "manual_review_notes": ["Проверить обоснованность добавления."],
                }
            },
        },
    }

    row = build_criteria_view(payload)[0]
    view = row["structure_view"]

    assert view["status_label"] == "Зачтено"
    assert view["found_count"] == 2
    assert view["found_sections"] == ["СВЕДЕНИЯ О ДОКУМЕНТЕ", "ОГЛАВЛЕНИЕ"]
    assert view["critical_deviations"] == ["не выявлены"]
    assert "Открытые вопросы" in view["neutral_notes"][0]
    assert view["llm_summary"] == "Добавленный раздел не дублирует шаблон."
    assert any("дублируют разделы шаблона" in item for item in view["quick_checks"])


def test_markdown_masks_comment_score_contradiction():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "level": "strong",
        "level_note": "test",
        "criteria": [
            {
                "title": "2. Оформление соответствует инструкции и шаблону",
                "weight": 1,
                "score": 100,
                "rationale": "Оформление не соответствует инструкции.",
                "evidence": ["Разделы оформлены по шаблону"],
            }
        ],
        "coverage_summary": {
            "formatting_instruction": {
                "checklist_facts": {
                    "checklist": [
                        {
                            "id": "no_template_explanations",
                            "rule": "В финальной версии нет служебных пояснений.",
                            "status": "pass",
                            "blocking": False,
                            "error_count": 0,
                            "systemic": False,
                            "evidence": [],
                        },
                        {
                            "id": "table_caption_formatting",
                            "rule": "Подпись таблицы оформлена по инструкции.",
                            "status": "needs_review",
                            "blocking": False,
                            "error_count": 0,
                            "systemic": False,
                            "evidence": ["требует ручной сверки"],
                        },
                    ]
                }
            }
        },
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": []},
        "ai_risk_signals": [],
    }

    report = build_markdown_report(payload)

    assert "Балл поставлен, потому что оформление не соответствует инструкции" not in report
    assert "Балл поставлен: по критерию 2 оформление принято как соответствующее инструкции" in report
    assert "Автоматическое пояснение конфликтует с баллом" not in report


def test_formatting_comment_for_zero_score_lists_reasons():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "level": "medium",
        "level_note": "test",
        "criteria": [
            {
                "criterion_id": "formatting_instruction",
                "title": "2. Оформление соответствует инструкции и шаблону",
                "weight": 1,
                "score": 0,
                "rationale": "Оформление проверено по чеклисту.",
                "evidence": [],
            }
        ],
        "coverage_summary": {
            "formatting_instruction": {
                "checklist_facts": {
                    "checklist": [
                        {
                            "id": "font_verdana",
                            "rule": "При оформлении документа необходимо использовать шрифт Verdana.",
                            "status": "fail",
                            "blocking": False,
                            "error_count": 3,
                            "systemic": True,
                            "evidence": ["Arial: текст раздела"],
                        }
                    ]
                }
            }
        },
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": []},
        "ai_risk_signals": [],
    }

    report = build_markdown_report(payload)

    assert "Балл снят: по чеклисту критерия 2 найдены блокирующие или системные нарушения оформления" in report
    assert "шрифт Verdana: Arial: текст раздела" in report


def test_formatting_view_groups_rules_for_web():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "criteria": [
            {
                "criterion_id": "formatting_instruction",
                "title": "2. Оформление соответствует инструкции и шаблону",
                "weight": 1,
                "score": 100,
                "rationale": "ok",
                "evidence": [],
            }
        ],
        "coverage_summary": {
            "formatting_instruction": {
                "checklist_facts": {
                    "metadata_summary": {
                        "table_count": 3,
                        "font_checked_text_runs": 20,
                        "non_verdana_count": 0,
                        "toc_like_line_count": 8,
                    },
                    "checklist": [
                        {
                            "id": "font_verdana",
                            "rule": "При оформлении документа необходимо использовать шрифт Verdana.",
                            "status": "pass",
                            "blocking": False,
                            "error_count": 0,
                            "systemic": False,
                            "evidence": [],
                        },
                        {
                            "id": "toc_format",
                            "rule": "Оглавление содержит разделы до 3 уровня и номера страниц.",
                            "status": "warn",
                            "blocking": False,
                            "error_count": 1,
                            "systemic": False,
                            "evidence": ["Не найдено достаточное количество строк"],
                        },
                        {
                            "id": "table_caption_formatting",
                            "rule": "Подпись таблицы: Verdana 9, справа.",
                            "status": "needs_review",
                            "blocking": False,
                            "error_count": 0,
                            "systemic": False,
                            "evidence": ["Таблица 1 - Версии документа"],
                        },
                    ],
                }
            }
        },
    }

    row = build_criteria_view(payload)[0]
    view = row["formatting_view"]

    assert view["status_label"] == "Зачтено"
    assert view["passed_count"] == 1
    assert view["warning_count"] == 1
    assert view["manual_count"] == 0
    assert view["warnings"][0]["title"] == "Оглавление"
    assert view["manual_checks"][0]["title"] == "не требуются"
    assert view["toc_note"].startswith("Строк оглавления: 8. Это хорошо")


def test_formatting_view_moves_actionable_rule_to_warnings_when_score_is_awarded():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "criteria": [
            {
                "criterion_id": "formatting_instruction",
                "title": "2. Оформление соответствует инструкции и шаблону",
                "weight": 1,
                "score": 100,
                "rationale": "ok",
                "evidence": [],
            }
        ],
        "coverage_summary": {
            "formatting_instruction": {
                "checklist_facts": {
                    "metadata_summary": {
                        "font_checked_text_runs": 20,
                        "non_verdana_count": 4,
                        "font_counts": {"Arial": 4, "Verdana": 16},
                        "toc_like_line_count": 11,
                    },
                    "checklist": [
                        {
                            "id": "font_verdana",
                            "rule": "При оформлении документа необходимо использовать шрифт Verdana.",
                            "status": "fail",
                            "blocking": False,
                            "error_count": 3,
                            "systemic": True,
                            "evidence": [
                                "Arial: первый фрагмент",
                                "Arial: второй фрагмент",
                            ],
                        }
                    ],
                }
            }
        },
    }

    row = build_criteria_view(payload)[0]
    view = row["formatting_view"]

    assert view["status_label"] == "Зачтено"
    assert view["has_actionable_issues"] is False
    assert view["failed_count"] == 0
    assert view["score_reasons"][0]["title"] == "не выявлены"
    assert view["warnings"][0]["title"] == "Шрифт Verdana"
    assert view["warnings"][0]["evidence"] == ["Arial: первый фрагмент", "Arial: второй фрагмент"]
    assert view["font_examples"] == ["Arial: первый фрагмент", "Arial: второй фрагмент"]
    assert "Ctrl+H" in view["warnings"][0]["manual_hint"]
    assert "Само число не снижает балл" in view["toc_note"]


def test_formatting_view_expands_systemic_rule_ids_for_web():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "criteria": [
            {
                "criterion_id": "formatting_instruction",
                "title": "2. Оформление соответствует инструкции и шаблону",
                "weight": 1,
                "score": 0,
                "rationale": "formatting failed",
                "evidence": [],
            }
        ],
        "coverage_summary": {
            "formatting_instruction": {
                "checklist_facts": {
                    "metadata_summary": {
                        "font_checked_text_runs": 20,
                        "non_verdana_count": 0,
                        "toc_like_line_count": 8,
                    },
                    "decision_hint": {"estimated_error_count": 9},
                    "checklist": [
                        {
                            "id": "body_font_size_9",
                            "rule": "Основной текст должен быть набран размером 9 пунктов.",
                            "status": "fail",
                            "blocking": False,
                            "error_count": 3,
                            "systemic": True,
                            "evidence": ["24.0: Функционально-технические требования"],
                        },
                        {
                            "id": "list_font_and_markers",
                            "rule": "Списки: Verdana 9, одинарный интервал; маркированные - дефисом.",
                            "status": "fail",
                            "blocking": False,
                            "error_count": 3,
                            "systemic": True,
                            "evidence": ["Пункт списка: маркер списка не дефис"],
                        },
                        {
                            "id": "systemic_formatting_errors",
                            "rule": "Ошибки оформления не повторяются по всему документу.",
                            "status": "fail",
                            "blocking": True,
                            "error_count": 9,
                            "systemic": True,
                            "evidence": ["body_font_size_9", "list_font_and_markers"],
                        },
                    ],
                }
            }
        },
    }

    row = build_criteria_view(payload)[0]
    view = row["formatting_view"]

    assert view["failed_count"] == 2
    assert [item["title"] for item in view["score_reasons"]] == [
        "Основной текст 9 пт",
        "Списки: шрифт и маркеры",
    ]
    assert view["score_reasons"][0]["evidence"] == ["размер 24.0 пт: Функционально-технические требования"]
    assert "body_font_size_9" not in str([item["evidence"] for item in view["score_reasons"]])
    assert "примерно 9 ошибок" in view["decision_summary"]


def test_generic_criterion_view_groups_sections_for_web():
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "criteria": [
            {
                "criterion_id": "business_goals",
                "title": "4.5 Цели создания системы соответствуют кейсу и интервью",
                "weight": 1,
                "score": 0,
                "rationale": "Цели сформулированы как внедрение системы, а не как бизнес-результат.",
                "evidence": ["Внедрить аналитическую систему для отчетности"],
            },
            {
                "criterion_id": "data_requirements",
                "title": "4.7 Требования к данным",
                "weight": 1,
                "score": 100,
                "rationale": "Требования к данным покрывают источники, показатели и обновление.",
                "evidence": ["Источник данных: transaction, stock, product"],
            },
        ],
    }

    failed, passed = build_criteria_view(payload)

    failed_view = failed["criterion_view"]
    assert failed_view["status_label"] == "Не зачтено"
    assert "Внедрить аналитическую систему" in failed_view["score_notes"][1]
    assert failed_view["neutral_notes"] == ["дополнительных предупреждений не выделено"]
    assert "бизнес-результаты" in failed_view["manual_checks"][0]

    passed_view = passed["criterion_view"]
    assert passed_view["status_label"] == "Зачтено"
    assert passed_view["score_notes"] == ["не выявлены"]
    assert "источники, показатели и обновление" in passed_view["neutral_notes"][0]
    assert "источники, показатели, формулы" in passed_view["manual_checks"][0]


def test_literacy_comment_prints_all_error_fragments():
    evidence = [
        "необходимости подготовки отчет по продажам — нарушение управления",
        "руководитель вручную анализирует отчет по продажам ассортименту — нарушение управления",
        "Заказчик должен обновлять данных — нужно «обновлять данные»",
        "тип агрегации по по магазинам — повтор предлога",
        "в сплывающем окне — опечатка",
        "должны производится — нужно «производиться»",
        "доступ к ко всем данным — лишний предлог",
        "лишний повтор повтор — повтор слова",
    ]
    payload = {
        "status": "ok",
        "profile_id": "analysts_2026_requirements",
        "level": "strong",
        "level_note": "test",
        "criteria": [
            {
                "criterion_id": "literacy_punctuation",
                "title": "3. Отсутствуют ошибки орфографии и пунктуации",
                "weight": 1,
                "score": 0,
                "rationale": "Найдено 8 языковых/редакторских ошибок.",
                "evidence": evidence,
            }
        ],
        "mentor_block": {"issues": []},
        "intern_block": {"feedback": []},
        "ai_risk_signals": [],
    }

    report = build_markdown_report(payload)

    assert "Ошибки:<br>- «необходимости подготовки отчет по продажам» — нарушение управления" in report
    assert "<br>- «доступ к ко всем данным» — лишний предлог" in report
    assert "<br>- «лишний повтор повтор» — повтор слова" in report
    assert "По критериями" not in report
    assert "По критериям при 3 и более ошибках ставится 0." in report
