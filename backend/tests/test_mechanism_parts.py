import json

import httpx
import pytest

from app.llm.openai_responses import OpenAIResponsesProvider
from app.schemas.agent import AnswerClaim, EvidenceAnchor
from app.services.answer_completeness import contract_kinds, contract_prompt, missing_fields
from app.services.mechanism_evidence import mechanism_covered, mentions_facet, requested_mechanisms
from app.services.retrieval import _expand_query
from app.services.retrieval_policy import EXPLANATION_QUERY, is_reference_query

QUESTION = ("比较 LoRA 与 QLoRA：两者如何处理预训练权重？QLoRA 使用什么位宽量化，"
            "以及 NF4、双重量化和分页优化器各自解决什么内存问题？分别引用两篇原文。")


def anchor(key, quote, doc="q", source="text"):
    return EvidenceAnchor(evidence_id=key, document_id=doc, page_number=5,
                          block_ids=[key], quote=quote, source_type=source, score=0.8)


def claim(text, ids):
    return AnswerClaim(claim_id="c", text=text, evidence_ids=ids)


def test_extracts_only_explicit_current_question_parts_not_expected_answers():
    assert requested_mechanisms(QUESTION) == ["NF4", "双重量化", "分页优化器"]
    assert "mechanism_parts" in contract_kinds(QUESTION)
    assert requested_mechanisms("解释甲模块、乙模块和丙模块各自解决什么问题？") == [
        "甲模块", "乙模块", "丙模块"]
    for question in ["NF4、双重量化和分页优化器的性能如何？", "NF4是什么？",
                     QUESTION + "\n\nCurrent user question:\n训练数据有多少？"]:
        assert requested_mechanisms(question) == []
    prompt = contract_prompt(QUESTION)
    assert "BOTH original papers" in prompt and "quantization constants" not in prompt


@pytest.mark.parametrize("question", [QUESTION, "为什么它有效？请引用原文。",
                                      "Explain how it works; cite both original papers."])
def test_support_citations_do_not_switch_to_bibliography(question):
    assert EXPLANATION_QUERY.search(question)
    assert not is_reference_query(question)


@pytest.mark.parametrize("question", ["为什么引用该参考文献？", "第11条引用是什么？",
                                      "请给出 DOI 和完整引用信息", "Find the references",
                                      "比较方法并引用原文，另列参考文献"])
def test_real_bibliography_intent_is_preserved(question):
    assert is_reference_query(question)


def test_glossary_and_facet_boundaries_do_not_supply_factual_values():
    assert "double quantization" in _expand_query("双重量化")
    assert "paged optimizers" in _expand_query("分页优化器")
    assert mentions_facet("Double Quantization reduces overhead.", "双重量化")
    assert mentions_facet("NF4 data type", "NF4")
    assert not mentions_facet("NF40 or XNF4", "NF4")


def test_method_names_and_performance_are_not_individual_mechanism_answers():
    evidence = [anchor("E1", "NF4 with double quantization recovers performance."),
                anchor("E2", "LoRA freezes pretrained weights.", doc="l")]
    claims = [claim("LoRA和QLoRA都冻结权重。", ["E1", "E2"]),
              claim("NF4加双重量化不会导致性能下降，分页优化器也有实验结果。", ["E1"])]
    missing = missing_fields(QUESTION, claims, evidence)
    assert len(missing) == 3
    assert all("分项机制解释" in item for item in missing)


def test_paging_transfer_clause_is_a_mechanism_without_borrowing_the_next_clause():
    evidence = [anchor("E1", "Paged Optimizers allocate memory for optimizer states and "
                       "automatically transfer between CPU and GPU.")]
    text = ("分页优化器借助统一内存，在CPU与GPU之间自动页对页传输；"
            "当显存不足时把优化器状态换出到CPU RAM。")
    assert mechanism_covered("分页优化器", [claim(text, ["E1"])], {"E1": evidence[0]})
    performance = claim("分页优化器在CPU与GPU实验中性能很好。", ["E1"])
    assert not mechanism_covered("分页优化器", [performance], {"E1": evidence[0]})


@pytest.mark.parametrize("names", ["NF4, Double Quantization and Paged Optimizers work well.",
                                   "NF4 + Double Quantization does not degrade performance."])
