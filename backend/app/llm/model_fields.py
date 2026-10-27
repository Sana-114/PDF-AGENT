"""Render model-owned numeric facts supplied by the LLM, never supply their values.

The query determines the slots, not the answers. Missing evidence is a valid
structured outcome. Rendered claims still undergo citation, numeric and field
audits in the ordinary production harness; this is not an entailment verifier.
"""

import re

from app.llm.base import GeneratedClaim, LLMInvalidCitationError, LLMResponseError

NOTE_CITATION = re.compile(r"(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])", re.I)
POSITIVE_REFERENCE = re.compile(
    r"所指|指向|所述|说明|指出|给出|记载|报告|支持|"
    r"\b(?:identifies?|describes?|states?|reports?|supports?)\b",
    re.I,
)
EXCLUDED_REFERENCE = re.compile(
    r"不单独|不作为|未(?:列出|给出|报告)|没有|不足|无法|"
    r"可能|也许|或许|推测|猜测|"
    r"\b(?:not|insufficient|exclude\w*|without|may|might|maybe|possibly|uncertain)\b",
    re.I,
)


def normalize_positive_note_citations(item: dict, allowed_ids: set[str]) -> tuple[dict, list[str]]:
    """Map the model's explicit positive inline references to the same citation list.

    Never infer an ID from a paper title, quote, value or the retrieval pool. A
    negated/uncertain use or unknown ID is not rescued. Keep the original object
    unchanged; strict field, numerical and provenance guards still run afterwards.
    """
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("source_note"), str)
        or not isinstance(item.get("evidence_ids"), list)
    ):
        return item, []
    canonical = {key.upper(): key for key in allowed_ids}
    ids = item["evidence_ids"]
    if any(
        not isinstance(key, str) or key.strip().strip("[]").upper() not in canonical for key in ids
    ):
        return item, []  # Leave malformed explicit arrays to the strict validator.
    cited = list(dict.fromkeys(canonical[key.strip().strip("[]").upper()] for key in ids))
    appended = []
    note = item["source_note"]
    for match in NOTE_CITATION.finditer(note):
        key = canonical.get(match.group().upper())
        if key is None:
            raise LLMInvalidCitationError("来源说明引用了未提供的证据编号。")
        if key in cited:
            continue
        after = re.split(r"[。;；\n]", note[match.end() :], maxsplit=1)[0][:45]
        before = note[max(0, match.start() - 12) : match.start()]
        if not EXCLUDED_REFERENCE.search(before + after) and (
            POSITIVE_REFERENCE.search(after[:30]) or re.search(r"见\s*$|\bsee\s*$", before, re.I)
        ):
            cited.append(key)
            appended.append(key)
    return {**item, "evidence_ids": cited}, appended


def requested_numeric_fields(question: str) -> dict[str, tuple[str, str]]:
    # The provider factory is imported by llm.__init__ while retrieval imports
    # numeric guards. Resolve these service helpers only at generation time.
    from app.services.answer_completeness import contract_kinds, requested_models

    kinds = [
        kind for kind in contract_kinds(question) if kind in {"model_parameters", "model_context"}
    ]
    return {
        f"{model}:{kind}": (model, kind) for model in requested_models(question) for kind in kinds
    }


def numeric_fields_schema(fields: dict[str, tuple[str, str]]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            key: {
                "type": "object",
                "additionalProperties": False,
                "description": f"One fact for {model}, field {kind}. Never borrow another subject.",
                "properties": {
                    "status": {"type": "string", "enum": ["supported", "inferred", "unavailable"]},
                    "value": {
                        "type": ["string", "null"],
                        "description": "One parameter count with unit or context token count; "
                        "null when unavailable. Not a list, range, formula or guessed value.",
                    },
                    "source_note": {
                        "type": "string",
                        "description": (
                            "Brief source qualification in the question language. "
                            "For inferred values, explain the cited baseline comparison. "
                            "Do not claim an earlier paper directly reported a later-paper "
                            "inference. Omit unrelated training phases. Every passage "
                            "mentioned in this note must also be in evidence_ids."
                        ),
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "value", "source_note", "evidence_ids"],
            }
            for key, (model, kind) in fields.items()
        },
        "required": list(fields),
    }


