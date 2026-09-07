from app.parsers.ocr_parser import validate_tessdata


def test_required_ocr_languages_are_installed() -> None:
    assert validate_tessdata(None, "eng+chi_sim") == []