def test_english_method_nouns_are_not_causal_actions(names):
    evidence = [anchor("E1", "NF4 and Double Quantization are used with Paged Optimizers.")]
    result = missing_fields(QUESTION, [claim(names, ["E1"])], evidence)
    assert len(result) == 3


def test_completeness_needs_each_clause_and_its_cited_original_not_an_unrelated_anchor():
    evidence = [anchor("E1", "NF4 represents normally distributed weights."),
                anchor("E2", "Double Quantization quantizes constants for memory savings."),
                anchor("E3", "Paged optimizers transfer optimizer states between CPU and GPU."),
                anchor("E4", "LoRA freezes pretrained weights.", doc="l")]
    claims = [claim("LoRA和QLoRA都冻结权重，仅训练适配器。", ["E1", "E4"]),
              claim("NF4通过匹配权重分布进行低位表示。", ["E1"]),
              claim("双重量化将量化常数再次量化，减少存储开销。", ["E2"]),
              claim("分页优化器通过转移优化器状态缓解显存尖峰。", ["E3"])]
    assert missing_fields(QUESTION, claims, evidence) == []
    claims[2] = claim(claims[2].text, ["E1"])
    assert "双重量化" in missing_fields(QUESTION, claims, evidence)[0]
    claims[2] = claim("双重量化未说明如何降低内存开销。", ["E2"])
    assert "双重量化" in missing_fields(QUESTION, claims, evidence)[0]
    claims[2] = claim("双重量化；NF4通过减少存储节省内存。", ["E2"])
    assert "双重量化" in missing_fields(QUESTION, claims, evidence)[0]


def test_single_source_background_is_not_a_cross_comparison():
    evidence = [anchor("E1", "NF4."), anchor("E2", "LoRA.", doc="l")]
    result = missing_fields(QUESTION, [claim("NF4通过量化权重降低存储开销。", ["E1"])], evidence)
    assert "跨论文机制比较的双来源结论" in result


def test_allocation_action_is_recognized_without_a_paper_specific_answer():
    evidence = anchor("E1", "甲量化 assigns values to bins.")
    anchors = {"E1": evidence}
    text = "甲量化确保各箱从输入中分配相等数量的数值。"
    assert mechanism_covered("甲量化", [claim(text, ["E1"])], anchors)
    assert mechanism_covered(
        "甲量化", [claim("甲量化可让各箱分配到相同数量的输入值。", ["E1"])], anchors
    )
    assert not mechanism_covered("甲量化", [claim("甲量化效果良好。", ["E1"])], anchors)
    assert mechanism_covered(
        "甲量化", [claim("甲量化基于分位数量化，使各箱分到相同数量的输入值。", ["E1"])], anchors
    )
    assert not mechanism_covered("甲量化", [claim("甲量化各箱表现相同。", ["E1"])], anchors)
    assert mechanism_covered(
        "甲量化", [claim("甲量化为优化状态分配分页内存，显存不足时换出到 CPU。", ["E1"])], anchors
    )
    assert not mechanism_covered("甲量化", [claim(text, ["missing"])], anchors)
    assert not mechanism_covered(
        "甲量化", [claim("甲量化尚未说明如何分配数值。", ["E1"])], anchors
    )


def test_based_on_quantization_is_action_but_better_accuracy_alone_is_not():
    evidence = anchor("E1", "甲量化 uses quantile quantization for normally distributed weights.")
    assert mechanism_covered("甲量化", [claim("甲量化基于分位数量化。", ["E1"])], {"E1": evidence})
    assert not mechanism_covered("甲量化", [claim("甲量化精度更好。", ["E1"])], {"E1": evidence})


@pytest.mark.asyncio
async def test_regular_qa_labels_original_identity_without_changing_evidence_or_auto_retry():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert "paper=P1;" in payload["input"] and "paper=P2;" in payload["input"]
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": '{"answer":"","claims":[]}'}]}]})

    provider = OpenAIResponsesProvider(api_key="test-only", model="test-model",
                                       base_url="https://example.test", timeout_seconds=5,
                                       transport=httpx.MockTransport(handler))
    evidence = [anchor("E1", "Unchanged original quote", doc="first"),
                anchor("E2", "Another quote", doc="second")]
    before = [e.model_dump() for e in evidence]
    await provider.generate_grounded_answer("Compare methods", evidence)
    assert len(requests) == 1 and before == [e.model_dump() for e in evidence]
