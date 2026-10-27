"""Bounded, deterministic contradiction checks; not a general entailment verifier.

Reject, never silently rewrite, an identifiable paper attribution mismatch or a
model-equivalence ordinal contradicted by cited text. Unknown phrasing remains
subject to source review, rather than being declared semantically verified.
"""

import re
from collections.abc import Sequence

from app.schemas.agent import EvidenceAnchor
from app.services.optimizer_evidence import optimizer_beta_conflicts
from app.services.scope_provenance import (
    exclusive_analysis_conflicts,
    memory_gain_conflicts,
    restricted_path_length_conflicts,
    sampling_task_conflicts,
)
from app.services.text_features import (
    FINETUNING_PATTERN,
    has_closed_form_policy_source,
    policy_scope_clauses,
)


def evidence_scope_note(text: str) -> bool:
    """Identify narrow input-evidence limitations, not literature absence facts.

    Authoring roles can display these as warnings, not as claims sourced to a
    paper. Ordinary QA retains its separate partial-answer/refusal handling.
    """
    return bool(re.search(
        r"(?:本(?:次)?证据|所给(?:证据|摘录)|provided evidence)[^。;；]{0,60}"
        r"(?:不可得|不足|不支持|不可比|insufficient|unavailable)|"
        r"\b(?:supplied|provided|given)\s+(?:evidence|excerpts?|passages?)\s+"
        r"(?:do(?:es)?\s+not\s+(?:report|support|describe)|is\s+insufficient)|"
        r"\bnone\s+of\s+(?:the\s+)?(?:supplied|provided|given)\s+"
        r"(?:evidence|excerpts?|passages?)\s+(?:reports?|supports?|describes?)\b",
        text, re.I,
    ))

MODEL_TOKEN = r"(?:GPT\s*[-‐‑–]?\s*\d+|BERT)"
MODEL_NAME = re.compile(rf"(?<![A-Za-z0-9]){MODEL_TOKEN}(?![A-Za-z0-9])", re.I)
PAPER_ASSERTION = re.compile(
    rf"(?P<model>{MODEL_TOKEN})\s*(?:论文(?:中)?|paper)\s*"
    r"(?:指出|提到|说明|宣称|报告|称|reports?|states?|says?|describes?)", re.I
)
SELF_NAMING = re.compile(
    rf"(?:we\s+call|called|named)\s+(?:the\s+model\s+)?"
    rf"(?P<model>{MODEL_TOKEN})(?![A-Za-z0-9])", re.I
)
ORDINAL_EQUIVALENCE = re.compile(
    r"(?P<rank>second|third|fourth|第[二三四])\s*[- ]?\s*"
    r"(?P<size>smallest|largest|小|大)\s*(?:模型|model)?"
    r"[^。.;；\n]{0,80}?(?:等价|等同|相当于|对应|equivalent|equates?\s+to|correspond)"
    rf"[^。.;；\n]{{0,80}}?(?P<target>{MODEL_TOKEN})(?![A-Za-z0-9])", re.I
)
TRAINING_SEQUENCE = re.compile(
    r"\btrain\w*\b[^。.;；\n]{0,140}\bsequences?\b|训练[^。.;；\n]{0,60}序列", re.I
)
WINDOW_INFERENCE = re.compile(
    r"(?<![不无])可(?:以)?作为[^。.;；\n]{0,60}上下文(?:长度|窗口)|"
    r"\b(?:can|could)\s+(?:be\s+)?(?:used|taken)\s+as\b"
    r"[^。.;；\n]{0,60}context\s+(?:window|length)", re.I
)
NAMED_VARIANT = re.compile(
    r"(?:referred\s+to\s+as|called|named)\s+(?P<variant>"
    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){2,4})(?![A-Za-z0-9-])", re.I)
OMITTED_SFT = re.compile(
    r"\b(?:bypass\w*|skip\w*|omit\w*)\b[^。.;；\n]{0,100}"
    r"(?:\bSFT\b|supervised\s+fine[- ]?tuning)|"
    r"\bwithout\b[^。.;；\n]{0,60}(?:\bSFT\b|supervised\s+fine[- ]?tuning)|"
    r"(?:绕过|跳过|省略)[^。.;；\n]{0,70}(?:SFT|监督微调)|"
    r"(?:SFT|监督微调)[^。.;；\n]{0,40}(?:被跳过|被省略)", re.I)
