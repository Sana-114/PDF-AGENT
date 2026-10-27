"""Conservative requested-field audits; citations alone do not mean an answer is complete."""

import re

from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.credit_name_completion import credit_scope_guidance, name_completion_guidance
from app.services.evidence_fields import (
    CONTEXT_ASSIGNMENT,
    COUNT,
    PARAMETER_LIST,
    PARAMETER_PROSE,
    PARAMETER_QUANTITY,
    context_values,
    parameter_values,
)
from app.services.grounded_text import normalise_fact
from app.services.mechanism_evidence import mechanism_covered, requested_mechanisms
from app.services.retrieval_policy import EXPLANATION_QUERY, comparison_query
from app.services.text_features import (
    FINETUNING_PATTERN,
    has_closed_form_policy_source,
    policy_scope_clauses,
)

MODEL = re.compile(r"(?<![A-Za-z0-9])GPT\s*[-‐‑–]?\s*(\d+)(?![A-Za-z0-9])", re.I)
ORIGINAL_GPT = re.compile(r"(?:原始\s*GPT|\boriginal\s+GPT)(?!\s*[-‐‑–]?\s*\d|[A-Za-z0-9])", re.I)
REVERSE_PARAMETER_ASSIGNMENT = re.compile(
    rf"(?P<value>{COUNT})\s*(?:对应|属于|is\s+the\s+size\s+of)\s*"
    rf"(?P<subject>{ORIGINAL_GPT.pattern}|{MODEL.pattern})"
    r"\s*(?:的\s*参数量|参数量|parameter\s+count)",
    re.I,
)
INFERRED_PARAMETER_ASSIGNMENT = re.compile(
    rf"(?:参数量|参数规模|parameter\s+count)\s*"
    rf"(?:可\s*推断为|推断为|is\s+inferred\s+to\s+be)\s*(?P<value>{COUNT})",
    re.I,
)
BASELINE_PARAMETER_ASSIGNMENT = re.compile(
    rf"(?:基线(?:参数量)?|\bbaseline(?:\s+parameter\s+count)?)\s*"
    rf"(?:为|是|is|[:：=])\s*(?P<value>{COUNT})"
    r"(?!\s*(?:tokens?\b|FLOPs?\b|词元))",
    re.I,
)
PARENTHETICAL_PARAMETER_ASSIGNMENT = re.compile(
    rf"^\s*[,，:：]?\s*(?:为|是|is|has|contains)\s*(?P<value>{COUNT})\s*"
    rf"[（(]\s*(?P<alias>{COUNT})\s*[）)]\s*(?:个\s*)?(?:参数|parameters?\b)",
    re.I,
)
QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:million|billion|万亿|亿|万|[MB])(?![A-Za-z])", re.I)
NEGATIVE = re.compile(
    r"(?:未|没有|未能|尚未)[^。;；\n]{0,30}(?:给出|提供|报告|提及|说明|确认|确定)|"
    r"(?:无法|不能|不足以)[^。;；\n]{0,25}(?:确定|确认|推断|证明)|"
    r"(?:不是|并非|不应|不可)[^。;；\n]{0,20}(?:确认|确定|结论|答案)|"
    r"证据不足|未知|猜测|可能|也许|或许|"
    r"\b(?:unknown|insufficient|speculation|might|maybe|possibly|uncertain)\b|"
    r"\b(?:not|never)\s+(?:explicitly\s+|directly\s+)?"
    r"(?:reported|provided|stated|given|known|determined)\b|"
    r"\b(?:cannot|can't|unable to)\s+(?:be\s+)?(?:determin\w*|confirm|infer)\b",
    re.I,
)
CLAUSE_BREAK = (
    r"[。;；!?！？\n]|(?<!\d)\.(?!\d)|"
    r"[,，]\s*(?:但是?|不过|然而|\bbut\b|\bhowever\b)\s*"
)
SOURCE_LIMITATION = re.compile(
    r"(?:非|并非|不是)原论文直接(?:报告|给出)|"
    r"not\s+(?:directly\s+|explicitly\s+)?reported\s+"
    r"(?:originally|in\s+(?:the\s+)?original\s+paper)",
    re.I,
)
PAPER_REPORTING_LIMITATION = re.compile(
    r"论文\s*(?:(?:自身|本身|中)\s*)?(?:未|没有|未能|尚未)\s*"
    r"(?:(?:直接|明确)\s*)?(?:给出|提供|报告|提及|说明)\s*"
    r"(?:(?:其|自身|本身|的)\s*)*"
    r"(?:参数量(?:数值|表|数据)?|参数(?:规模|数量)|"
    r"(?:最大)?上下文(?:窗口|大小|长度)?(?:数值|值)?|窗口(?:数值|值)?)|"
    r"\bpaper\s+(?:itself\s+)?(?:does\s+not|did\s+not|has\s+not)\s+"
    r"(?:(?:directly|explicitly)\s+)?(?:report|provide|state|give|reported|provided)\s+"
    r"(?:(?:its|the)\s+)?(?:parameter\s+count|number\s+of\s+parameters|"
    r"(?:maximum\s+)?context\s+(?:window|size|length))\b",
    re.I,
)
CONTEXT_REVERSE = re.compile(
    r"(?P<value>\d+)\s*[- ]?\s*tokens?\s+(?:context\s+(?:window|size|length)|上下文)", re.I
)
CONTEXT_CORRECTION = re.compile(r"(?:而是|instead(?: it is)?)\s*(?P<value>\d+)\s*tokens?", re.I)
INFERRED_CONTEXT_BASELINE = re.compile(
    r"(?<!不)(?:可(?:以)?推断|据此推断|can\s+infer|is\s+inferred)"
    r"(?P<body>[^。;；\n]{0,160}?)(?:基线|\bbaseline)\s*"
    r"(?:(?:上下文(?:窗口|长度|大小|规模)?|context\s+(?:window|size))\s*)?"
    r"(?:为|是|is|[:：=])\s*(?P<value>\d+)\s*tokens?\b",
    re.I,
)
SHORTHAND_CONTEXT = re.compile(r"^\s*[:：=]\s*(?P<value>\d+)(?!\d|\s*[MB])", re.I)
SHORTHAND_PARAMETERS = re.compile(rf"^\s*[:：=]\s*(?P<value>{COUNT})", re.I)
MODEL_SIZE_ASSIGNMENT = re.compile(
    rf"^\s*(?:的\s*)?(?:规模|model\s+size|size)\s*(?:约\s*)?"
    rf"(?:为|是|is)\s*(?P<value>{COUNT})(?!\s*(?:tokens?\b|FLOPs?\b|词元))",
    re.I,
)
MODEL_CORRESPONDING_COUNT = re.compile(
    rf"^\s*(?:对应|corresponds\s+to)\s*(?P<value>{COUNT})"
    r"(?!\s*(?:tokens?\b|FLOPs?\b|词元))",
    re.I,
)
LIST_PARAMETER_VALUE = re.compile(
    rf"^\s*(?:(?:small|medium|large|xl)\s+)?(?P<value>{COUNT})"
    r"(?!\s*(?:tokens?\b|FLOPs?\b|词元))",
    re.I,
)
INHERITED_RELATION = re.compile(r"承袭|继承|基于|沿用|保留|inherit|retain|based on", re.I)
REMOVED_RELATION = re.compile(
    r"丢弃|抛弃|去掉|移除|不包含|不使用|未使用|discard|without|omit|encoder.only", re.I
)
WHY_QUESTION = re.compile(r"为什么|为何|\bwhy\b", re.I)
EXPLICIT_EXPLANATION_REFUSAL = re.compile(
    r"(?:证据|摘录|原文|材料)[^。;；\n]{0,40}(?:未|没有|不足)[^。;；\n]{0,60}"
    r"(?:为何|为什么|机制|原因|推导)|"
    r"\b(?:evidence|excerpts?|sources?)\b[^.;\n]{0,40}"
    r"(?:does\s+not|do\s+not|insufficient|cannot)[^.;\n]{0,60}"
    r"\b(?:why|mechanism|reason|derivation)\b",
    re.I,
)


