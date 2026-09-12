import re

WORD_PATTERN = re.compile(r"[a-zA-Z]+(?:[_-]?\d+)?|\d+(?:\.\d+)?|[α-ωΑ-Ωβ₁₂]+")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]+")


def tokenize(text: str) -> list[str]:
    normalized = text.casefold().replace("β₁", "beta_1").replace("β₂", "beta_2")
    tokens = [match.group(0) for match in WORD_PATTERN.finditer(normalized)]
    for match in CJK_PATTERN.finditer(normalized):
        sequence = match.group(0)
        tokens.extend(sequence)
        tokens.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return tokens


def infer_source_type(section: str | None, block_ids: list[str]) -> str:
    normalized_section = (section or "").casefold()
    first_id = str(block_ids[0]).casefold() if block_ids else ""
    if first_id.startswith("ref-") or normalized_section.startswith("参考文献"):
        return "reference"
    if "table-" in first_id or normalized_section.startswith("表格"):
        return "table"
    if "figure-" in first_id or normalized_section.startswith("图表"):
        return "figure"
    if first_id.startswith("formula-") or normalized_section.startswith("公式"):
        return "formula"
    if normalized_section.strip(" .·0123456789") in {"abstract", "摘要"}:
        return "abstract"
    return "text"
