from __future__ import annotations

from review_agent.config import load_profile
from review_agent.engine import _detect_level
from review_agent.engine import _extract_fio_from_filename
from review_agent.engine import _extract_intern_fio
from review_agent.engine import _merge_scores
from review_agent.engine import run_review
from review_agent.models import CriterionScore
from review_agent.rules import analyze_rule_based
from tests.helpers import make_minimal_docx


def test_run_review_ok_without_llm(tmp_path):
    paragraphs = [
        "Цели",
        "Бизнес-цель: повысить скорость принятия решений.",
        "Текущая ситуация",
        "Сейчас отчеты собираются вручную.",
        "Требования к данным",
        "Источник данных: ежедневный снимок БД.",
        "Требования к визуализации",
        "Нужны гистограммы, таблицы, фильтр по локации, топ-5.",
        "Нефункциональные требования",
        "Обновление раз в день, надежность и доступность.",
        "Требования к безопасности и разграничению доступа",
        "Роли: супервайзер, менеджер ИТ-проекта.",
    ]
    docx_path = make_minimal_docx(tmp_path / "good_submission.docx", paragraphs)

    result = run_review(
        input_path=str(docx_path),
        profile_id="analysts_2026_requirements",
        enable_llm=False,
    )

    assert result["status"] == "ok"
    assert "mentor_block" in result
    assert "intern_block" in result
    assert isinstance(result["criteria"], list)
    assert result["overall_score"] >= 0


def test_run_review_profile_not_found(tmp_path):
    docx_path = make_minimal_docx(tmp_path / "submission.docx", ["Цели"])

    result = run_review(
        input_path=str(docx_path),
        profile_id="missing_profile",
        enable_llm=False,
    )

    assert result["status"] == "error"
    assert "Ошибка загрузки профиля" in result["error"]


def test_run_review_invalid_input_extension(tmp_path):
    path = tmp_path / "submission.txt"
    path.write_text("content", encoding="utf-8")

    result = run_review(
        input_path=str(path),
        profile_id="analysts_2026_requirements",
        enable_llm=False,
    )

    assert result["status"] == "error"
    assert "DOCX и PDF" in result["error"]