def current_question(question: str) -> str:
    # Trusted dialogue wrapper supplies this delimiter. Do not audit historical turns.
    return question.rsplit("\n\nCurrent user question:\n", 1)[-1]


def requested_models(question: str) -> list[str]:
    question = current_question(question)
    models = list(dict.fromkeys(name for _, _, name in model_mentions(question)))
    if len(models) == 2 and re.search(r"从.*?到|\bfrom\b.*?\bto\b", question, re.I):
        start, end = (int(model.split("-")[1]) for model in models)
        if 0 < end - start <= 5:
            return [f"GPT-{number}" for number in range(start, end + 1)]
    return models


def model_mentions(text: str) -> list[tuple[int, int, str]]:
    """Named generation aliases only; this does not supply a numerical answer."""
    names = [(match.start(), match.end(), "GPT-" + match[1]) for match in MODEL.finditer(text)]
    names.extend((match.start(), match.end(), "GPT-1") for match in ORIGINAL_GPT.finditer(text))
    return sorted(names)


def contract_kinds(question: str) -> list[str]:
    question = current_question(question)
    kinds = []
    if re.search(r"BLEU", question, re.I) and re.search(
        r"提升|差值|improv|difference", question, re.I
    ):
        kinds.append("bleu_comparison")
    if model_mentions(question) and re.search(r"参数量|parameters?|模型参数", question, re.I):
        kinds.append("model_parameters")
    if model_mentions(question) and re.search(
        r"上下文|context\s*(?:window|size)|n_?ctx", question, re.I
    ):
        kinds.append("model_context")
    if re.search(r"承袭|继承|丢弃|抛弃|inherit|discard", question, re.I):
        kinds.append("architecture_relationship")
    if requested_mechanisms(question):
        kinds.append("mechanism_parts")
    if not kinds and comparison_query(question):
        kinds.append("cross_paper_comparison")
    return kinds


