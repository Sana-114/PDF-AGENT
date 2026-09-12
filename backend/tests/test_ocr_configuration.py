from app.core.config import Settings
from app.parsers.layout import extract_text_blocks
from app.parsers.ocr_parser import validate_tessdata


def test_required_ocr_languages_are_installed() -> None:
    assert validate_tessdata(None, "eng+chi_sim") == []


def test_chinese_ocr_is_prioritized_and_span_gaps_are_removed() -> None:
    assert Settings.model_fields["ocr_languages"].default == "chi_sim+eng"
    blocks = extract_text_blocks(
        {
            "blocks": [
                {
                    "type": 0,
                    "bbox": [0, 0, 100, 20],
                    "lines": [
                        {
                            "spans": [
                                {"text": "基于", "size": 16},
                                {"text": "无人机", "size": 16},
                                {"text": " PDF Agent", "size": 16},
                            ]
                        }
                    ],
                }
            ]
        },
        1,
    )

    assert blocks[0].text == "基于无人机 PDF Agent"
