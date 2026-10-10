"""Conservative requested-field audits; citations alone do not mean an answer is complete."""

import re

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.grounded_text import NUMERIC_FACT_PATTERN

MODEL = re.compile(r"(?<![A-Za-z0-9])GPT\s*[-‐‑–]?\s*(\d+)(?![A-Za-z0-9])", re.I)
QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:million|billion|万亿|亿|万|[MB])(?![A-Za-z])", re.I)
NEGATIVE = re.compile(
    r"未给出|未提供|未报告|证据不足|无法确定|不能确定|"
    r"\b(?:not reported|not provided|unknown|insufficient|cannot determine)\b", re.I
)


def current_question(question: str) -> str:
    # Trusted dialogue wrapper supplies this delimiter. Do not audit historical turns.
    return question.rsplit("\n\nCurrent user question:\n", 1)[-1]


def requested_models(question: str) -> list[str]:
    question = current_question(question)
    models = list(dict.fromkeys("GPT-" + match.group(1) for match in MODEL.finditer(question)))
    if len(models) == 2 and re.search(r"从.*?到|\bfrom\b.*?\bto\b", question, re.I):
        start, end = (int(model.split("-")[1]) for model in models)
        if 0 < end - start <= 5:
            return [f"GPT-{number}" for number in range(start, end + 1)]
    return models


def contract_kind(question: str) -> str | None:
    question = current_question(question)
    if (re.search(r"BLEU", question, re.I)
            and re.search(r"提升|差值|improv|difference", question, re.I)):
        return "bleu_comparison"
    if MODEL.search(question) and re.search(r"参数量|parameters?|模型参数", question, re.I):
        return "model_parameters"
    if (MODEL.search(question)
            and re.search(r"上下文|context\s*(?:window|size)|n_?ctx", question, re.I)):
        return "model_context"
    if re.search(r"承袭|继承|丢弃|抛弃|inherit|discard", question, re.I):
        return "architecture_relationship"
    return None


def contract_prompt(question: str) -> str:
    kind = contract_kind(question)
    instructions = {
        "bleu_comparison": "Report the requested model score, previous best INCLUDING ensembles "
        "under the exact same task, and the improvement. Label any subtraction as derived and "
        "write both source operands and the expression. Never mix language directions or FLOPs.",
        "model_parameters": "Give one explicit model-name-to-parameter-count claim per requested "
        "model, not just a list of sizes. If a later paper identifies an earlier model's baseline "
        "size, cite both the equivalence statement and baseline table and label that inference.",
        "model_context": "Give one explicit model-name-to-context-window claim per model. "
        "Do not relabel a training sequence length as a maximum context window. A later paper's "
        "explicit configuration comparison may support a qualified earlier-model inference; cite "
        "that comparison and explain the source limitation.",
        "architecture_relationship": "Return one JOINT structural claim explaining what is "
        "inherited and what is removed, with exact evidence IDs from BOTH original papers. "
        "Label conclusions inferred from architecture descriptions as inference. Separate facts "
        "or two single-source summaries do not replace this joint claim.",
    }
    if not kind:
        return question
    return (question + "\n\nAnswer completeness contract (not factual evidence):\n"
            + instructions[kind])


def _model_segments(text: str):
    matches = list(MODEL.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield "GPT-" + match.group(1), text[match.start():end]


def missing_fields(question: str, claims: list[AnswerClaim], evidence: list[EvidenceAnchor],
                   *, bleu_proof_published: bool = False) -> list[str]:
    kind = contract_kind(question)
    anchors = {item.evidence_id: item for item in evidence}
    if kind in {"model_parameters", "model_context"}:
        models = requested_models(question)
        covered = set()
        for claim in claims:
            cited = [anchors[key] for key in claim.evidence_ids if key in anchors]
            for model, segment in _model_segments(claim.text):
                if NEGATIVE.search(segment):
                    continue
                if kind == "model_parameters" and QUANTITY.search(segment):
                    if any(re.search(r"parameters?|n_?params|参数", item.quote, re.I)
                           for item in cited):
                        covered.add(model)
                if kind == "model_context" and re.search(r"上下文|context|tokens?", segment, re.I):
                    if NUMERIC_FACT_PATTERN.search(segment) and any(
                        re.search(r"context|n_?ctx|上下文", item.quote, re.I) for item in cited
                    ):
                        covered.add(model)
        field = "参数量" if kind == "model_parameters" else "最大上下文及来源说明"
        return [f"{model} {field}" for model in models if model not in covered]
    if kind == "architecture_relationship":
        for claim in claims:
            documents = {anchors[key].document_id for key in claim.evidence_ids if key in anchors}
            if len(documents) >= 2 and re.search(
                r"承袭|继承|基于|沿用|保留|inherit|retain|based on", claim.text, re.I
            ) and re.search(
                r"丢弃|抛弃|去掉|移除|不包含|不使用|未使用|discard|without|omit|encoder.only",
                claim.text, re.I,
            ):
                return []
        return ["承袭与去除部分的双来源联合结论"]
    if kind == "bleu_comparison" and not bleu_proof_published:
        return ["同任务的表格基线与可核验差值（当前未形成确定性证明）"]
    return []