def contract_kind(question: str) -> str | None:
    kinds = contract_kinds(question)
    return kinds[0] if kinds else None


def context_comparison_guidance(question: str, evidence: list[EvidenceAnchor]) -> str:
    """Prioritize an existing architecture comparison, without supplying an answer.

    The IDs still refer to the original evidence; no numbers, model alias mapping,
    new source, or proof of an inference is added by this organizational hint.
    """
    if "model_context" not in contract_kinds(question):
        return ""
    candidates = []
    for item in evidence:
        if item.source_type != "text" or not re.search(
            r"\bfollow(?:s|ed)?\b|\bbased\s+on\b|基于|沿用", item.quote, re.I
        ):
            continue
        if any(
            match["last"] for match in CONTEXT_ASSIGNMENT.finditer(" ".join(item.quote.split()))
        ):
            candidates.append(item.evidence_id)
    if not candidates:
        return ""
    from app.services.baseline_evidence import baseline_reference_bridges

    bridges = sorted(set().union(*baseline_reference_bridges(evidence, evidence)))
    bridge_hint = (
        " Available author-year reference entries whose titles uniquely match another selected "
        "original document: " + ", ".join(bridges)
        + ". When an earlier-model inference uses that baseline identity, cite the needed "
        "reference entry IN ADDITION to its configuration comparison; do not leave the "
        "identity bridge as an uncited retrieved candidate. This is a reading hint, not a "
        "new fact, model-alias assignment or automatic citation."
    ) if bridges else ""
    return (
        "\n\nSource-reading priority (organization only, not additional factual evidence): "
        + ", ".join(dict.fromkeys(candidates))
        + " contain an explicit from/to context configuration comparison alongside a named "
        "architecture baseline. Read the named baseline and scope in these original passages "
        "before declaring an earlier-model field unavailable. Do not substitute a training "
        "sequence or a sample-generation caption. If the baseline identification is ambiguous "
        "or the passages conflict, leave that field incomplete; do not invent an assignment. "
        "Cite the original IDs and label any supported earlier-model conclusion as inference."
        " Follow any supplied author-year reference entry to its full title and compare that "
        "title with the selected original paper titles. An author-year citation can identify "
        "the baseline even if a retrospective generation alias is not spelled out literally. "
        "Do not invent such a link when the bibliography/title match is missing or ambiguous."
        + bridge_hint
    )