DIRECTION_CHANGE = re.compile(
    r"(?:将|把)[^。.;；\n]{0,80}(?:自注意力|self[- ]attention)"
    r"[^。.;；\n]{0,30}(?:改为|改成|转为|变为)[^。.;；\n]{0,20}(?:双向|bidirectional)|"
    r"\b(?:changes?|switch\w*)\b[^。.;；\n]{0,40}self[- ]attention"
    r"[^。.;；\n]{0,30}(?:to|into)\s+bidirectional", re.I)
EXPLICIT_ATTENTION_COMPARATOR = re.compile(
    r"\b(?:while|whereas|unlike)\s+(?:the\s+)?(?P<model>[A-Za-z][A-Za-z0-9-]{1,40})"
    r"\s+(?:Transformer|model)\s+uses\s+(?:constrained|causal|unidirectional)\s+"
    r"self[- ]attention", re.I)
NORMALIZATION = re.compile(r"\blayer\s*norm(?:alization)?\b|\blayer_norm\b|\bLN\b|层归一化", re.I)
NORM_POSITION_CLAIM = re.compile(
    r"未(?:做|作|进行|采用)(?:此|这[一项种]?)(?:调整|改动|修改)|"
    r"(?:移|挪)(?:动|到|至)|(?:放(?:在|到|于)?|置于|位于|处于)"
    r"[^。;；\n]{0,25}(?:输入|之前|之后|前|后)|"
    r"\b(?:pre[- ]?norm|post[- ]?norm|moved?|relocated?|before|after)\b|"
    r"(?:layer\s*norm(?:alization)?|LN|层归一化)[^。;；\n]{0,60}"
    r"(?:原始|original)[^。;；\n]{0,25}(?:位置|position|placement)|"
    r"(?:未|没有)(?:描述|说明)[^。;；\n]{0,20}(?:上述|此|这)[^。;；\n]{0,10}(?:调整|改动)|"
    r"\b(?:did|does)\s+not\s+(?:make|use|adopt)\s+(?:this|that)\s+(?:change|modification)\b",
    re.I,
)
NORM_POSITION_SOURCE = re.compile(
    r"\b(?:pre[- ]?norm|post[- ]?norm|pre[- ]activation|moved?|before|after)\b|"
    r"followed\s+by\s+layer\s*norm|(?:输入处|输入端|之前|之后|移动到|放在|置于)", re.I,
)
NORM_ORIGIN_CLAIM = re.compile(
    r"从[^。;；\n]{0,40}(?:之后|之前|后面|前面|前端|后端)|"
    r"\bfrom\s+(?:post[- ]?norm|after|before)\b", re.I,
)
NORM_ORIGIN_SOURCE = re.compile(
    r"\bfrom\b[^.;\n]{0,40}\b(?:after|before|post[- ]?norm|pre[- ]?norm)\b|"
    r"(?:residual|sub[- ]?layer|sub[- ]?block)[^.;\n]{0,70}followed\s+by\s+layer\s*norm|"
    r"layer\s*norm[^.;\n]{0,50}(?:after|before)\s+(?:each|every)\s+sub[- ]?(?:layer|block)", re.I,
)


def _normalization_position_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    if not NORMALIZATION.search(text) or not NORM_POSITION_CLAIM.search(text):
        return []
    if NORM_ORIGIN_CLAIM.search(text) and not any(
        NORM_ORIGIN_SOURCE.search(" ".join(item.quote.split())) for item in cited
    ):
        return ["normalization_origin_not_supported_by_cited_passage"]
    for item in cited:
        quote = re.sub(r"(?<=\w)-\s+(?=\w)", "", item.quote)
        quote = " ".join(quote.split())  # PDF line wrapping is not a sentence boundary.
        for clause in re.split(r"[。;；]|(?<!\d)\.(?!\d)", quote):
            if NORMALIZATION.search(clause) and NORM_POSITION_SOURCE.search(clause):
                return []
    # This only requires direct positional support. It is NOT proof of the
    # direction, model identity or full entailment of every other proposition.
    return ["normalization_position_missing_from_cited_passage"]


def _attention_baseline_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    if not DIRECTION_CHANGE.search(text):
        return []
    baselines = set()
    for item in cited:
        quote = re.sub(r"(?<=\w)-\s+(?=\w)", "", item.quote)
        quote = " ".join(quote.split())
        baselines.update(match["model"] for match in EXPLICIT_ATTENTION_COMPARATOR.finditer(quote))
    if baselines and not any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I)
                             for name in baselines):
        return ["attention_direction_change_uses_different_source_baseline"]
    return []


