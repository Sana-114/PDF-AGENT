"""Review-only structural contract; citation multiplicity is not semantic entailment."""

from app.llm.base import GeneratedAnswer, GeneratedClaim, LLMResponseError
from app.llm.review_limits import (
    MAX_REVIEW_CLAIM_CHARS,
    MAX_REVIEW_CLAIMS,
    mixed_source_shortage_note,
    opaque_paper_attribution,
)
from app.schemas.agent import EvidenceAnchor


def thematic_review_schema() -> dict:
    item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "topic": {"type": "string", "enum": ["T1", "T2", "T3", "T4"]},
            "status": {"type": "string", "enum": ["supported", "unavailable"]},
            "text": {"type": ["string", "null"], "maxLength": MAX_REVIEW_CLAIM_CHARS},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["topic", "status", "text", "evidence_ids"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": "string", "enum": [""]},
            "topics": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "id": {"type": "string", "enum": ["T1", "T2", "T3", "T4"]},
                        "title": {"type": "string", "minLength": 1, "maxLength": 40},
                    },
                    "required": ["id", "title"],
                },
            },
            "comparisons": {
                "type": "array",
                "maxItems": MAX_REVIEW_CLAIMS - 2,
                "items": item,
            },
            "background": {
                "type": "array", "maxItems": 2, "items": item,
            },
        },
        "required": ["answer", "topics", "comparisons", "background"],
    }


def render_thematic_review(raw, evidence: list[EvidenceAnchor]) -> GeneratedAnswer:
    if (
        not isinstance(raw, dict)
        or set(raw) not in ({"answer", "topics", "claims"},
                            {"answer", "topics", "comparisons", "background"})
        or raw["answer"] != ""
    ):
        raise LLMResponseError("主题综述根结构无效；不发布未经核查的自由叙述。")
    if not isinstance(raw["topics"], list) or len(raw["topics"]) > 4:
        raise LLMResponseError("主题注册表无效或超过四项；不自动合并或改写。")
    topics = {}
    for topic in raw["topics"]:
        if not isinstance(topic, dict) or set(topic) != {"id", "title"}:
            raise LLMResponseError("主题注册表条目无效。")
        key, title = topic["id"], topic["title"]
        if not isinstance(key, str) or key not in {"T1", "T2", "T3", "T4"} or key in topics:
            raise LLMResponseError("主题注册表编号重复或未识别。")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 40 or "\n" in title:
            raise LLMResponseError("主题注册表标题无效。")
        if title.strip().casefold() in {t.casefold() for t in topics.values()}:
            raise LLMResponseError("主题标题重复，不能用重复主题充数。")
        topics[key] = title.strip()
    if "claims" in raw:
        # Saved legacy output remains readable; do not relabel or reorder it.
        rows = raw["claims"]
    else:
        rows = []
        for bucket, kind, limit in (("comparisons", "cross_paper", MAX_REVIEW_CLAIMS - 2),
                                    ("background", "single_paper", 2)):
            items = raw[bucket]
            if not isinstance(items, list) or len(items) > limit:
                raise LLMResponseError("主题综述分组超过数量约束；未截断或自动重试。")
            for item in items:
                # Bucket placement explicitly declares the role; no role is
                # inferred from citations or repaired by appending evidence.
                if isinstance(item, dict) and set(item) == {
                    "topic", "status", "text", "evidence_ids",
                }:
                    rows.append({**item, "kind": kind})
                else:
                    rows.append(None)  # Rejected individually below; raw stays exact.
    if not isinstance(rows, list) or len(rows) > MAX_REVIEW_CLAIMS:
        raise LLMResponseError("主题综述超过数量约束；未截断或自动重试。")
    anchors = {e.evidence_id.upper(): e for e in evidence}
    claims, rejected = [], {}
    for index, row in enumerate(rows):
        key = f"review_item_{index + 1}"
        try:
            if not isinstance(row, dict) or set(row) != {
                "topic",
                "kind",
                "status",
                "text",
                "evidence_ids",
            }:
                raise LLMResponseError("主题条目缺少字段或包含额外字段。")
            topic, kind, status, text, ids = (
                row[k] for k in ("topic", "kind", "status", "text", "evidence_ids")
            )
            if not isinstance(topic, str) or topic not in topics:
                raise LLMResponseError("条目未指向有效的注册主题。")
            if (
                not isinstance(kind, str)
                or not isinstance(status, str)
                or kind not in {"cross_paper", "single_paper"}
                or status not in {"supported", "unavailable"}
                or not isinstance(ids, list)
            ):
                raise LLMResponseError("主题分类／状态／引用结构无效。")
            if status == "unavailable":
                if text is not None or ids:
                    raise LLMResponseError("依据不足的主题不能填入结论或借用出处。")
                rejected[key] = "所给摘录不足，本主题未生成结论。"
                continue
            if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_REVIEW_CLAIM_CHARS:
                raise LLMResponseError("主题条目为空或超长；整条暂缓，不改写或截断。")
            if mixed_source_shortage_note(text):
                raise LLMResponseError("不能将证据不足说明混进有出处的事实，请用unavailable状态。")
            if opaque_paper_attribution(text):
                raise LLMResponseError("正文必须使用可读论文／方法名，不能用内部P编号代替归属。")
            if not ids or any(
                not isinstance(eid, str) or eid.strip().strip("[]").upper() not in anchors
                for eid in ids
            ):
                raise LLMResponseError("主题引用为空或引用了未发送的证据。")
            cited = list(
                dict.fromkeys(anchors[eid.strip().strip("[]").upper()].evidence_id for eid in ids)
            )
            body_documents = {
                anchors[eid.upper()].document_id
                for eid in cited
                if anchors[eid.upper()].source_type != "reference"
            }
            if kind == "cross_paper" and len(body_documents) < 2:
                raise LLMResponseError("跨文条目必须有两篇不同论文的正文，不以同篇多块或书目充数。")
            if kind == "single_paper" and len(body_documents) > 1:
                raise LLMResponseError("单篇条目与多篇正文引用不一致，请明确比较类型。")
            claims.append(GeneratedClaim(text.strip(), cited, topics[topic], kind))
        except LLMResponseError as exc:
            rejected[key] = str(exc)
    return GeneratedAnswer(
        answer="", claims=claims, raw_grounded_output=raw, field_rejections=rejected
    )