def contract_prompt(question: str, evidence: list[EvidenceAnchor] | None = None) -> str:
    kinds = contract_kinds(question)
    instructions = {
        "cross_paper_comparison": "Compare exactly the requested methods and the same requested "
        "scope, not a different baseline discussed by one paper. For a multi-original evidence "
        "pool, include one concise relational conclusion citing exact IDs from BOTH requested "
        "originals. Write that joint-source conclusion first, then explain the separate parts. "
        "Separate single-paper summaries cannot cover that comparison. Use the "
        "remaining claims for the requested mechanisms, not unrequested benchmark scores. "
        "If only one original is supplied, compare its described methods within that source; "
        "never invent another paper or citation. Declare genuinely unsupported parts missing.",
        "mechanism_parts": "Explain each explicitly requested component separately: "
        + ", ".join(requested_mechanisms(question))
        + ". Each needs its own concise causal explanation and direct body citations. "
        "Naming methods together or reporting benchmark performance does not explain their "
        "individual roles. For an explicit cross-paper comparison, give a bounded shared/changed "
        "mechanism conclusion citing BOTH original papers FIRST, then the component roles. "
        "Keep low-precision storage distinct from computation and from trainable parameters "
        "when those distinctions are supported. Omit unrequested numerical details. "
        "Do not collapse more requested parts to obey the usual four-claim suggestion. "
        "Declare unsupported parts unavailable, never fill them from background knowledge.",
        "bleu_comparison": "Report the requested model score, previous best INCLUDING ensembles "
        "under the exact same task, and the improvement. Label any subtraction as derived and "
        "write both source operands and the expression. Never mix language directions or FLOPs.",
        "model_parameters": "Give one explicit model-name-to-parameter-count claim per requested "
        "model, not just a list of sizes. If a later paper identifies an earlier model's baseline "
        "size, cite both the equivalence statement and baseline table and label that inference. "
        "Identify the largest/main variant when reporting a family of sizes. Omit unrelated "
        "model equivalences; never reverse smallest/largest rankings or misattribute a paper. "
        "Prefer the architecture parameter-count table to rounded abstract values. A supported "
        "later-paper baseline inference is not a claim that the earlier paper directly reports it. "
        "Write one standalone model-to-count assignment per requested generation. Cite all "
        "passages needed for every fact in that claim; do not add incidental row counts or "
        "table numbers that are absent from those cited passages.",
        "model_context": "Give one explicit model-name-to-context-window claim per model. "
        "For this configuration comparison, omit unrequested training procedures and phase "
        "labels. A training paragraph is not evidence of supervised fine-tuning unless it "
        "explicitly identifies that phase. Explain an earlier-model window inference through "
        "the cited later-paper architectural baseline comparison, not through the training "
        "sequence sentence alone. "
        "Do not relabel a training sequence length as a maximum context window. A later paper's "
        "explicit configuration comparison may support a qualified earlier-model inference; cite "
        "that comparison and explain the source limitation. Source provenance is not answer "
        "availability: do not decline a supported earlier-model configuration merely because "
        "it is reported through a later paper. Explicitly label that conclusion as inference, "
        "not as an earlier-paper quotation. If the comparison does not identify the baseline "
        "unambiguously, decline only that field. Never guess a value absent from the evidence. "
        "An explicit architectural context size or full context window need not contain the "
        "literal word 'maximum'. Report that supported configuration with its source limitation; "
        "do not claim an unreported universal upper bound. A training sequence length alone "
        "does not establish a context window. Write one standalone model-to-window assignment "
        "per requested generation, and keep provenance limitations separate from the assignment.",
        "architecture_relationship": "Explain BOTH what is inherited and what is removed. "
        "Each relational conclusion must cite exact evidence IDs from BOTH original papers. "
        "One combined claim or two separately dual-source relational claims are acceptable. "
        "Label conclusions inferred from architecture descriptions as inference. Two unrelated "
        "single-source summaries do not replace a source-supported relationship. "
        "Compare the SAME parent component. A property stated for the child is not proof "
        "that it was changed from the parent. A contrast with a DIFFERENT baseline is not "
        "a change relative to the requested parent; name that other comparator explicitly. "
        "Keep inheritance and removal to one concise dual-source claim each. Omit unrequested "
        "dimensionalities, layer/head counts and variant parameter sizes. Additional claims "
        "about the original parent's masking or placement need that parent's own cited "
        "evidence; the child's description alone does not prove them.",
    }
    if not kinds:
        return (question + name_completion_guidance(question, evidence or [])
                + credit_scope_guidance(question, evidence or []))
    return (
        question
        + "\n\nAnswer completeness contract (not factual evidence):\n"
        + "\n".join(instructions[kind] for kind in kinds)
        + context_comparison_guidance(question, evidence or [])
        + name_completion_guidance(question, evidence or [])
        + credit_scope_guidance(question, evidence or [])
    )