def _variant_stage_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    """A directly named training experiment cannot be generalized to a family.

    Bounded naming/omitted-stage check only; not general semantic entailment.
    Reject ambiguous pronouns rather than inventing the intended variant.
    """
    variants = set()
    for item in cited:
        quote = re.sub(r"(?<=\w)-\s+(?=\w)", "-", item.quote)
        quote = " ".join(quote.split())
        if OMITTED_SFT.search(quote):
            variants.update(match["variant"].casefold()
                            for match in NAMED_VARIANT.finditer(quote))
    if not variants:
        return []
    for clause in re.split(r"[。.;；\n]", text):
        clause = re.sub(r"(?:不|未|没有)(?:绕过|跳过|省略)|"
                        r"\b(?:does\s+not|did\s+not|never)\s+(?:bypass|skip|omit)\b",
                        "", clause, flags=re.I)
        if OMITTED_SFT.search(clause) and not any(
                re.search(r"(?<![\w-])" + re.escape(variant) + r"(?![\w-])", clause, re.I)
                for variant in variants):
            return ["omitted_training_stage_requires_named_variant"]
    return []


def _model(value: str) -> str:
    return re.sub(r"[\s‐‑–-]+", "", value).upper()


def _ordinal_links(text: str) -> list[tuple[str, str, str]]:
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    # PDF wraps between model and equivalence. Whitespace is not a clause boundary.
    text = " ".join(text.split())
    ranks = {"second": "2", "third": "3", "fourth": "4", "第二": "2",
             "第三": "3", "第四": "4"}
    sizes = {"smallest": "small", "largest": "large", "小": "small", "大": "large"}
    return [(_model(match["target"]), ranks[match["rank"].lower()],
             sizes[match["size"].lower()]) for match in ORDINAL_EQUIVALENCE.finditer(text)]


def _paper_subjects(evidence: Sequence[EvidenceAnchor]) -> dict[str, set[str]]:
    subjects: dict[str, set[str]] = {}
    for item in evidence:
        names = {_model(match.group()) for match in MODEL_NAME.finditer(item.document_title or "")}
        names.update(_model(match["model"]) for match in SELF_NAMING.finditer(item.quote))
        if names:
            subjects.setdefault(item.document_id, set()).update(names)
    # Ambiguous comparisons in a title or conflicting self descriptions are not identity proof.
    return {key: names for key, names in subjects.items() if len(names) == 1}


def _initialization_gradient_conflicts(
    text: str, cited: Sequence[EvidenceAnchor],
) -> list[str]:
    """Preserve an explicit Post-LN initialization theorem's scope, not general entailment."""
    scoped = any(
        item.source_type != "reference"
        and re.search(
            r"\bat initialization\b.{0,260}\bexpected gradients?\b.{0,100}"
            r"\bnear\s+(?:the\s+)?output\s+layer\b",
            " ".join(item.quote.split()), re.I,
        ) for item in cited
    )
    if not scoped:
        return []
    errors = []
    for clause in re.split(r"[。;；]|(?<!\d)\.(?!\d)", text):
        if not re.search(r"(?<![A-Za-z0-9_])post[-\s]*ln(?![A-Za-z0-9_])|后置(?:层)?归一化",
                         clause, re.I):
            continue
        if not re.search(r"(?:gradients?|梯度).{0,30}(?:large|大|高)|"
                         r"(?:large|大|高).{0,30}(?:gradients?|梯度)", clause, re.I):
            continue
        if re.search(r"not\s+(?:universally|always|(?:necessarily\s+)?large)|不一定|并非.*都",
                     clause, re.I):
            continue
        for pattern, error in (
            (r"initiali[sz](?:ation|ed)|初始化|初始(?:时|阶段|状态)|初始(?:的)?期望梯度",
             "gradient_initialization_phase_omitted"),
            (r"(?:near|close\s+to)[-\s]+(?:the\s+)?output[-\s]+layer|"
             r"输出(?:层|端)附近|(?:靠近|接近|邻近)输出(?:层|端)",
             "gradient_near_output_parameter_scope_omitted"),
            (r"\bexpected\b|期望", "gradient_expectation_qualifier_omitted"),
        ):
            if not re.search(pattern, clause, re.I):
                errors.append(error)
    return list(dict.fromkeys(errors))


