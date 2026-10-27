import copy

import pytest

from app.llm.base import LLMResponseError
from app.llm.comparison_support import compose_source_comparison, validate_comparison_support
from app.llm.explanation_fields import (
    explanation_fields_schema,
    render_explanation_fields,
    requested_explanation_fields,
)
from app.schemas.agent import EvidenceAnchor

QUESTION = "比较 Alpha 与 Beta：两者如何处理输入？"


def sources():
    return [EvidenceAnchor(evidence_id=key, document_id=doc, page_number=page,
                           block_ids=[key], bbox=[10, 10, 400, 200],
                           source_type="text", score=0.8, quote=text)
            for key, doc, page, text in (
                ("E1", "first", 3, "Alpha first trains a supervised encoder on the provided data."),
                ("E2", "second", 5,
                 "Beta transforms its inputs with a fixed encoder and minimizes loss."),
                ("E3", "first", 4, "Step 2: We optimize that encoder using the ABC algorithm."),
            )]


def row():
    es = sources()
    return {"status": "inferred", "text": "Alpha trains an encoder; Beta uses a fixed encoder.",
            "evidence_ids": ["E1", "E2"], "source_statements": [
                {"subject": "Alpha", "paper_id": "P1", "text": "Alpha trains its encoder.",
                 "citations": [{"evidence_id": "E1", "quote": es[0].quote}]},
                {"subject": "Beta", "paper_id": "P2", "text": "Beta uses a fixed encoder.",
                 "citations": [{"evidence_id": "E2", "quote": es[1].quote}]},
            ]}


def live_row():
    raw = row()
    raw.pop("evidence_ids")
    for unit in raw["source_statements"]:
        unit["evidence_ids"] = [c["evidence_id"] for c in unit.pop("citations")]
    return raw


@pytest.mark.parametrize("statement", ["Alpha 更新矩阵 X、Y。", "Alpha updates matrices X and Y."])
@pytest.mark.parametrize("reported", [True, False])
def test_single_letter_matrix_labels_require_selected_same_original_support(statement, reported):
    raw, es = live_row(), sources()
    raw["source_statements"][0]["text"] = statement
    es[0] = es[0].model_copy(update={"quote": (
        "Alpha freezes its weights and updates matrices X and Y." if reported else
        "Alpha freezes its weights and updates low-rank matrices."
    )})
    # Neither another available passage nor the second original can fill a gap.
    es[1] = es[1].model_copy(update={"quote": "Beta uses a fixed encoder and matrices X and Y."})
    es[2] = es[2].model_copy(update={"quote": "Alpha uses matrices X and Y."})
    anchors = {e.evidence_id: e for e in es}
    if reported:
        assert validate_comparison_support(raw, anchors, ["Alpha", "Beta"],
                                           derive_citations=True) == ["E1", "E2"]
    else:
        with pytest.raises(LLMResponseError, match="矩阵符号"):
            validate_comparison_support(raw, anchors, ["Alpha", "Beta"], derive_citations=True)


def test_composition_preserves_both_attributed_premises_without_new_shared_properties():
    raw = live_row()
    raw["text"] = ""
    raw["relation"] = "contrast"
    before = copy.deepcopy(raw)
    result = compose_source_comparison(raw, chinese=True)
    assert raw == before
    assert result["source_statements"] == raw["source_statements"]
    assert result["text"] == (
        "在所问机制上，二者处理方式不同：Alpha: Alpha trains its encoder.；"
        "Beta: Beta uses a fixed encoder.")
    assert "都" not in result["text"] and "KL" not in result["text"]
    assert validate_comparison_support(result, {e.evidence_id: e for e in sources()},
                                       ["Alpha", "Beta"], derive_citations=True) == ["E1", "E2"]