def _model_segments(text: str):
    matches = model_mentions(text)
    # Parenthetical bibliographic provenance is not a new assignment subject.
    # Keep numerical aliases and ordinary model/value lists intact.
    provenance = [
        match.span()
        for match in re.finditer(r"[（(][^（）()]*[）)]", text)
        if re.search(
            r"论文|\bpaper\b|et\s+al\.|基线|\bbaseline\b|直接报告|"
            r"来源说明|source\s+provenance",
            match.group(),
            re.I,
        )
    ]
    matches = [
        match for match in matches if not any(start < match[0] < end for start, end in provenance)
    ]
    for index, (start, _, name) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(text)
        # Preserve a refusal preceding the first subject in the same sentence.
        prefix = re.split(CLAUSE_BREAK, text[:start])[-1] if index == 0 else ""
        yield name, prefix + text[start:end]


def _has_unavailable_claim(clause: str) -> bool:
    """Ignore only reporting-provenance spans, not every negative in that clause.

    This does not prove the provenance statement true or supply an assignment.
    Any remaining refusal/speculation still invalidates a previous assignment.
    """
    remaining = SOURCE_LIMITATION.sub(" ", clause)
    remaining = PAPER_REPORTING_LIMITATION.sub(" ", remaining)
    return bool(NEGATIVE.search(remaining))


def _without_structured_source_notes(text: str) -> str:
    """Audit assignments, not numbers/models inside labelled provenance notes.

    Only the explicit source-note wrapper is removed from this *audit view*;
    nested bibliographic parentheses are balanced. The published text, numeric
    safety audit and original model output remain untouched. Ordinary aliases,
    lists and inferred conclusions outside those notes are still examined.
    """
    spans, stack = [], []
    for position, char in enumerate(text):
        if char in "（(":
            stack.append((position, char))
        elif char in "）)" and stack:
            start, opening = stack[-1]
            if (opening, char) not in {("（", "）"), ("(", ")")}:
                continue
            stack.pop()
            if not stack and re.match(
                r"\s*(?:(?:推断|inferred)\s*[;；]\s*)?"
                r"(?:来源说明\s*[:：]|source\s+provenance\s*:)",
                text[start + 1 : position],
                re.I,
            ):
                spans.append((start, position + 1))
    for start, end in reversed(spans):
        text = text[:start] + " " + text[end:]
    return text


