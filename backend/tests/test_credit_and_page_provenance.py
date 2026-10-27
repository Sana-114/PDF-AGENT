import pytest

from app.llm.base import GeneratedClaim
from app.schemas.agent import EvidenceAnchor
from app.services.answer_completeness import contract_prompt
from app.services.credit_name_completion import name_completion_conflicts
from app.services.grounded_text import audit_grounded_claims, unsupported_numeric_facts


def source(page=2, text="The contributors are Ada One and Bob Two.", section=None):
    return EvidenceAnchor(
        evidence_id="E1",
        document_id="public",
        page_number=page,
        quote=text,
        section=section,
        score=1,
    )


@pytest.mark.parametrize(
    "claim",
    [
        "名单见第2页。",
        "名单见物理第 2 页。",
        "The source is on page 2.",
        "See p. 2.",
    ],
)
def test_actual_cited_page_locator_not_a_scientific_numeric_fact(claim):
    assert unsupported_numeric_facts(claim, [source()]) == []


@pytest.mark.parametrize(
    "claim",
    [
        "名单见第3页。",
        "The source is on page 3.",
        "第2页表明使用2 GPUs。",
        "该模型有2层。",
    ],
)
def test_wrong_page_and_measurement_equal_to_page_number_remain_rejected(claim):
    assert unsupported_numeric_facts(claim, [source()])


def test_both_cross_page_locators_require_both_cited_pages():
    text = "姓名从第2页延续到第3页。"
    assert unsupported_numeric_facts(text, [source(2), source(3)]) == []
    assert unsupported_numeric_facts(text, [source(2)]) == ["3"]


def credit_source():
    return source(
        text="Contribution role (structured group): Key Contributors\n"
        "该名单属于贡献分组；不是全局作者顺序。\n"
        "Key Contributors Ada One, Alex L. Rivera, Beth Two",
        section="贡献分组 · Key Contributors",
    )


QUESTION = "Key Contributors 中 Alex L. 对应的完整姓名是什么？"


def test_full_name_hint_uses_the_actual_source_not_following_person():
    prompt = contract_prompt(QUESTION, [credit_source()])
    assert "complete name Alex L. Rivera" in prompt
    assert "complete name Beth Two" not in prompt
    assert (
        name_completion_conflicts(QUESTION, "完整姓名为 Alex L. Rivera。", [credit_source()]) == []
    )


@pytest.mark.parametrize("text", ["完整姓名为 Beth Two。", "姓氏是 Two，完整姓名为 Beth Two。"])
def test_next_person_is_not_valid_name_completion_even_if_both_names_present(text):
    claims, audit = audit_grounded_claims(
        [GeneratedClaim(text, ["E1"])], [credit_source()], question=QUESTION
    )
    assert claims == []
    assert "semantic_conflict:name_completion_target_mismatch" in audit.rejections[0].reasons


def test_unrelated_question_and_ambiguous_prefix_do_not_invent_full_name():
    question = "What optimizer is used?"
    assert contract_prompt(question, [credit_source()]) == question
    second = credit_source().model_copy(
        update={"quote": credit_source().quote.replace("Alex L. Rivera", "Alex L. Different")}
    )
    assert "Name-completion constraint" not in contract_prompt(QUESTION, [credit_source(), second])


def test_credit_question_is_narrowed_without_inventing_an_author_order():
    prompt = contract_prompt("Key Contributors 中有哪些成员？", [credit_source()])
    assert "Credit-list scope" in prompt and "not global author rank" in prompt
