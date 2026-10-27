"""Question-derived slots for explicit two-original mechanism comparisons.

No paper names, expected answers or source locations are encoded here. Local
checks establish structure and citation scope, not scientific entailment.
"""

import re

from app.llm.base import GeneratedClaim, LLMResponseError
from app.llm.comparison_support import (
    comparison_support_schema,
    compose_source_comparison,
    validate_comparison_support,
)
from app.schemas.agent import EvidenceAnchor
from app.services.mechanism_evidence import requested_mechanisms
from app.services.retrieval_policy import EXPLANATION_QUERY, requested_comparison_pair


def requested_explanation_fields(question: str, evidence: list[EvidenceAnchor]) -> dict[str, str]:
    current = question.split("\n\nAnswer completeness contract", 1)[0]
    current = current.rsplit("\n\nCurrent user question:\n", 1)[-1]
    pair = requested_comparison_pair(current)
    originals = {e.document_id for e in evidence if e.source_type != "reference"}
    if (len(pair) != 2 or pair[0].casefold() == pair[1].casefold() or len(originals) != 2
            or not EXPLANATION_QUERY.search(current)):
        return {}
    # Keep the user's actual comparison focus (e.g. learning workflow), not
    # only the two names. This is query text, never an expected-answer fact.
    intro = re.split(r"[：:？?。；;\n]", current, maxsplit=1)[0].strip()
    fields = {"comparison": intro + "；对照两篇原文在该机制上的共同点或差异"}
    facets = requested_mechanisms(current)
    questions = []
    for part in re.split(r"[：:，,？?。；;\n]", current):
        part = part.strip()
        if not part or not re.search(
            r"什么|如何|哪个|哪种|怎样|\b(?:what|how|which)\b", part, re.I
        ):
            continue
        if facets and re.search(r"各自|分别", part):
            continue  # The explicit enumeration is represented by separate slots below.
        if re.search(r"两者|二者|both\s+methods", part, re.I):
            fields["comparison"] += "；" + part
            continue
        questions.append(part)
    questions.extend(f"{facet}：题目所问的分项机制与作用" for facet in facets)
    if len(questions) > 6 or any(len(part) > 160 for part in questions):
        return {}  # Never silently truncate a complex user's requested fields.
    fields.update({f"detail_{n}": part for n, part in enumerate(dict.fromkeys(questions), 1)})
    return fields


def explanation_fields_schema(fields: dict[str, str], *, paper_ids=("P1", "P2"),
                              compose_comparison: bool = False) -> dict:
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            key: {
                "type": "object", "additionalProperties": False,
                "description": question,
                "properties": {
                    "status": {"type": "string", "enum": ["supported", "inferred", "unavailable"]},
                    "text": {"type": ["string", "null"], "maxLength": 500},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["status", "text", "evidence_ids"],
            } for key, question in fields.items()
        },
        "required": list(fields),
    }
    comparison = schema["properties"]["comparison"]
    support = comparison_support_schema(requested_comparison_pair(fields["comparison"]),
                                        list(paper_ids), fields["comparison"])
    # Unavailable comparisons keep an explicit empty proof list, never guesses.
    support["minItems"] = 0
    comparison["properties"]["source_statements"] = support
    comparison["properties"].pop("evidence_ids")
    comparison["required"].remove("evidence_ids")
    comparison["properties"]["status"]["enum"] = ["inferred", "unavailable"]
    comparison["required"].append("source_statements")
    if compose_comparison:
        comparison["properties"]["text"] = {"type": ["string", "null"], "enum": ["", None]}
        comparison["properties"]["relation"] = {
            "type": "string", "enum": ["contrast", "similarity", "unavailable"],
        }
        comparison["required"].append("relation")
        support["items"]["properties"]["text"]["maxLength"] = 200
    return schema


def render_explanation_fields(raw, fields: dict[str, str], evidence: list[EvidenceAnchor], *,
                              chinese: bool, require_comparison_support: bool = False,
                              review_pending: bool = False,
                              compose_comparison: bool = False,
                              ) -> tuple[list[GeneratedClaim], dict[str, str]]:
    """Legacy saved fields remain readable; live providers explicitly require proof."""
    if not isinstance(raw, dict) or set(raw) != set(fields):
        raise LLMResponseError("机制栏目缺失或包含未请求栏目；未自动补全或改写。")
    anchors = {e.evidence_id.upper(): e for e in evidence}
    originals = {e.document_id for e in evidence if e.source_type != "reference"}
    claims, rejected = [], {}
    for key in fields:
        try:
            row = raw[key]
            strict_comparison = key == "comparison" and require_comparison_support
            keys = {"status", "text"} if strict_comparison else {"status", "text", "evidence_ids"}
            if key == "comparison" and (require_comparison_support
                                         or isinstance(row, dict) and "source_statements" in row):
                keys.add("source_statements")
            if strict_comparison and compose_comparison:
                keys.add("relation")
            if not isinstance(row, dict) or set(row) != keys:
                raise LLMResponseError("机制栏目结构无效。")
            if strict_comparison and compose_comparison:
                row = compose_source_comparison(row, chinese=chinese)
            status, text = row["status"], row["text"]
            ids = [] if strict_comparison else row["evidence_ids"]
            if (not isinstance(status, str)
                    or status not in {"supported", "inferred", "unavailable"}
                    or not isinstance(ids, list)):
                raise LLMResponseError("机制栏目状态或引用类型无效。")
            if status == "unavailable":
                if text is not None or ids:
                    raise LLMResponseError("无依据栏目不能同时填入猜测或借用引用。")
                if key == "comparison" and "source_statements" in row:
                    validate_comparison_support(
                        row, anchors, requested_comparison_pair(fields["comparison"]),
                        derive_citations=strict_comparison,
                    )
                rejected[key] = "所给证据不足，此请求项未得到回答。"
                continue
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= 500:
                raise LLMResponseError("机制栏目为空或超长；不截断结论。")
            if strict_comparison:
                cited = validate_comparison_support(
                    row, anchors, requested_comparison_pair(fields["comparison"]),
                    derive_citations=True,
                    validate_relation=not review_pending,
                )
            else:
                if not ids or any(not isinstance(eid, str)
                                  or eid.strip().strip("[]").upper() not in anchors for eid in ids):
                    raise LLMResponseError("机制栏目必须明确引用已发送的证据编号。")
                cited = list(dict.fromkeys(anchors[eid.strip().strip("[]").upper()].evidence_id
                                          for eid in ids))
            body = {anchors[eid.upper()].document_id for eid in cited
                    if anchors[eid.upper()].source_type != "reference"}
            if not body:
                raise LLMResponseError("书目不能替代机制正文依据。")
            if key == "comparison" and (len(originals) != 2 or body != originals):
                raise LLMResponseError("比较栏目必须分别引用两份原文的正文；不自动添加出处。")
            if key == "comparison" and "source_statements" in row and not strict_comparison:
                validate_comparison_support(
                    row, anchors, requested_comparison_pair(fields["comparison"]),
                )
            inline = re.findall(r"(?<![A-Za-z0-9_])[ERG]\d+(?![A-Za-z0-9_])", text, re.I)
            if any(eid.upper() not in {c.upper() for c in cited} for eid in inline):
                raise LLMResponseError("机制正文提到了未列入该栏目引用的证据编号。")
            prefix = ("据两篇原文推断：" if chinese else "Inferred from the originals: ") \
                if key == "comparison" and status == "inferred" else ""
            claims.append(GeneratedClaim(text=prefix + text.strip(), evidence_ids=cited))
        except LLMResponseError as exc:
            rejected[key] = str(exc)
    return claims, rejected
