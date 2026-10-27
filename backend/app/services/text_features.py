import re

WORD_PATTERN = re.compile(r"[a-zA-Z]+(?:[_-]?\d+)?|\d+(?:\.\d+)?|[α-ωΑ-Ωβ₁₂]+")
CJK_PATTERN = re.compile(r"[\u3400-\u9fff]+")
PDF_LINE_BREAK_WORD = re.compile(r"\b([a-zA-Z]+)-\s+([a-zA-Z]+)\b")
# A lexical phase alias, not permission to infer the phase from another clause.
FINETUNING_PATTERN = r"(?:微调|细调|精调|\bfine[-\s]?tuning\b)"
POLICY_SCOPE_BREAK = re.compile(
    r"[。;；\n]|(?<!\d)\.(?!\d)|(?=而不是|而非|而是)|"
    r"[,，]\s*(?=并非|并不是|(?:这|它|论文|因此)?并?[不未]|但|然而|"
    r"实际上|并且|而且|却|\bbut\b)",
    re.I,
)


def policy_scope_clauses(text: str) -> list[str]:
    """Share proposition boundaries between scope rejection and completeness.

    A contrastive denial neither erases an earlier positive statement nor
    supplies a missing object or phase to a different proposition.
    """
    return POLICY_SCOPE_BREAK.split(text)


def has_closed_form_policy_source(text: str) -> bool:
    """Recognize an explicit policy statement or a directly displayed solution.

    A derivation page may call its expression 'the optimal solution' instead of
    repeating 'closed form'. Require an explicit policy assignment and its
    exponential expression, not merely a policy mention or a loss gradient.
    This is a bounded evidence-shape check, not a general theorem prover.
    """
    text = " ".join(text.split())
    return bool(
        re.search(r"optimal polic(?:y|ies).{0,50}\bin closed form\b", text, re.I)
        or (
            re.search(r"optimal solution\s*[:：]", text, re.I)
            and re.search(r"π\s*(?:[∗*]\s*)?\(\s*y\s*\|\s*x\s*\)\s*=", text)
            and re.search(r"\bexp\b", text)
        )
    )


def tokenize(text: str) -> list[str]:
    normalized = text.casefold().replace("β₁", "beta_1").replace("β₂", "beta_2")
    tokens = [match.group(0) for match in WORD_PATTERN.finditer(normalized)]
    # PDF line wrapping often turns "encoder" into "en- coder". Keep the
    # original terms and add a joined form for recall without changing quotes.
    tokens.extend(
        f"{match.group(1)}{match.group(2)}" for match in PDF_LINE_BREAK_WORD.finditer(normalized)
    )
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