def render_numeric_fields(
    raw, fields: dict[str, tuple[str, str]], allowed_ids: set[str], *, chinese: bool
) -> list[GeneratedClaim]:
    from app.services.evidence_fields import COUNT

    if not isinstance(raw, dict) or set(raw) != set(fields):
        raise LLMResponseError("模型数值栏目缺失、重复归属或超出请求范围。")
    canonical = {value.upper(): value for value in allowed_ids}
    claims = []
    for key, (model, kind) in fields.items():
        item = raw[key]
        if not isinstance(item, dict) or set(item) != {
            "status",
            "value",
            "source_note",
            "evidence_ids",
        }:
            raise LLMResponseError("模型数值栏目结构无效。")
        status, value, note, ids = (
            item[name] for name in ("status", "value", "source_note", "evidence_ids")
        )
        if (
            not isinstance(status, str)
            or status not in {"supported", "inferred", "unavailable"}
            or not isinstance(note, str)
            or not note.strip()
            or not isinstance(ids, list)
        ):
            raise LLMResponseError("模型数值栏目缺少状态或来源说明。")
        if any(
            not isinstance(eid, str) or eid.strip().strip("[]").upper() not in canonical
            for eid in ids
        ):
            raise LLMInvalidCitationError("模型数值栏目引用了未提供的证据。")
        cited = list(dict.fromkeys(canonical[eid.strip().strip("[]").upper()] for eid in ids))
        mentioned = {
            match.upper()
            for match in re.findall(r"(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])", note, re.I)
        }
        if mentioned - {eid.upper() for eid in cited}:
            raise LLMInvalidCitationError("来源说明提到的原文未列入该栏目的引用。")
        label = (
            ("参数量" if kind == "model_parameters" else "上下文窗口")
            if chinese
            else ("parameter count" if kind == "model_parameters" else "context window")
        )
        if status == "unavailable":
            if value is not None:
                raise LLMResponseError("证据不足栏目不得同时填写猜测数值。")
            if cited:
                text = (
                    f"{model} 的{label}无法确认（来源说明：{note.strip()}）。"
                    if chinese
                    else f"{model}'s {label} cannot be determined "
                    f"(source provenance: {note.strip()})."
                )
                claims.append(GeneratedClaim(text=text, evidence_ids=cited))
            continue
        pattern = (
            rf"(?P<count>{COUNT})(?:\s*(?:个\s*)?参数|\s+parameters?)?"
            if kind == "model_parameters"
            else (r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\s*(?:[个個]\s*)?(?:tokens?|词元))?")
        )
        match = re.fullmatch(pattern, value.strip(), re.I) if isinstance(value, str) else None
        if not match:
            raise LLMResponseError("模型数值栏目必须是单一有效量值。")
        if not cited:
            raise LLMInvalidCitationError("模型数值栏目没有原文引用。")
        value = value.strip()
        if kind == "model_parameters":
            value = match["count"]
        if kind == "model_context":
            value = re.match(r"[\d,]+", value)[0].replace(",", "") + " tokens"
        inference = status == "inferred"
        if chinese:
            assignment = "可推断为" if inference and kind == "model_parameters" else "为"
            qualifier = "推断；" if inference else ""
            text = f"{model} 的{label}{assignment} {value}（{qualifier}来源说明：{note.strip()}）。"
        else:
            qualifier = "inferred; " if inference else ""
            text = f"{model}'s {label} is {value} ({qualifier}source provenance: {note.strip()})."
        claims.append(GeneratedClaim(text=text, evidence_ids=cited))
    return claims


def render_valid_numeric_fields(
    raw,
    fields: dict[str, tuple[str, str]],
    allowed_ids: set[str],
    *,
    chinese: bool,
    citation_normalizations: dict[str, list[str]] | None = None,
) -> tuple[list[GeneratedClaim], dict[str, str]]:
    """Keep valid slots without accepting or repairing an invalid sibling.

    The outer contract is still strict. A local citation/quantity defect removes
    that entire field, not its inconvenient note, and does not guess its value.
    Ordinary completeness checks must mark the omitted requested field missing.
    """
    if not isinstance(raw, dict) or set(raw) != set(fields):
        raise LLMResponseError("模型数值栏目缺失、重复归属或超出请求范围。")
    claims, rejected = [], {}
    if citation_normalizations is not None:
        citation_normalizations.clear()
    for key, field in fields.items():
        try:
            item, appended = (
                normalize_positive_note_citations(raw[key], allowed_ids)
                if citation_normalizations is not None
                else (raw[key], [])
            )
            claims.extend(
                render_numeric_fields({key: item}, {key: field}, allowed_ids, chinese=chinese)
            )
            if appended:
                citation_normalizations[key] = appended
        except LLMResponseError as exc:
            rejected[key] = str(exc)
    if rejected and not claims:
        raise LLMResponseError("所有模型数值栏目均未通过本地结构／引用检查。")
    return claims, rejected