def affirmative_model_values(text: str, kind: str) -> dict[str, set[str]]:
    """Bounded explicit assignments, not general semantic entailment verification.

    Ignore refusals/guesses, unrelated numbers and source-limitations in separate
    clauses. An affirmative later-paper inference may still cover the field.
    """
    text = _without_structured_source_notes(text)
    answered: dict[str, set[str]] = {}
    for model, segment in _model_segments(text):
        segment_values = set()
        retracted = False
        for clause in re.split(CLAUSE_BREAK, segment):
            if _has_unavailable_claim(clause):
                # A later explicit retraction invalidates an earlier bare value;
                # a provenance limitation is not a retraction of a qualified inference.
                segment_values.clear()
                retracted = True
                continue
            mentions = model_mentions(clause)
            body = (clause[: mentions[0][0]] + clause[mentions[0][1] :]) if mentions else clause
            values = set()
            if kind == "model_context":
                values.update(
                    normalise_fact(match["last"] or match["first"])
                    for match in CONTEXT_ASSIGNMENT.finditer(body)
                )
                values.update(
                    normalise_fact(match["value"]) for match in CONTEXT_REVERSE.finditer(body)
                )
                if re.search(r"上下文|context", body, re.I):
                    values.update(
                        normalise_fact(match["value"])
                        for match in CONTEXT_CORRECTION.finditer(body)
                    )
                if match := SHORTHAND_CONTEXT.match(body):
                    values.add(normalise_fact(match["value"]))
            elif kind == "model_parameters":
                # A standalone model assignment may put a converted count in
                # parentheses before the field label. Only accept two equivalent
                # scaled quantities; dimensions, tokens and conflicting aliases
                # must not become parameter assignments through this rule.
                if match := PARENTHETICAL_PARAMETER_ASSIGNMENT.match(body):
                    value, alias = (normalise_fact(match[key]) for key in ("value", "alias"))
                    if value == alias:
                        values.add(value)
                values.update(
                    normalise_fact(match["value"])
                    for match in INFERRED_PARAMETER_ASSIGNMENT.finditer(body)
                )
                values.update(
                    normalise_fact(match["before"] or match["after"])
                    for match in PARAMETER_PROSE.finditer(body)
                )
                values.update(
                    normalise_fact(match["after_last"])
                    for match in PARAMETER_PROSE.finditer(body)
                    if match["after_last"]
                )
                values.update(
                    normalise_fact(value)
                    for match in PARAMETER_LIST.finditer(body)
                    for value in PARAMETER_QUANTITY.findall(match["values"])
                )
                if match := SHORTHAND_PARAMETERS.match(body):
                    values.add(normalise_fact(match["value"]))
                if match := MODEL_SIZE_ASSIGNMENT.match(body):
                    values.add(normalise_fact(match["value"]))
                # A model-size list may repeat the family name before every
                # variant. Require an explicit parameter label in the full claim;
                # never interpret a bare training-token count as that variant's size.
                if re.search(r"参数量|参数规模|\bparameters?\b", text, re.I):
                    # A later-paper inference can explicitly name the model
                    # again: "...parameter count...infer MODEL corresponds to N".
                    # The scaled value is not enough without this field label;
                    # a training/context quantity must not cover parameters.
                    if not re.search(
                        r"训练|上下文|\b(?:train\w*|tokens?|FLOPs?|context)\b", body, re.I
                    ):
                        assignment_body = clause[mentions[0][1] :] if mentions else clause
                        if match := MODEL_CORRESPONDING_COUNT.match(assignment_body):
                            values.add(normalise_fact(match["value"]))
                    if match := LIST_PARAMETER_VALUE.match(body):
                        values.add(normalise_fact(match["value"]))
                    # A labelled count discussion may name a baseline rather
                    # than repeat the field label. Do not reinterpret a training
                    # baseline or an unlabelled size as a parameter assignment.
                    if not re.search(r"训练|\b(?:train\w*|tokens?|FLOPs?)\b", body, re.I):
                        values.update(
                            normalise_fact(match["value"])
                            for match in BASELINE_PARAMETER_ASSIGNMENT.finditer(body)
                        )
            if values:
                segment_values.update(values)
                retracted = False
        if segment_values:
            answered.setdefault(model, set()).update(segment_values)
        elif retracted:
            answered.pop(model, None)
    if kind == "model_parameters":
        reverse_values: dict[str, set[str]] = {}
        for clause in re.split(CLAUSE_BREAK, text):
            if _has_unavailable_claim(clause):
                names = {name for _, _, name in model_mentions(clause)}
                if names:
                    for name in names:
                        reverse_values.pop(name, None)
                else:
                    reverse_values.clear()
                continue
            for match in REVERSE_PARAMETER_ASSIGNMENT.finditer(clause):
                names = model_mentions(match["subject"])
                if len(names) == 1:
                    reverse_values.setdefault(names[0][2], set()).add(
                        normalise_fact(match["value"])
                    )
        for model, values in reverse_values.items():
            answered.setdefault(model, set()).update(values)
    if kind == "model_context":
        # A model-labelled paragraph can state a qualified earlier-model baseline
        # inference after naming the later paper. Audit this explicit prose, not
        # the incidental from/to values in that later-paper comparison.
        heading = MODEL.match(text.strip())
        if (
            heading
            and re.match(r"\s*[:：]", text.strip()[heading.end() :])
            and re.search(r"上下文|context\s+(?:window|size)", text, re.I)
        ):
            subject = "GPT-" + heading[1]
            for match in INFERRED_CONTEXT_BASELINE.finditer(text):
                other_models = {name for _, _, name in model_mentions(match["body"])}
                if not other_models or other_models == {subject}:
                    answered.setdefault(subject, set()).add(normalise_fact(match["value"]))
    return answered


