"""Question-derived coverage hints, not a mechanism answer bank or entailment judge."""

import re

# A bilingual retrieval glossary only: no paper identity, page, answer or value.
FACET_ALIASES = {
    "双重量化": ("double quantization",),
    "分页优化器": ("paged optimizer", "paged optimizers"),
    "梯度检查点": ("gradient checkpointing",),
    "残差连接": ("residual connection", "residual connections"),
}
MECHANISM_ACTION = re.compile(
    r"通过|用于|用来|利用|将.{1,60}(?:量化|压缩|转移|存储)|"
    r"基于.{1,60}(?:量化|压缩|转移|存储)|"
    r"(?:确保|保证).{1,60}(?:分配|划分|量化)|"
    r"(?:分配|分到).{0,35}(?:相同|相等|等量)|"
    r"为.{1,40}分配.{0,20}(?:内存|显存)|换出.{0,30}(?:CPU|内存|显存)|"
    r"(?:CPU|GPU).{0,30}(?:传输|迁移)|"
    r"(?:减少|降低|节省|避免|防止|缓解|压缩|转移|匹配|适配|提升).{1,45}"
    r"(?:内存|显存|开销|存储|权重|分布|精度|误差)|"
    r"\b(?:quantiz(?:e|es|ed|ing)|compress(?:es|ed|ing)?|evict(?:s|ed|ing)?|"
    r"transfer(?:s|red|ring)?|rescal(?:e|es|ed|ing))\b|"
    r"\b(?:reduce|save|prevent|avoid)\w*\b.{1,70}\b(?:memory|storage|overhead|spikes?)\b",
    re.I,
)


def requested_mechanisms(question: str) -> list[str]:
    """Only explicit enumerations asking each item's role; at most six short items.

    No global keyword extraction: history, a performance list and a single named
    method must not silently become a new multi-field output contract.
    """
    current = question.rsplit("\n\nCurrent user question:\n", 1)[-1]
    current = current.split("\n\nAnswer completeness contract", 1)[0]
    match = re.search(
        r"([^。？！?\n：:，,]{1,160}?)(?:各自|分别)(?:解决|如何|有什么作用|起什么作用)",
        current,
    )
    if not match:
        return []
    listing = re.sub(r"^(?:以及|其中|并说明|说明|解释|请解释)\s*", "", match[1].strip())
    items = [item.strip(" 、,，") for item in re.split(r"、|，|,|和|与|及", listing)]
    if not 2 <= len(items) <= 6 or any(not item or len(item) > 32 for item in items):
        return []
    return list(dict.fromkeys(items))


def mentions_facet(text: str, facet: str) -> bool:
    aliases = (facet, *FACET_ALIASES.get(facet, ()))
    return any(re.search(
        (r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])")
        if re.search(r"[A-Za-z]", alias) else re.escape(alias), text, re.I,
    ) for alias in aliases)


def mechanism_covered(facet: str, claims, anchors: dict) -> bool:
    """A bounded omission guard. Human review still checks the actual causal link.

    Require the named item and an action in the SAME short clause, with a cited
    body/abstract passage mentioning it. Scores/quotes are never changed. This
    deliberately does not certify causal entailment or swapped component roles.
    """
    for claim in claims:
        cited = [anchors[key] for key in claim.evidence_ids if key in anchors]
        if not any(item.source_type in {"text", "abstract", "formula"}
                   and mentions_facet(item.quote, facet) for item in cited):
            continue
        for clause in re.split(r"[。；;!?\n]", claim.text):
            if (mentions_facet(clause, facet) and MECHANISM_ACTION.search(clause)
                    and not re.search(r"未(?:能|给出|说明)|无法|不能确定|不清楚|"
                                      r"\b(?:unknown|unavailable|not explained)\b", clause, re.I)):
                return True
    return False
