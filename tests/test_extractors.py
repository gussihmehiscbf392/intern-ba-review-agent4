from __future__ import annotations

import pytest

from review_agent.extractors import ExtractionError, extract_submission
from tests.helpers import make_docx_from_body_xml
from tests.helpers import make_minimal_docx


def test_extract_docx_success(tmp_path):
    docx_path = make_minimal_docx(
        tmp_path / "submission.docx",
        ["Цели", "Текущая ситуация", "Требования к данным"],
    )

    result = extract_submission(str(docx_path))

    assert result.extension == ".docx"
    assert "Цели" in result.text
    assert len(result.lines) == 3
    assert result.formatting_metadata["format"] == "docx"


def test_extract_docx_formatting_metadata(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>1. Общие сведения</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
      <w:r><w:t>1.1 Цели создания системы 4</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:shd w:val="clear" w:fill="D9D9D9"/></w:pPr>
      <w:r><w:t>Подсказка: укажите, что должно быть указано в разделе.</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Термин</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Определение</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "formatted.docx", body_xml)

    result = extract_submission(str(docx_path))
    metadata = result.formatting_metadata

    assert metadata["table_count"] == 1
    assert metadata["heading_style_count"] == 1
    assert metadata["numbered_paragraph_count"] >= 2
    assert metadata["toc_like_line_count"] >= 1
    assert metadata["template_hint_candidate_count"] >= 1
    assert "Подсказка" in metadata["template_hint_candidates"][0]["text"]


def test_extract_docx_toc_without_spaces_and_shading_title_not_template_hint(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:shd w:val="clear" w:fill="D9D9D9"/></w:pPr>
      <w:r><w:t>автоматизация отчетности сети строительных материалов «СтройТорг»</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>1.ОБЩИЕ СВЕДЕНИЯ4</w:t></w:r></w:p>
    <w:p><w:r><w:t>1.1.Полное наименование системы и ее условное обозначение4</w:t></w:r></w:p>
    <w:p><w:r><w:t>3.3.Требования к безопасности и разграничению доступа10</w:t></w:r></w:p>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "toc.docx", body_xml)

    result = extract_submission(str(docx_path))
    metadata = result.formatting_metadata

    assert metadata["toc_like_line_count"] == 3
    assert metadata["template_hint_candidate_count"] == 0
    assert metadata["shaded_or_highlighted_count"] == 1


def test_body_font_size_samples_ignore_title_page_and_table_headers(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:jc w:val="center"/></w:pPr>
      <w:r><w:rPr><w:sz w:val="48"/></w:rPr><w:t>Функционально-технические требования</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:jc w:val="center"/></w:pPr>
      <w:r><w:rPr><w:sz w:val="48"/></w:rPr><w:t>автоматизация отчетности сети строительных материалов «Стройторг»</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tr>
        <w:tc>
          <w:p>
            <w:r><w:rPr><w:sz w:val="22"/><w:b/></w:rPr><w:t>Версия</w:t></w:r>
          </w:p>
        </w:tc>
      </w:tr>
    </w:tbl>
    <w:p>
      <w:r><w:rPr><w:sz w:val="24"/></w:rPr><w:t>Обычный абзац основного текста с неверным размером.</w:t></w:r>
    </w:p>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "body-sizes.docx", body_xml)

    result = extract_submission(str(docx_path))
    samples = result.formatting_metadata["paragraph_format_summary"]["non_body_size_samples"]
    sample_text = " ".join(item["text"] for item in samples)

    assert "Обычный абзац основного текста" in sample_text
    assert "Функционально-технические требования" not in sample_text
    assert "автоматизация отчетности" not in sample_text
    assert "Версия" not in sample_text


def test_extract_docx_font_metadata_from_runs_and_styles(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Normal"/></w:pPr>
      <w:r><w:t>Текст стилем Verdana.</w:t></w:r>
    </w:p>
    <w:p>
      <w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr><w:t>Текст Arial.</w:t></w:r>
    </w:p>
    """
    styles_xml = """
    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:docDefaults>
        <w:rPrDefault><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/></w:rPr></w:rPrDefault>
      </w:docDefaults>
      <w:style w:type="paragraph" w:styleId="Normal">
        <w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/></w:rPr>
      </w:style>
    </w:styles>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "fonts.docx", body_xml, styles_xml=styles_xml)

    result = extract_submission(str(docx_path))
    font_summary = result.formatting_metadata["font_summary"]

    assert font_summary["checked_text_runs"] == 2
    assert font_summary["font_counts"]["Verdana"] == 1
    assert font_summary["font_counts"]["Arial"] == 1
    assert font_summary["non_verdana_count"] == 1
    assert font_summary["non_verdana_samples"][0]["font"] == "Arial"
    assert font_summary["non_verdana_samples_by_font"]["Arial"][0]["text"] == "Текст Arial."