def affirmative_relations(text: str) -> tuple[bool, bool]:
    """Do not mistake a dual-cited refusal or speculation for a relationship answer."""
    inherited = removed = False
    for clause in re.split(CLAUSE_BREAK, text):
        has_inherited = bool(INHERITED_RELATION.search(clause))
        has_removed = bool(REMOVED_RELATION.search(clause))
        if _has_unavailable_claim(clause):
            if has_inherited or has_removed:
                if has_inherited:
                    inherited = False
                if has_removed:
                    removed = False
            else:
                inherited = removed = False
            continue
        inherited = inherited or has_inherited
        removed = removed or has_removed
    return inherited, removed


def missing_fields(
    question: str,
    claims: list[AnswerClaim],
    evidence: list[EvidenceAnchor],
    *,
    bleu_proof_published: bool = False,
) -> list[str]:
    kinds = contract_kinds(question)
    anchors = {item.evidence_id: item for item in evidence}
    missing = []
    # A requested reproducibility value remains unanswered even if the model
    # supplements an explicit refusal with unrelated, correctly cited settings.
    # This supplies no seed number and does not infer one from training steps.
    seed_field = re.compile(
        r"随机(?:数)?种子|随机状态|\b(?:random[ -]?)?seed\b|\brandom_state\b", re.I
    )
    current = current_question(question)
    missing.extend(_missing_policy_scope(current, claims, anchors))
    for facet in requested_mechanisms(question):
        if not mechanism_covered(facet, claims, anchors):
            missing.append(f"{facet} 的分项机制解释及原文依据（方法名或性能结果不能替代）")
    if (set(kinds) & {"mechanism_parts", "cross_paper_comparison"} and comparison_query(current)
            and len({item.document_id for item in evidence}) > 1
            and not any(len({anchors[key].document_id for key in claim.evidence_ids
                             if key in anchors}) > 1 for claim in claims)):
        missing.append("跨论文机制比较的双来源结论")
    if (
        seed_field.search(current)
        and re.search(
            r"多少|具体|整数|编号|数值|\b(?:value|integer|number|what|which)\b", current, re.I
        )
        and any(seed_field.search(claim.text) and (
            NEGATIVE.search(claim.text)
            or re.search(r"未出现|未找到|\b(?:not found|absent)\b", claim.text, re.I)
        ) for claim in claims)
    ):
        missing.append("所问随机种子的明确数值（不能以训练步数等其他设置替代）")
    if not kinds and re.search(
        r"多少|几(?:个|种|倍)|分位|\bhow\s+many\b|\bpercentile\b", current_question(question), re.I
    ):
        requested = set(requested_models(question))
        text = "\n".join(claim.text for claim in claims)
        answered = {name for _, _, name in model_mentions(text)}
        missing.extend(
            f"{name} 的请求结论（不能只回答对照模型）" for name in sorted(requested - answered)
        )
    # This detects explicit missing explanations, not general semantic entailment.
    # Preserve the partial answer and refusal; never supply a mechanism with rules.
    if (WHY_QUESTION.search(current) or EXPLANATION_QUERY.search(current)) and any(
        EXPLICIT_EXPLANATION_REFUSAL.search(claim.text) for claim in claims
    ):
        missing.append("请求的原因或机制解释（回答明确声明证据未给出）")
    for kind in (key for key in kinds if key in {"model_parameters", "model_context"}):
        models = requested_models(question)
        covered = set()
        for claim in claims:
            cited = [anchors[key] for key in claim.evidence_ids if key in anchors]
            source_values = set().union(
                *(
                    parameter_values(item) if kind == "model_parameters" else context_values(item)
                    for item in cited
                )
            )
            for model, values in affirmative_model_values(claim.text, kind).items():
                if values & source_values:
                    if kind == "model_context" and re.search(
                        r"推断|\binferred\b", claim.text, re.I,
                    ):
                        from app.services.baseline_evidence import baseline_reference_bridges

                        required = baseline_reference_bridges(cited, evidence)
                        if any(not ids.intersection(claim.evidence_ids) for ids in required):
                            continue  # Keep the partial claim, but do not mark provenance complete.
                    covered.add(model)
        field = "参数量" if kind == "model_parameters" else "最大上下文及来源说明"
        missing.extend(f"{model} {field}" for model in models if model not in covered)
    if "architecture_relationship" in kinds:
        inherited = removed = False
        for claim in claims:
            documents = {anchors[key].document_id for key in claim.evidence_ids if key in anchors}
            if len(documents) < 2:
                continue
            claim_inherited, claim_removed = affirmative_relations(claim.text)
            inherited = inherited or claim_inherited
            removed = removed or claim_removed
        missing.extend(
            (["承袭关系的双来源结论"] if not inherited else [])
            + (["去除部分的双来源结论"] if not removed else [])
        )
    if "bleu_comparison" in kinds and not bleu_proof_published:
        missing.append("同任务的表格基线与可核验差值（当前未形成确定性证明）")
    return missing