def _policy_optimization_scope_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    """Bounded source-triggered guards, not a paper-specific answer or theorem prover.

    A policy's analytical expression is not an analytical fit of a neural model.
    Omitting online LM generation during fine-tuning does not remove every kind
    of sampling throughout the pipeline. Only actually cited body text triggers
    these guards; bibliography titles and other available excerpts do not.
    """
    quotes = [" ".join(item.quote.split()) for item in cited if item.source_type != "reference"]
    policy_source = any(has_closed_form_policy_source(quote) for quote in quotes)
    sampling_source = any(re.search(
        r"(?:eliminat\w*|without|no need for).{0,55}sampling from (?:the )?"
        rf"(?:LM|language model).{{0,25}}(?:during|in) {FINETUNING_PATTERN}", quote, re.I,
    ) for quote in quotes)
    errors = []
    # Audit contrast propositions separately: a negative continuation must not
    # hide a preceding positive error, nor turn itself into an affirmative fit.
    clauses = policy_scope_clauses(text)
    # A clearly bounded statement can be restated as a quoted phrase from the
    # same cited source, but it cannot authorize a new pipeline-wide assertion.
    qualified_sampling = bool(re.search(
        rf"{FINETUNING_PATTERN}[^。;；]{{0,35}}(?:无需|不需|免去)[^。;；]{{0,20}}"
        r"(?:语言模型|\bLM\b)[^。;；]{0,12}采样", text, re.I,
    ))
    for clause in clauses:
        if re.fullmatch(
            r"\s*(?:因此)?并?不(?:存在|具有|提供)对?(?:神经网络|网络|模型)"
            r"(?:的)?(?:参数|权重)(?:的)?(?:闭式解|解析解)\s*", clause,
        ):
            continue
        if (re.match(r"\s*(?:所给|当前|提供的)(?:证据|原文|摘录)(?:未|不)支持关于", clause)
                and "的任何说法" in clause
                and not re.search(r"但|然而|实际上|而是", clause)):
            # Quoted propositions explicitly rejected by a source-limit note
            # are not affirmative analytic-fit or sampling-free assertions.
            continue
        if re.match(r"\s*(?:(?:该论文|论文|原文|文中|作者|这|它|其|该结果|这一结果|上述结果)\s*)?"
                    r"(?:(?:并未|未|没有|并没有)(?:声称|称|宣称|报告|说明|表示|说)|"
                    r"(?:并未|未|没有|并没有)被(?:描述|表述|定义)(?:为|成)|"
                    r"不是(?:在)?说|这不是对|"
                    r"This is not a closed[- ]form fit)",
                    clause, re.I) and not re.search(r"但|然而|实际上|而是|\bbut\b", clause, re.I):
            continue
        # An explicit warning against an extrapolation is not that extrapolation.
        if re.search(r"不意味着|不等于|不等同于|而不是|而非|并非|并不是|并不表示|并不表明|不能据此|"
                     r"does not imply|does not mean|"
                     r"not equivalent to|cannot conclude", clause, re.I) and not re.search(
                         r"而是|然而|\bbut\b", clause, re.I):
            continue
        if policy_source and re.search(r"闭式|解析解|closed[- ]form", clause, re.I):
            if not re.search(r"策略|\bpolic(?:y|ies)\b", clause, re.I):
                errors.append("closed_form_policy_object_omitted")
            if re.search(
                r"(?:闭式|解析解)(?:地|下)?(?:直接)?(?:训练|拟合)(?:整个)?(?:神经)?(?:网络|模型)|"
                r"(?:闭式|解析解)(?:地|下)?(?:直接)?(?:求出|解出|求解)(?:神经)?"
                r"(?:网络|模型)(?:的)?(?:参数|权重)|"
                r"(?:network|model)\s+(?:weights?|parameters?).{0,30}"
                r"(?:solved|fitted|learned|obtained)\s+in closed form",
                clause, re.I,
            ):
                errors.append("policy_expression_not_analytic_model_fit")
        avoids_sampling = re.search(
            r"(?:省去|免去|免除|避免|避开|无需|不需要|不需|消除|不再进行)"
            r"[^，,。;；]{0,30}采样|"
            r"(?:without|no need for|eliminat\w*|does not require).{0,45}\bsampling\b",
            clause, re.I,
        )
        # Missing the excerpt with the restriction must not bypass this check.
        model_sampling = re.search(
            r"(?:\b(?:RLHF|RL|LM)\b|语言模型|language model).{0,18}(?:采样|sampling)|"
            r"sampling.{0,25}(?:\b(?:RLHF|RL|LM)\b|language model)", clause, re.I,
        )
        if avoids_sampling and model_sampling and not sampling_source:
            errors.append("sampling_avoidance_missing_direct_cited_support")
        if sampling_source and avoids_sampling and not (
            re.search(FINETUNING_PATTERN, clause, re.I)
            and re.search(r"语言模型|(?:从|由)(?:当前)?模型|"
                          r"(?<![A-Za-z0-9_])LM(?![A-Za-z0-9_])|language model", clause, re.I)
        ):
            quoted_restatement = (
                qualified_sampling
                and re.match(r"\s*(?:同一|该|上述)(?:摘要|原文|论文).{0,30}"
                             r"(?:描述|表述|重述)", clause)
                and re.search(r"[「“\"]从\s*(?:语言模型|LM)\s*采样[」”\"]", clause, re.I)
                and not re.search(r"整个|全部|任何|所有|一切|从不|\b(?:every|entire|all|never)\b",
                                  clause, re.I)
            )
            if quoted_restatement:
                continue
            errors.append("lm_sampling_finetuning_scope_omitted")
    return list(dict.fromkeys(errors))