@pytest.mark.parametrize("defect", ["free_draft", "missing", "long", "empty", "extra"])
def test_composition_never_repairs_free_text_or_truncates_premises(defect):
    raw = live_row()
    raw["text"] = ""
    raw["relation"] = "contrast"
    if defect == "free_draft":
        raw["text"] = "Both use KL."
    elif defect == "missing":
        raw["source_statements"].pop()
    elif defect == "long":
        raw["source_statements"][0]["text"] = "x" * 201
    elif defect == "empty":
        raw["source_statements"][0]["text"] = " "
    else:
        raw["evidence_ids"] = ["E1", "E2"]
    before = copy.deepcopy(raw)
    with pytest.raises(LLMResponseError):
        compose_source_comparison(raw, chinese=True)
    assert raw == before


def test_live_schema_disables_free_relation_but_legacy_saved_fields_stay_readable():
    fields = requested_explanation_fields(QUESTION, sources())
    schema = explanation_fields_schema(fields, compose_comparison=True)
    compare = schema["properties"]["comparison"]["properties"]
    assert compare["text"]["enum"] == ["", None]
    assert compare["source_statements"]["items"]["properties"]["text"]["maxLength"] == 200
    assert validate(row()) == ["E1", "E2"]


@pytest.mark.parametrize("relation", ["unknown", "both_use_KL", "inferred", None, [], True])
def test_only_explicit_bounded_relation_operators_are_accepted(relation):
    raw = {**live_row(), "text": "", "relation": relation}
    with pytest.raises(LLMResponseError):
        compose_source_comparison(raw, chinese=True)


def test_similarity_operator_is_not_overall_model_equivalence():
    raw = {**live_row(), "text": "", "relation": "similarity"}
    before = copy.deepcopy(raw)
    candidate = compose_source_comparison(raw, chinese=True)
    assert "有共同处理方式" in candidate["text"]
    assert "完全相同" not in candidate["text"] and "等价" not in candidate["text"]
    assert raw == before


@pytest.mark.parametrize("defect", ["other_paper", "unknown", "precision", "number"])
def test_program_composition_still_requires_original_source_validation(defect):
    raw = live_row()
    raw["text"] = ""
    raw["relation"] = "contrast"
    unit = raw["source_statements"][0]
    if defect == "other_paper":
        unit["evidence_ids"] = ["E2"]
    elif defect == "unknown":
        unit["evidence_ids"] = ["E99"]
    elif defect == "precision":
        unit["text"] = "Alpha keeps full-precision weights."
    else:
        unit["text"] = "Alpha trains for 9999 steps."
    fields = requested_explanation_fields(QUESTION, sources())
    claims, rejected = render_explanation_fields({"comparison": raw}, fields, sources(),
        chinese=True, require_comparison_support=True, compose_comparison=True)
    assert not claims and set(rejected) == {"comparison"}


def validate(raw, es=None):
    es = es or sources()
    return validate_comparison_support(raw, {e.evidence_id: e for e in es}, ["Alpha", "Beta"])


def test_verbatim_support_preserves_inputs_and_requires_declared_proof_ids():
    raw, es = row(), sources()
    before, originals = copy.deepcopy(raw), [e.model_dump() for e in es]
    assert validate(raw, es) == ["E1", "E2"]
    assert raw == before and originals == [e.model_dump() for e in es]


@pytest.mark.parametrize("defect", [
    "missing", "same_paper", "same_subject", "wrong_paper", "unknown_id", "reference",
    "invented_quote", "spliced_quote", "short_quote", "too_long", "unused_id", "missing_id",
    "non_inferred", "wrong_type",
])
def test_invalid_or_borrowed_support_rejects_only_the_comparison(defect):
    raw, es = row(), sources()
    unit = raw["source_statements"][0]
    if defect == "missing":
        raw["source_statements"] = []
    elif defect == "same_paper":
        raw["source_statements"][1]["paper_id"] = "P1"
    elif defect == "same_subject":
        unit["subject"] = "Beta"
    elif defect == "wrong_paper":
        unit["paper_id"] = "P2"
    elif defect == "unknown_id":
        unit["citations"][0]["evidence_id"] = "E99"
    elif defect == "reference":
        es[0].source_type = "reference"
    elif defect == "invented_quote":
        unit["citations"][0]["quote"] = "Alpha is an imaginary method that does not exist."
    elif defect == "spliced_quote":
        unit["citations"][0]["quote"] = "Alpha first trains encoder on the provided data."
    elif defect == "short_quote":
        unit["citations"][0]["quote"] = "Alpha"
    elif defect == "too_long":
        unit["text"] = "a" * 281
    elif defect == "unused_id":
        raw["evidence_ids"].append("E3")
    elif defect == "missing_id":
        raw["evidence_ids"] = ["E1"]
    elif defect == "non_inferred":
        raw["status"] = "supported"
    else:
        unit["citations"][0]["quote"] = []
    fields = requested_explanation_fields(QUESTION, es)
    # Keep a valid unrelated detail slot so a bad comparison is not whole-answer fallback.
    fields["detail_1"] = "Alpha如何学习"
    response = {"comparison": raw, "detail_1": {
        "status": "supported", "text": "Alpha trains an encoder.", "evidence_ids": ["E3"]}}
    claims, rejected = render_explanation_fields(response, fields, es, chinese=False,
                                                  require_comparison_support=False)
    assert set(rejected) == {"comparison"} and len(claims) == 1
    assert claims[0].evidence_ids == ["E3"]