def _missing_policy_scope(question, claims, anchors) -> list[str]:
    """Detect omitted, explicitly requested scope fields; supply no policy facts.

    This is a bounded completeness check, not an entailment proof. Only direct
    citations to the relevant original passage can cover either requested field.
    Rejected drafts and negative statements about absent evidence do not count.
    """
    if not (re.search(r"闭式|closed[- ]form", question, re.I)
            and re.search(r"采样|sampling", question, re.I)
            and re.search(r"对象|阶段|边界|整个流程|scope|stage|object", question, re.I)):
        return []
    policy = sampling = False
    for claim in claims:
        quotes = " ".join(anchors[key].quote for key in claim.evidence_ids
                          if key in anchors and anchors[key].source_type != "reference")
        for clause in policy_scope_clauses(claim.text):
            if re.search(r"未(?:报告|声称|称)|未支持|不支持|不意味着|并非|而非|"
                         r"不能据此|does not (?:mean|imply)|not reported", clause, re.I):
                continue
            policy = policy or bool(
                re.search(r"策略|\bpolic(?:y|ies)\b", clause, re.I)
                and re.search(r"闭式|closed[- ]form", clause, re.I)
                and has_closed_form_policy_source(quotes)
            )
            sampling = sampling or bool(
                re.search(FINETUNING_PATTERN, clause, re.I)
                and re.search(r"语言模型|\bLM\b|language model", clause, re.I)
                and re.search(r"(?:免去|无需|不需|避免|消除).{0,40}采样|"
                              r"(?:eliminat\w*|without|no need for).{0,50}sampling", clause, re.I)
                and re.search(r"sampling from (?:the )?(?:LM|language model).{0,25}"
                              rf"(?:during|in) {FINETUNING_PATTERN}", quotes, re.I)
            )
    return (["闭式结果对应的策略对象及直接原文"] if not policy else []) + (
        ["免采样的语言模型对象、微调阶段及直接原文"] if not sampling else []
    )