def test_merge_scores_uses_language_check_for_literacy(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. Орфография и пунктуация",
            weight=1,
            score=100.0,
            rationale="rule",
            evidence=[],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "literacy_punctuation",
                    "score": 100,
                    "rationale": "ok",
                    "evidence": [],
                }
            ],
            "language_check": {
                "error_count": 4,
                "errors": [
                    {"fragment": "должна производится", "error_type": "grammar", "comment": "нужно «производиться»"},
                    {"fragment": "в ручную", "error_type": "spelling", "comment": "пишется слитно"},
                    {"fragment": "по по", "error_type": "editing", "comment": "повтор предлога"},
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)
    assert merged[0].score == 0.0
    assert "Найдено 4 языковых/редакторских ошибок" in merged[0].rationale
    assert len(merged[0].evidence) >= 3


def test_extract_intern_fio_finds_author_value_below_table_header():
    lines = [
        "СВЕДЕНИЯ О ДОКУМЕНТЕ",
        "Версия",
        "Дата",
        "Автор",
        "Описание изменений",
        "1.0.0.",
        "27.04.26",
        "Потапова Дарья",
        "Создание документа",
    ]

    assert _extract_intern_fio(lines) == "Потапова Дарья"


def test_extract_intern_fio_falls_back_to_filename_initials():
    lines = ["Функционально-технические требования", "СВЕДЕНИЯ О ДОКУМЕНТЕ"]

    fio = _extract_intern_fio(
        lines,
        input_path=r"uploads\8b78f79763724ff3b9f0694250d6bcc2_1тестовое_ДАР_Потапова_ДС_а.docx",
    )

    assert fio == "Потапова Д.С."


def test_extract_fio_from_filename_with_dotted_initials():
    assert _extract_fio_from_filename("uploads/Шаблон ФТТ Рыбина Е.А..docx") == "Рыбина Е.А."


def test_merge_scores_uses_language_check_pass_for_literacy(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. Орфография и пунктуация",
            weight=1,
            score=0.0,
            rationale="rule",
            evidence=[],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "literacy_punctuation",
                    "score": 0,
                    "rationale": "bad",
                    "evidence": [],
                }
            ],
            "language_check": {
                "error_count": 2,
                "errors": [
                    {"fragment": "подготовки отчет", "error_type": "grammar", "comment": "нужно «отчета»"},
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)
    assert merged[0].score == 100.0
    assert "Найдено 2 языковых/редакторских ошибок" in merged[0].rationale


def test_merge_scores_uses_language_check_even_if_criterion_score_missing(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. Орфография и пунктуация",
            weight=1,
            score=100.0,
            rationale="rule",
            evidence=[],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [],
            "language_check": {
                "error_count": 3,
                "errors": [
                    {"fragment": "подготовки отчет", "error_type": "grammar", "comment": "нужно «отчета»"},
                    {"fragment": "обновлять данных", "error_type": "grammar", "comment": "нужно «данные»"},
                    {"fragment": "доступ к ко всем данным", "error_type": "editing", "comment": "лишний предлог"},
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0
    assert len(merged[0].evidence) == 3


def test_rule_based_language_hints_find_russian_errors():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "старт процесса: о необходимости подготовки отчет по продажам;",
        "руководитель вручную анализирует отчет по продажам ассортименту;",
        "Заказчик должен обновлять данных по остаткам;",
        "тип агрегации по по магазинам;",
        "При наведении на график в сплывающем окне отражается значение;",
        "Авторизация пользователей должна производится посредством ввода логина и пароля.",
        "Категорийный менеджер должен иметь доступ к ко всем данным.",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    literacy = next(item for item in result.criteria if item.criterion_id == "literacy_punctuation")

    assert literacy.score == 0.0
    assert any(item.startswith("необходимости подготовки отчет по продажам —") for item in literacy.evidence)
    assert any(item.startswith("доступ к ко всем данным —") for item in literacy.evidence)
    assert len(literacy.evidence) == 7


def test_rule_based_formatting_checklist_uses_docx_metadata():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Подсказка: укажите, что должно быть указано в разделе.",
        "Оглавление",
        "1. Общие сведения 4",
    ]
    metadata = {
        "format": "docx",
        "paragraph_count": 4,
        "table_count": 0,
        "heading_style_count": 0,
        "heading_candidates": [{"text": "Сведения о документе"}],
        "font_summary": {
            "checked_text_runs": 4,
            "non_verdana_count": 3,
            "font_counts": {"Arial": 3, "Verdana": 1},
            "non_verdana_samples": [
                {"font": "Arial", "text": "Подсказка: укажите, что должно быть указано в разделе."}
            ],
        },
        "numbered_paragraph_count": 1,
        "numbered_samples": ["1. Общие сведения 4"],
        "toc_like_line_count": 1,
        "toc_like_samples": ["1. Общие сведения 4"],
        "template_hint_candidates": [
            {"text": "Подсказка: укажите, что должно быть указано в разделе."}
        ],
        "template_hint_candidate_count": 1,
        "shaded_or_highlighted_count": 1,
    }

    result = analyze_rule_based("\n".join(lines), lines, profile, formatting_metadata=metadata)
    formatting = next(item for item in result.criteria if item.criterion_id == "formatting_instruction")
    facts = result.coverage_summary["formatting_instruction"]["checklist_facts"]

    assert formatting.score == 0.0
    assert facts["decision_hint"]["must_zero_if_template_explanation"] is True
    assert facts["checklist"][0]["id"] == "no_template_explanations"
    assert facts["checklist"][0]["status"] == "fail"
    assert facts["checklist"][1]["id"] == "font_verdana"
    assert facts["checklist"][1]["status"] == "fail"
    rule_ids = {item["id"] for item in facts["checklist"]}
    assert "body_font_size_9" in rule_ids
    assert "table_sequential_numbering_and_caption" in rule_ids
    assert "figure_numbering_caption_and_references" in rule_ids
    assert "page_numbering_footer" in rule_ids
    assert "footer_document_name" in rule_ids
    assert len(rule_ids) >= 25
    assert "Подсказка" in facts["checklist"][0]["evidence"][0]


def test_rule_based_formatting_score_does_not_fail_on_metadata_without_template_hints():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Оглавление",
        "1. Общие сведения 4",
        "2. Цели 5",
        "3. Требования 6",
    ]
    metadata = {
        "format": "docx",
        "paragraph_count": 4,
        "table_count": 1,
        "heading_style_count": 3,
        "font_summary": {
            "checked_text_runs": 20,
            "non_verdana_count": 5,
            "font_counts": {"Arial": 5, "Verdana": 15},
            "non_verdana_samples": [
                {"font": "Arial", "text": "Фрагмент с другим шрифтом"}
            ],
        },
        "toc_like_line_count": 3,
        "toc_like_samples": ["1. Общие сведения 4", "2. Цели 5", "3. Требования 6"],
        "template_hint_candidates": [],
        "template_hint_candidate_count": 0,
    }

    result = analyze_rule_based("\n".join(lines), lines, profile, formatting_metadata=metadata)
    formatting = next(item for item in result.criteria if item.criterion_id == "formatting_instruction")
    facts = result.coverage_summary["formatting_instruction"]["checklist_facts"]

    assert formatting.score == 100.0
    assert facts["decision_hint"]["must_zero_if_template_explanation"] is False
    assert "font_verdana" in facts["decision_hint"]["systemic_rule_ids"]
    font_rule = next(item for item in facts["checklist"] if item["id"] == "font_verdana")
    assert font_rule["status"] == "fail"


def test_merge_scores_can_use_llm_formatting_score_after_rule_metadata_warning(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="formatting_instruction",
            title="2. Оформление соответствует инструкции и шаблону",
            weight=1,
            score=0.0,
            rationale="rule formatting failure",
            evidence=["Arial: фрагмент"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "formatting_instruction",
                    "score": 1,
                    "rationale": "llm pass",
                    "evidence": [],
                }
            ]
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 100.0
    assert merged[0].rationale == "llm pass"


def test_merge_scores_keeps_all_language_evidence(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. Орфография и пунктуация",
            weight=1,
            score=100.0,
            rationale="rule",
            evidence=[f"rule ошибка {idx}" for idx in range(5)],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [],
            "language_check": {
                "error_count": 12,
                "errors": [
                    {"fragment": f"llm ошибка {idx}", "error_type": "editing", "comment": "комментарий"}
                    for idx in range(8)
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0
    assert len(merged[0].evidence) == 13


def test_merge_scores_keeps_non_literacy_score_aligned_with_llm_comment(monkeypatch):
    monkeypatch.setenv("LLM_SCORE_MODE", "direct")
    rule_scores = [
        CriterionScore(
            criterion_id="formatting_instruction",
            title="2. Оформление",
            weight=1,
            score=100.0,
            rationale="rule ok",
            evidence=[],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "formatting_instruction",
                    "score": 0,
                    "rationale": "Оформление не соответствует инструкции.",
                    "evidence": ["Остались пояснения из шаблона"],
                }
            ],
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0
    assert merged[0].rationale == "Оформление не соответствует инструкции."


def test_merge_scores_defaults_to_hybrid_llm_scores(monkeypatch):
    monkeypatch.delenv("LLM_SCORE_MODE", raising=False)
    rule_scores = [
        CriterionScore(
            criterion_id="visualization_requirements",
            title="4.8 РўСЂРµР±РѕРІР°РЅРёСЏ Рє РІРёР·СѓР°Р»РёР·Р°С†РёРё",
            weight=1,
            score=0.0,
            rationale="rule fail",
            evidence=["rule evidence"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "visualization_requirements",
                    "score": 1,
                    "rationale": "llm pass",
                    "evidence": ["llm evidence"],
                }
            ],
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0
    assert merged[0].rationale == "rule fail"
    assert merged[0].evidence == ["rule evidence"]


def test_merge_scores_hybrid_uses_language_check(monkeypatch):
    monkeypatch.delenv("LLM_SCORE_MODE", raising=False)
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. РћСЂС„РѕРіСЂР°С„РёСЏ Рё РїСѓРЅРєС‚СѓР°С†РёСЏ",
            weight=1,
            score=100.0,
            rationale="rule pass",
            evidence=["в ручную", "должна производится"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [],
            "language_check": {
                "error_count": 3,
                "errors": [
                    {"fragment": "РІ СЂСѓС‡РЅСѓСЋ", "error_type": "spelling", "comment": "РЅСѓР¶РЅРѕ СЃР»РёС‚РЅРѕ"},
                    {"fragment": "РґРѕР»Р¶РЅР° РїСЂРѕРёР·РІРѕРґРёС‚СЃСЏ", "error_type": "grammar", "comment": "С„РѕСЂРјР° РіР»Р°РіРѕР»Р°"},
                    {"fragment": "РїРѕ РїРѕ РјР°РіР°Р·РёРЅР°Рј", "error_type": "editing", "comment": "РїРѕРІС‚РѕСЂ"},
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0


def test_merge_scores_hybrid_does_not_lower_language_without_rule_corrobation(monkeypatch):
    monkeypatch.delenv("LLM_SCORE_MODE", raising=False)
    rule_scores = [
        CriterionScore(
            criterion_id="literacy_punctuation",
            title="3. РћСЂС„РѕРіСЂР°С„РёСЏ Рё РїСѓРЅРєС‚СѓР°С†РёСЏ",
            weight=1,
            score=100.0,
            rationale="rule pass",
            evidence=[],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [],
            "language_check": {
                "error_count": 4,
                "errors": [
                    {"fragment": "fragment 1", "error_type": "editing", "comment": "x"},
                    {"fragment": "fragment 2", "error_type": "editing", "comment": "x"},
                    {"fragment": "fragment 3", "error_type": "editing", "comment": "x"},
                    {"fragment": "fragment 4", "error_type": "editing", "comment": "x"},
                ],
            },
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 100.0
    assert merged[0].rationale == "rule pass"


def test_rule_based_structure_template_calibrated_positive_comment():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Автор Иванов И.И., документ создан для проверки структуры.",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1. Общие сведения 4",
        "1.1 Полное наименование системы и ее условное обозначение 4",
        "1.2 Цели создания системы 4",
        "2. Характеристика объекта автоматизации 5",
        "2.1 Описание текущей ситуации 5",
        "3. Требования к системе 6",
        "3.1 Требования к данным 6",
        "3.2 Требования к визуализации 7",
        "3.3 Нефункциональные требования 8",
        "3.4 Требования к безопасности и разграничению доступа 9",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Полное наименование: Система аналитической отчетности.",
        "Цели создания системы",
        "Цель: сократить время подготовки отчетности.",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "Сейчас отчеты собираются вручную каждый месяц.",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Источники данных: продажи, остатки и справочники товаров.",
        "Требования к визуализации",
        "Нужны графики, фильтры и таблицы.",
        "Нефункциональные требования",
        "Время открытия отчета - не более 10 секунд.",
        "Требования к безопасности и разграничению доступа",
        "Доступы разделяются по ролям пользователей.",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 100.0
    assert structure.rationale.startswith("Структура соответствует шаблону: есть")
    assert "«Сведения о документе»" in structure.rationale
    assert "«Требования к системе»" in structure.rationale
    assert "Разделы шаблона не потеряны." in structure.rationale
    assert "Критичные отклонения, влияющие на балл: не выявлены." in structure.rationale
    assert "Особенности структуры, не влияющие на балл: не выявлены." in structure.rationale
    assert "Сведения о документе" in structure.evidence


def test_rule_based_structure_template_zero_if_any_template_section_missing():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Термины, понятия и сокращения",
        "Оглавление",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 0.0
    assert "Структура не соответствует шаблону" in structure.rationale
    assert "Требования к безопасности и разграничению доступа" in structure.rationale
    assert "поэтому 0 баллов" in structure.rationale
    assert "Критичные отклонения, влияющие на балл: отсутствуют обязательные разделы" in structure.rationale


def test_rule_based_structure_template_body_section_missing_from_toc_is_deviation_not_zero():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1. Общие сведения 4",
        "2. Характеристика объекта автоматизации 5",
        "3. Требования к системе 6",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
        "Требования к безопасности и разграничению доступа",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 100.0
    assert "разделы есть в теле документа, но не отражены в оглавлении" in structure.rationale
    assert "Требования к безопасности и разграничению доступа" in structure.rationale


def test_rule_based_structure_template_reports_added_sections_without_zeroing_score():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1. Общие сведения 4",
        "1.1 Полное наименование системы и ее условное обозначение 4",
        "1.2 Цели создания системы 4",
        "2. Характеристика объекта автоматизации 5",
        "2.1 Описание текущей ситуации 5",
        "3. Требования к системе 6",
        "3.1 Требования к данным 6",
        "3.2 Требования к визуализации 7",
        "3.3 Нефункциональные требования 8",
        "3.4 Требования к безопасности и разграничению доступа 9",
        "4. Открытые вопросы для уточнения с заказчиком 10",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
        "Требования к безопасности и разграничению доступа",
        "ОТКРЫТЫЕ ВОПРОСЫ ДЛЯ УТОЧНЕНИЯ С ЗАКАЗЧИКОМ",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 100.0
    assert "добавлены разделы вне шаблона" in structure.rationale
    assert "4. Открытые вопросы для уточнения с заказчиком" in structure.rationale
    assert "4. Открытые вопросы для уточнения с заказчиком" in result.coverage_summary["structure_template"][
        "added_sections"
    ]
    assert "1. Общие сведения" in result.coverage_summary["structure_template"]["found_section_headings"]


def test_rule_based_structure_template_uses_toc_numbers_without_spaces():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1.ОБЩИЕ СВЕДЕНИЯ4",
        "1.1.Полное наименование системы и ее условное обозначение4",
        "1.2.Цели создания системы4",
        "2.ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ5",
        "2.1.Описание текущей ситуации5",
        "3.ТРЕБОВАНИЯ К СИСТЕМЕ6",
        "3.1.1.Требования к данным6",
        "3.1.2.Требования к визуализации7",
        "3.2.Нефункциональные требования8",
        "3.3.Требования к безопасности и разграничению доступа9",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
        "Требования к безопасности и разграничению доступа",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    headings = result.coverage_summary["structure_template"]["found_section_headings"]

    assert "1. ОБЩИЕ СВЕДЕНИЯ" in headings
    assert "1.1. Полное наименование системы и ее условное обозначение" in headings
    assert "3.1.1. Требования к данным" in headings


def test_rule_based_structure_template_reports_pre_toc_sections_listed_in_toc():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "Сведения о документе 1",
        "Термины, понятия и сокращения 2",
        "1. Общие сведения 4",
        "1.1 Полное наименование системы и ее условное обозначение 4",
        "1.2 Цели создания системы 4",
        "2. Характеристика объекта автоматизации 5",
        "2.1 Описание текущей ситуации 5",
        "3. Требования к системе 6",
        "3.1 Требования к данным 6",
        "3.2 Требования к визуализации 7",
        "3.3 Нефункциональные требования 8",
        "3.4 Требования к безопасности и разграничению доступа 9",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
        "Требования к безопасности и разграничению доступа",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 100.0
    assert "предшаблонные разделы расположены до оглавления" in structure.rationale
    assert "Особенности структуры, не влияющие на балл" in structure.rationale
    assert "Сведения о документе" in structure.rationale


def test_rule_based_structure_template_toc_only_section_is_missing_from_body():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1. Общие сведения 4",
        "1.1 Полное наименование системы и ее условное обозначение 4",
        "1.2 Цели создания системы 4",
        "2. Характеристика объекта автоматизации 5",
        "2.1 Описание текущей ситуации 5",
        "3. Требования к системе 6",
        "3.1 Требования к данным 6",
        "3.2 Требования к визуализации 7",
        "3.3 Нефункциональные требования 8",
        "3.4 Требования к безопасности и разграничению доступа 9",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Цели создания системы",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нефункциональные требования",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 0.0
    assert "Требования к безопасности и разграничению доступа" in structure.rationale
    assert "разделы указаны в оглавлении, но не найдены в теле документа" in structure.rationale


def test_rule_based_structure_template_reports_empty_sections_without_zeroing_score():
    profile = load_profile("analysts_2026_requirements")
    lines = [
        "Сведения о документе",
        "Версия 1.0.0 Автор Иванов И.И.",
        "Термины, понятия и сокращения",
        "MVP - Минимально жизнеспособный продукт",
        "Оглавление",
        "1. Общие сведения 4",
        "1.1 Полное наименование системы и ее условное обозначение 4",
        "1.2 Цели создания системы 4",
        "2. Характеристика объекта автоматизации 5",
        "2.1 Описание текущей ситуации 5",
        "3. Требования к системе 6",
        "3.1 Требования к данным 6",
        "3.2 Требования к визуализации 7",
        "3.3 Нефункциональные требования 8",
        "3.4 Требования к безопасности и разграничению доступа 9",
        "ОБЩИЕ СВЕДЕНИЯ",
        "Полное наименование системы и ее условное обозначение",
        "Система аналитической отчетности",
        "Цели создания системы",
        "Сократить время подготовки отчета и повысить качество данных.",
        "ХАРАКТЕРИСТИКА ОБЪЕКТА АВТОМАТИЗАЦИИ",
        "Описание текущей ситуации",
        "Сейчас отчеты собираются вручную каждый месяц.",
        "ТРЕБОВАНИЯ К СИСТЕМЕ",
        "Требования к данным",
        "Требования к визуализации",
        "Нужны графики, фильтры и топ-5 товаров.",
        "Нефункциональные требования",
        "Время открытия отчета - не более 10 секунд.",
        "Требования к безопасности и разграничению доступа",
        "Роли пользователей и права доступа должны быть описаны.",
    ]

    result = analyze_rule_based("\n".join(lines), lines, profile)
    structure = next(item for item in result.criteria if item.criterion_id == "structure_template")

    assert structure.score == 100.0
    assert "разделы найдены, но выглядят пустыми или почти пустыми" in structure.rationale
    assert "Требования к данным" in structure.rationale
    assert "Требования к данным" in result.coverage_summary["structure_template"]["empty_sections"]


def test_merge_scores_keeps_calibrated_structure_template_rule_result():
    rule_scores = [
        CriterionScore(
            criterion_id="structure_template",
            title="1. Структура соответствует шаблону",
            weight=1,
            score=100.0,
            rationale="Структура соответствует шаблону: есть «Оглавление». Разделы шаблона не потеряны.",
            evidence=["Оглавление"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "criterion_scores": [
                {
                    "criterion_id": "structure_template",
                    "score": 0,
                    "rationale": "LLM считает, что структура нарушена.",
                    "evidence": ["нет раздела"],
                }
            ],
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 100.0
    assert merged[0].rationale.startswith("Структура соответствует шаблону")


def test_merge_scores_adds_structure_template_llm_assist_without_changing_score():
    rule_scores = [
        CriterionScore(
            criterion_id="structure_template",
            title="1. Структура соответствует шаблону",
            weight=1,
            score=0.0,
            rationale=(
                "Структура не соответствует шаблону: отсутствует обязательный раздел "
                "«Требования к безопасности и разграничению доступа»."
            ),
            evidence=["Сведения о документе"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "structure_template_assist": {
                "section_mappings": [
                    {
                        "template_section": "Требования к безопасности и разграничению доступа",
                        "found_heading": "Роли и доступы",
                        "status": "possible_synonym",
                        "comment": "может быть переименованием раздела",
                    }
                ],
                "manual_review_notes": ["Проверить, раскрыты ли права доступа"],
                "added_sections": ["Приложение А"],
                "summary": "Есть возможный синоним для отсутствующего раздела",
            }
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 0.0
    assert "LLM-подсказка для наставника (на балл не влияет)" in merged[0].rationale
    assert "Роли и доступы" in merged[0].rationale
    assert "Добавленные разделы: Приложение А" in merged[0].rationale


def test_merge_scores_shows_structure_template_llm_summary():
    rule_scores = [
        CriterionScore(
            criterion_id="structure_template",
            title="1. Структура соответствует шаблону",
            weight=1,
            score=100.0,
            rationale=(
                "Структура соответствует шаблону: есть «Оглавление». "
                "Критичные отклонения, влияющие на балл: не выявлены. "
                "Особенности структуры, не влияющие на балл: добавлены разделы вне шаблона: «Открытые вопросы»."
            ),
            evidence=["Оглавление"],
        )
    ]
    llm_result = {
        "status": "ok",
        "payload": {
            "structure_template_assist": {
                "section_mappings": [],
                "manual_review_notes": [],
                "added_sections": [],
                "summary": "Нет дублирующих или добавленных разделов.",
            }
        },
    }

    merged = _merge_scores(rule_scores, llm_result)

    assert merged[0].score == 100.0
    assert "Свободное резюме LLM: Нет дублирующих или добавленных разделов" in merged[0].rationale
    assert "Открытые вопросы" in merged[0].rationale


def test_dubrovskaya_calibration_keeps_13_of_15_as_strong():
    examples = [
        {
            "id": "dubrovskaya_golden",
            "level": "strong",
            "overall_score": 86.67,
            "overall_points": 13,
            "max_points": 15,
        }
    ]

    level, note = _detect_level(13, 15, examples)

    assert level == "strong"
    assert note == "Уровень определен по калибровке."