def test_retrieved_but_uncited_step_cannot_support_a_named_algorithm():
    raw = row()
    raw["source_statements"][0]["text"] = "Alpha trains with ABC."
    with pytest.raises(LLMResponseError, match="技术缩写"):
        validate(raw)
    raw["source_statements"][0]["citations"].append(
        {"evidence_id": "E3", "quote": sources()[2].quote})
    with pytest.raises(LLMResponseError, match="引用必须"):
        validate(raw)  # No automatic citation insertion.
    raw["evidence_ids"].append("E3")
    assert validate(raw) == ["E1", "E3", "E2"]


def test_bfloat16_expansion_supports_bf16_without_changing_source_text():
    raw, es = row(), sources()
    es[0].quote = "Alpha trains the encoder using the BFloat16 computation data type."
    raw["source_statements"][0].update(
        text="Alpha trains the encoder using BF16 computation.",
        citations=[{"evidence_id": "E1", "quote": es[0].quote}],
    )
    raw["text"] = "Alpha trains using BF16 computation; Beta uses a fixed encoder."
    before = [e.model_dump() for e in es]
    assert validate(raw, es) == ["E1", "E2"]
    assert before == [e.model_dump() for e in es]


@pytest.mark.parametrize("defect", ["other_type", "other_original", "uncited"])
def test_bf16_alias_cannot_borrow_an_unrelated_precision_or_source(defect):
    raw, es = row(), sources()
    if defect == "other_type":
        es[0].quote += " We compute using Float16."
        raw["source_statements"][0]["citations"][0]["quote"] = es[0].quote
    elif defect == "other_original":
        es[1].quote += " We compute using BFloat16."
        raw["source_statements"][1]["citations"][0]["quote"] = es[1].quote
    else:
        es[2].quote += " We compute using BFloat16."
    raw["source_statements"][0]["text"] = "Alpha trains using BF16 computation."
    with pytest.raises(LLMResponseError, match="技术缩写"):
        validate(raw, es)


def test_other_original_cannot_supply_algorithm_or_full_rank_supply_full_precision():
    raw, es = row(), sources()
    es[1].quote += " We use XYZ."
    raw["source_statements"][1]["citations"][0]["quote"] = es[1].quote
    raw["text"] = "Alpha uses XYZ; Beta uses a fixed encoder."
    with pytest.raises(LLMResponseError, match="另一原文"):
        validate(raw, es)
    raw = row()
    raw["source_statements"][0]["text"] = "Alpha holds its weights at full precision."
    with pytest.raises(LLMResponseError, match="全精度"):
        validate(raw)


def test_whitespace_and_pdf_ligatures_are_allowed_but_unknown_technical_values_are_not():
    raw, es = row(), sources()
    es[0].quote = es[0].quote.replace("first", "ﬁrst\n")
    assert validate(raw, es) == ["E1", "E2"]
    raw["source_statements"][0]["text"] = "Alpha trains for 9999 steps."
    with pytest.raises(LLMResponseError, match="数值"):
        validate(raw, es)