def test_extract_docx_font_metadata_keeps_samples_for_each_non_verdana_font(tmp_path):
    arial_runs = "\n".join(
        '<w:p><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr>'
        f"<w:t>Arial fragment {idx}</w:t></w:r></w:p>"
        for idx in range(25)
    )
    body_xml = f"""
    {arial_runs}
    <w:p>
      <w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/></w:rPr>
      <w:t>Times fragment after many Arial runs.</w:t></w:r>
    </w:p>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "font_samples.docx", body_xml)

    result = extract_submission(str(docx_path))
    samples_by_font = result.formatting_metadata["font_summary"]["non_verdana_samples_by_font"]

    assert samples_by_font["Arial"][0]["text"] == "Arial fragment 0"
    assert samples_by_font["Times New Roman"][0]["text"] == "Times fragment after many Arial runs."


def test_extract_docx_font_metadata_resolves_theme_font_before_style_font(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Intro"/></w:pPr>
      <w:r>
        <w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/></w:rPr>
        <w:t>Версии документа представлены в</w:t>
      </w:r>
    </w:p>
    """
    styles_xml = """
    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:style w:type="paragraph" w:styleId="Intro">
        <w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/></w:rPr>
      </w:style>
    </w:styles>
    """
    theme_xml = """
    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <a:themeElements>
        <a:fontScheme name="Office">
          <a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>
          <a:minorFont><a:latin typeface="Verdana"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>
        </a:fontScheme>
      </a:themeElements>
    </a:theme>
    """
    docx_path = make_docx_from_body_xml(
        tmp_path / "theme_font.docx",
        body_xml,
        styles_xml=styles_xml,
        theme_xml=theme_xml,
    )

    result = extract_submission(str(docx_path))
    font_summary = result.formatting_metadata["font_summary"]

    assert font_summary["font_counts"] == {"Verdana": 1}
    assert font_summary["non_verdana_count"] == 0


def test_extract_docx_font_metadata_ignores_complex_script_font_for_cyrillic_text(tmp_path):
    body_xml = """
    <w:p>
      <w:r>
        <w:rPr><w:rFonts w:cs="Times New Roman"/></w:rPr>
        <w:t>Нефункциональные требования</w:t>
      </w:r>
    </w:p>
    """
    styles_xml = """
    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:docDefaults>
        <w:rPrDefault><w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/></w:rPr></w:rPrDefault>
      </w:docDefaults>
    </w:styles>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "complex_script_font.docx", body_xml, styles_xml=styles_xml)

    result = extract_submission(str(docx_path))
    font_summary = result.formatting_metadata["font_summary"]

    assert font_summary["font_counts"] == {"Verdana": 1}
    assert font_summary["non_verdana_count"] == 0


def test_extract_docx_font_metadata_ignores_east_asia_font_for_cyrillic_text(tmp_path):
    body_xml = """
    <w:p>
      <w:pPr><w:pStyle w:val="Toc"/></w:pPr>
      <w:r>
        <w:rPr><w:rFonts w:eastAsia="Arial Unicode MS" w:cs="Arial Unicode MS"/></w:rPr>
        <w:t>ОБЩИЕ СВЕДЕНИЯ</w:t>
      </w:r>
    </w:p>
    """
    styles_xml = """
    <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:style w:type="paragraph" w:styleId="Toc">
        <w:rPr><w:rFonts w:ascii="Verdana" w:hAnsi="Verdana"/></w:rPr>
      </w:style>
    </w:styles>
    """
    docx_path = make_docx_from_body_xml(tmp_path / "east_asia_font.docx", body_xml, styles_xml=styles_xml)

    result = extract_submission(str(docx_path))
    font_summary = result.formatting_metadata["font_summary"]

    assert font_summary["font_counts"] == {"Verdana": 1}
    assert font_summary["non_verdana_count"] == 0


def test_extract_unsupported_extension(tmp_path):
    path = tmp_path / "submission.txt"
    path.write_text("abc", encoding="utf-8")

    with pytest.raises(ExtractionError):
        extract_submission(str(path))
