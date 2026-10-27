"""A bounded output contract for two-part cross-paper inheritance/removal questions.

Slots come from the question, their text from the model, facts from supplied
evidence. Two distinct cited documents are necessary, not sufficient, proof.
"""

import re

from app.llm.base import GeneratedClaim, LLMInvalidCitationError, LLMResponseError
from app.schemas.agent import EvidenceAnchor


def requested_relationship_fields(question: str) -> dict[str, str]:
    from app.services.answer_completeness import current_question

    question = current_question(question)
    inherit = re.search(r"承袭|继承|\binherit\w*\b", question, re.I)
    remove = re.search(r"丢弃|抛弃|移除|去除|\b(?:discard|remove|omit)\w*\b", question, re.I)
    # Do not change a single-part question or silently add a second request.
    return {"inheritance": "承袭关系", "removal": "移除关系"} if inherit and remove else {}


def relationship_fields_schema(fields: dict[str, str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            key: {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["supported", "inferred", "unavailable"]},
                    "text": {
                        "type": ["string", "null"],
                        "description": "One concise conclusion for this requested "
                        "relationship ONLY. "
                        "No unrequested comparator, sizes, configurations or extra history. "
                        "Null when evidence is unavailable.",
                    },
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "text", "evidence_ids"],
            }
            for key in fields
        },
        "required": list(fields),
    }


def render_relationship_fields(
    raw, fields: dict[str, str], evidence: list[EvidenceAnchor], *, chinese: bool
) -> tuple[list[GeneratedClaim], dict[str, str]]:
    if not isinstance(raw, dict) or set(raw) != set(fields):
        raise LLMResponseError("架构关系栏目缺失或包含未请求的额外比较。")
    anchors = {item.evidence_id.upper(): item for item in evidence}
    claims, rejected, valid_slots = [], {}, 0
    for key, label in fields.items():
        try:
            item = raw[key]
            if not isinstance(item, dict) or set(item) != {"status", "text", "evidence_ids"}:
                raise LLMResponseError("架构关系栏目结构无效。")
            status, text, ids = (item[name] for name in ("status", "text", "evidence_ids"))
            if (
                not isinstance(status, str)
                or status not in {"supported", "inferred", "unavailable"}
                or not isinstance(ids, list)
            ):
                raise LLMResponseError("架构关系栏目状态或引用无效。")
            if any(
                not isinstance(eid, str) or eid.strip().strip("[]").upper() not in anchors
                for eid in ids
            ):
                raise LLMInvalidCitationError("架构关系引用了未提供的原文编号。")
            cited = list(
                dict.fromkeys(anchors[eid.strip().strip("[]").upper()].evidence_id for eid in ids)
            )
            if status == "unavailable":
                if text is not None:
                    raise LLMResponseError("无依据的关系栏目不能同时填入猜测结论。")
                valid_slots += 1
                continue
            if not isinstance(text, str) or not text.strip():
                raise LLMResponseError("架构关系栏目没有有效结论。")
            inline = re.findall(r"(?<![A-Za-z0-9])E\d+(?![A-Za-z0-9])", text, re.I)
            if any(eid.upper() not in {key.upper() for key in cited} for eid in inline):
                raise LLMInvalidCitationError("架构关系正文提到未列入引用的原文。")
            if len({anchors[eid.upper()].document_id for eid in cited}) < 2:
                raise LLMInvalidCitationError(
                    "跨论文关系必须引用两份不同原文，不能用同篇多个摘录充数。"
                )
            if chinese:
                prefix = label + ("（架构推断）" if status == "inferred" else "") + "："
            else:
                prefix = key + (" (architectural inference)" if status == "inferred" else "") + ": "
            claims.append(GeneratedClaim(text=prefix + text.strip(), evidence_ids=cited))
            valid_slots += 1
        except LLMResponseError as exc:
            rejected[key] = str(exc)
    if rejected and not valid_slots:
        raise LLMResponseError("所有架构关系栏目均未通过本地结构／引用检查。")
    return claims, rejected