def test_live_mode_rejects_legacy_comparison_without_hiding_saved_reader():
    raw = row()
    raw.pop("source_statements")
    fields, es = requested_explanation_fields(QUESTION, sources()), sources()
    assert render_explanation_fields({"comparison": raw}, fields, es, chinese=False)[0]
    assert render_explanation_fields({"comparison": raw}, fields, es, chinese=False,
                                      require_comparison_support=True)[1]


def test_repeated_evidence_id_cannot_overwrite_an_earlier_proof_span():
    raw = row()
    raw["source_statements"][0]["citations"].append(
        copy.deepcopy(raw["source_statements"][0]["citations"][0]))
    with pytest.raises(LLMResponseError, match="重复编号"):
        validate(raw)


def test_live_citations_have_one_source_and_keep_explicit_proof_order_not_pool_rank():
    raw, es = live_row(), sources()
    raw["source_statements"][0]["evidence_ids"].append("E3")
    before = copy.deepcopy(raw)
    claims, rejected = render_explanation_fields(
        {"comparison": raw}, requested_explanation_fields(QUESTION, es), es,
        chinese=False, require_comparison_support=True,
    )
    assert not rejected and claims[0].evidence_ids == ["E1", "E3", "E2"]
    assert raw == before and "evidence_ids" not in raw


@pytest.mark.parametrize("defect", ["unknown", "unselected", "cross_paper", "precision"])
def test_derived_array_does_not_fill_unknown_unselected_or_unsupported_citations(defect):
    raw, es = live_row(), sources()
    if defect == "unknown":
        raw["source_statements"][0]["evidence_ids"][0] = "E99"
    elif defect == "unselected":
        raw["text"] = "Alpha optimizes with ABC; Beta uses a fixed encoder."
    elif defect == "cross_paper":
        raw["source_statements"][0]["evidence_ids"][0] = "E2"
    else:
        raw["text"] = "Alpha stores full-precision weights; Beta uses a fixed encoder."
    claims, rejected = render_explanation_fields(
        {"comparison": raw}, requested_explanation_fields(QUESTION, es), es,
        chinese=False, require_comparison_support=True,
    )
    assert not claims and set(rejected) == {"comparison"}


def test_live_schema_has_no_redundant_comparison_array_and_keeps_detail_citations():
    fields = {**requested_explanation_fields(QUESTION, sources()), "detail_1": "使用什么目标"}
    props = explanation_fields_schema(fields)["properties"]
    assert set(props["comparison"]["properties"]) == {"status", "text", "source_statements"}
    assert "evidence_ids" not in props["comparison"]["required"]
    assert "evidence_ids" in props["detail_1"]["required"]


@pytest.mark.parametrize("text", [
    "Alpha保持原精度的冻结权重，Beta使用固定编码器。",
    "Alpha retains original precision, Beta uses a fixed encoder.",
])
def test_other_subject_after_comma_cannot_hide_an_unsupported_precision_claim(text):
    raw, es = live_row(), sources()
    raw["text"] = text
    fields = requested_explanation_fields(QUESTION, es)
    # The untrusted draft may enter the editor, but must not be published even
    # if an editor mistakenly calls it supported.
    assert render_explanation_fields({"comparison": raw}, fields, es, chinese=False,
                                      require_comparison_support=True, review_pending=True)[0]
    claims, rejected = render_explanation_fields({"comparison": raw}, fields, es, chinese=False,
                                                  require_comparison_support=True)
    assert not claims and "原始精度" in rejected["comparison"]


def test_schema_uses_actual_paper_labels_and_unavailable_proof_must_be_empty():
    fields = requested_explanation_fields(QUESTION, sources())
    schema = explanation_fields_schema(fields, paper_ids=["P2", "P3"])
    assert schema["properties"]["comparison"]["properties"]["source_statements"][
        "items"]["properties"]["paper_id"]["enum"] == ["P2", "P3"]
    raw = {"status": "unavailable", "text": None, "evidence_ids": [], "source_statements": []}
    assert validate(raw) == []
    raw["source_statements"] = row()["source_statements"]
    with pytest.raises(LLMResponseError, match="无依据"):
        validate(raw)