def _shared_constraint_conflicts(text: str, cited: Sequence[EvidenceAnchor]) -> list[str]:
    """A shared constraint needs direct terms in both originals, not full entailment."""
    originals: dict[str, list[str]] = {}
    for item in cited:
        if item.source_type != "reference":
            originals.setdefault(item.document_id, []).append(item.quote)
    if len(originals) < 2:
        return []
    constraint = r"\bKL\b|KL[- ]divergence|Kullback|KL\s*散度"
    for clause in re.split(r"[。;；\n]", text):
        if re.search(r"并非|并不|不是|不意味着|未声称|\bnot\s+both\b|"
                     r"\bdoes not (?:mean|imply)\b", clause, re.I):
            continue
        if (re.search(r"(?:两篇|两者|二者|双方).{0,30}(?:都|均)|\bboth\b", clause, re.I)
                and re.search(constraint, clause, re.I)
                and not all(any(re.search(constraint, quote, re.I) for quote in quotes)
                            for quotes in originals.values())):
            return ["shared_constraint_missing_from_each_original"]
    return []


def claim_semantic_conflicts(text: str, cited: Sequence[EvidenceAnchor],
                             all_evidence: Sequence[EvidenceAnchor] = ()) -> list[str]:
    errors = (
        _variant_stage_conflicts(text, cited) + _attention_baseline_conflicts(text, cited)
        + optimizer_beta_conflicts(text, cited) + _normalization_position_conflicts(text, cited)
        + _initialization_gradient_conflicts(text, cited)
        + _policy_optimization_scope_conflicts(text, cited)
        + _shared_constraint_conflicts(text, cited)
        + sampling_task_conflicts(text, cited) + exclusive_analysis_conflicts(text, cited)
        + restricted_path_length_conflicts(text, cited)
        + memory_gain_conflicts(text, cited)
    )
    source_links = [link for item in cited for link in _ordinal_links(item.quote)]
    for target, rank, size in _ordinal_links(text):
        corresponding = [link for link in source_links if link[:2] == (target, rank)]
        if corresponding and (target, rank, size) not in corresponding:
            errors.append("ordinal_model_equivalence_mismatch")
    subjects = _paper_subjects(all_evidence or cited)
    cited_subjects = set().union(*(subjects.get(item.document_id, set()) for item in cited))
    for match in PAPER_ASSERTION.finditer(text):
        if cited_subjects and _model(match["model"]) not in cited_subjects:
            # Unknown cited identities cannot prove a mismatch; fail only when all are known.
            if all(item.document_id in subjects for item in cited):
                errors.append("paper_attribution_mismatch")
    if (WINDOW_INFERENCE.search(text)
            and any(TRAINING_SEQUENCE.search(item.quote) for item in cited)):
        # Import at call time: evidence field normalization uses grounded_text,
        # which also calls these semantic guards. Do not form an import-time cycle.
        from app.services.evidence_fields import context_values

        if not any(context_values(item) for item in cited):
            errors.append("training_sequence_only_context_inference")
    return list(dict.fromkeys(errors))
