"""Bounded source statements for comparisons, not a general entailment judge."""

import re
import unicodedata

from app.llm.base import GeneratedClaim, LLMResponseError


def comparison_support_schema(subjects: list[str], paper_ids: list[str], focus: str = "") -> dict:
    return {
        "type": "array", "minItems": 2, "maxItems": 2,
        "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "subject": {"type": "string", "enum": subjects},
                "paper_id": {"type": "string", "enum": paper_ids},
                "text": {"type": "string", "minLength": 1, "maxLength": 280,
                         "description": "One minimal single-source premise for: " + focus
                         + ". Leave separately requested detail-slot facts in their own slots; "
                         "do not add deployment operations, benchmarks or unasked precisions. "
                         "Use only symbols and specifics explicitly present in the selected "
                         "evidence_ids; omit unneeded symbols rather than borrowing them from "
                         "an uncited passage."},
                "evidence_ids": {
                    "type": "array", "minItems": 1, "maxItems": 3,
                    "items": {"type": "string"},
                },
            }, "required": ["subject", "paper_id", "text", "evidence_ids"],
        },
    }


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def compose_source_comparison(row: dict, *, chinese: bool) -> dict:
    """Compose a selected relation operator and two attributed original premises.

    Live generation leaves text empty. Source validation and the independent
    reviewer still check every premise and the operator. Add no technical facts;
    do not rewrite or salvage a free draft.
    """
    if not isinstance(row, dict) or set(row) != {"status", "text", "relation", "source_statements"}:
        raise LLMResponseError("机制对照只接受状态、空文本和两份原文前提。")
    if row["status"] == "unavailable":
        if (row["text"] is not None or row["source_statements"] != []
                or row["relation"] != "unavailable"):
            raise LLMResponseError("无依据对照不能包含替代文本或前提。")
        return {key: row[key] for key in ("status", "text", "source_statements")}
    if row["status"] != "inferred" or row["text"] != "":
        raise LLMResponseError("机制对照文本由程序组成，不接受模型的自由比较句。")
    if not isinstance(row["relation"], str) or row["relation"] not in {"contrast", "similarity"}:
        raise LLMResponseError("必须明确选择差异或共同处理关系，并经原文校审。")
    units = row["source_statements"]
    if not isinstance(units, list) or len(units) != 2 or any(
        not isinstance(unit, dict) or not isinstance(unit.get("subject"), str)
        or not isinstance(unit.get("text"), str) or not 1 <= len(unit["text"].strip()) <= 200
        for unit in units
    ):
        raise LLMResponseError("对照必须有两条简洁的原文前提；不截断或补写。")
    separator = "；" if chinese else "; "
    heading = {
        "contrast": "在所问机制上，二者处理方式不同：" if chinese else
        "The methods differ on the requested mechanism: ",
        "similarity": "在所问机制上，二者有共同处理方式：" if chinese else
        "The methods share a treatment of the requested mechanism: ",
    }[row["relation"]]
    text = heading + separator.join(f'{u["subject"]}: {u["text"]}' for u in units)
    if len(text) > 500:
        raise LLMResponseError("原文对照超长；不截断前提。")
    return {"status": row["status"], "text": text, "source_statements": units}


def _technical_terms(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,10})(?![A-Za-z0-9])", text))


def _unreported_precision(text: str, support: str) -> bool:
    claimed = re.search(
        r"(?:原(?:始)?|全|低|高|存储|计算|数值)(?:的)?精度|"
        r"(?:storage|compute|computation|original|full|low|high|numerical)[- ]precision",
        text, re.I,
    )
    reported = re.search(
        r"\b(?:precision|BFloat16|BF16|FP(?:16|32|64)|\d+[- ]bits?)\b|精度|位宽", support, re.I,
    )
    return bool(claimed and not reported)


def _missing_terms(text: str, support: str, excluded: set[str]) -> bool:
    for term in _technical_terms(text) - excluded:
        literal = r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])"
        # An explicitly expanded common acronym is not an absent algorithm.
        # This is a query-language alias, not a paper or expected answer mapping.
        expansions = {"RL": r"\breinforcement\s+learning\b", "BF16": r"\bBFloat16\b"}
        pattern = literal + ("|" + expansions[term] if term in expansions else "")
        if not re.search(pattern, support, re.I):
            return True
    return False


def _missing_matrix_symbols(text: str, support: str) -> bool:
    """A named matrix symbol needs literal support in its selected original.

    Only explicit uppercase single-letter matrix labels are recognized, not
    arbitrary initials or English articles. Presence is not an entailment proof.
    """
    symbol = r"[A-Z](?![A-Za-z0-9_])"
    labels = re.finditer(
        rf"(?:矩阵\s*|\b(?i:matri(?:x|ces))\s+)(?P<labels>{symbol}"
        rf"(?:\s*(?:、|,|，|and|和|与|及|/)\s*{symbol})*)", text,
    )
    return any(
        not re.search(r"(?<![A-Za-z0-9_])" + re.escape(label) + r"(?![A-Za-z0-9_])", support)
        for match in labels for label in re.findall(r"[A-Z]", match["labels"])
    )


def validate_comparison_support(row: dict, anchors: dict, subjects: list[str], *,
                                derive_citations: bool = False,
                                validate_relation: bool = True) -> list[str]:
    # Imported late to keep the provider/service import order independent.
    from app.services.grounded_text import audit_grounded_claims

    units = row.get("source_statements")
    if row["status"] == "unavailable":
        if units != []:
            raise LLMResponseError("无依据比较不能附带猜测的来源结论。")
        return []
    if row["status"] != "inferred":
        raise LLMResponseError("跨原文综合需明确标记为推断，不能冒称原文直接陈述。")
    if not isinstance(units, list) or len(units) != 2:
        raise LLMResponseError("比较必须包含两份原文各自的原子结论及逐字摘录。")
    # Match precisely the paper labels actually sent to this model request.
    labels = {}
    for item in anchors.values():
        labels.setdefault(item.document_id, f"P{len(labels) + 1}")
    expected = {labels[e.document_id] for e in anchors.values() if e.source_type != "reference"}
    seen_subjects, seen_papers, proof_ids, proofs, statements = set(), set(), [], [], []
    excluded = {term for name in subjects for term in _technical_terms(name)}
    for unit in units:
        source_field = "evidence_ids" if derive_citations else "citations"
        if not isinstance(unit, dict) or set(unit) != {"subject", "paper_id", "text", source_field}:
            raise LLMResponseError("比较来源结论结构无效。")
        subject, paper, text, citations = (unit[k] for k in
                                           ("subject", "paper_id", "text", source_field))
        if (not isinstance(subject, str) or subject not in subjects or subject in seen_subjects
                or not isinstance(paper, str) or paper not in expected or paper in seen_papers
                or not isinstance(text, str) or not 1 <= len(text.strip()) <= 280
                or not isinstance(citations, list) or not 1 <= len(citations) <= 3):
            raise LLMResponseError("两份原文的主体／标签／原子结论必须分别明确。")
        local = []
        for citation in citations:
            if derive_citations:
                key = citation
                item = anchors.get(key.upper()) if isinstance(key, str) else None
                if item is None or not item.quote.strip():
                    raise LLMResponseError("来源结论必须选择已发送的有效原文段落。")
                quote = item.quote
            elif not isinstance(citation, dict) or set(citation) != {"evidence_id", "quote"}:
                raise LLMResponseError("原子结论的摘录结构无效。")
            else:
                key, quote = citation["evidence_id"], citation["quote"]
                item = anchors.get(key.upper()) if isinstance(key, str) else None
            if (item is None or item.source_type == "reference"
                    or labels[item.document_id] != paper or not isinstance(quote, str)
                    or not derive_citations and (not 24 <= len(quote) <= 400
                        or _normalized(quote) not in _normalized(item.quote))):
                raise LLMResponseError("逐字摘录必须来自该原文已发送的正文，不能拼接或借用。")
            if any(previous.evidence_id == item.evidence_id for previous in local):
                raise LLMResponseError("同一证据应给出单个连续摘录，不能用重复编号覆盖先前依据。")
            local.append(item.model_copy(update={"quote": quote}, deep=True))
            if item.evidence_id not in proof_ids:
                proof_ids.append(item.evidence_id)
        support = "\n".join(e.quote for e in local)
        if _missing_terms(text, support, excluded):
            raise LLMResponseError("原子结论中的技术缩写未获该原文摘录支持。")
        if _missing_matrix_symbols(text, support):
            raise LLMResponseError("原子结论中的矩阵符号未在该原文所选摘录中出现。")
        if (re.search(r"全精度|full[- ]precision", text, re.I)
                and not re.search(r"全精度|full[- ]precision", support, re.I)):
            raise LLMResponseError("不能由冻结／全秩推断未报告的全精度设定。")
        if _unreported_precision(text, support):
            raise LLMResponseError("该原文没有直接报告原子结论所声称的存储／计算精度。")
        accepted, _ = audit_grounded_claims(
            [GeneratedClaim(text, [e.evidence_id for e in local])], local,
        )
        if not accepted:
            raise LLMResponseError("原子结论未通过摘录范围内的数值或限定检查。")
        seen_subjects.add(subject)
        seen_papers.add(paper)
        proofs.extend(local)
        statements.append((subject, text, support))
    if seen_subjects != set(subjects) or seen_papers != expected:
        raise LLMResponseError("比较来源未覆盖两个所问主体及原文。")
    if not derive_citations:
        # Saved legacy output retains its original independent citation-array
        # check. Never retroactively repair or rescore that historical output.
        declared = {key.strip().strip("[]").upper() for key in row["evidence_ids"]}
        if declared != {key.upper() for key in proof_ids}:
            raise LLMResponseError("比较引用必须与原子结论实际使用的摘录一致；不自动补引用。")
    if not validate_relation:
        # Only valid, scoped premises can reach the editor. The draft relation
        # is not published; final rendering always runs the checks below.
        return proof_ids
    # Check named technical components in explicit subject clauses against that
    # subject's own proof, rather than any passage from the other original.
    text = row["text"]
    subject_pattern = "|".join(re.escape(name) for name in subjects)
    clause_break = (r"[。;；]|\bwhereas\b|[,，]\s*(?=(?:(?:而|但|while)\s*)?(?:"
                    + subject_pattern + r")(?![A-Za-z0-9]))")
    for subject, statement, support in statements:
        other = next(name for name in subjects if name != subject)
        for clause in re.split(clause_break, text, flags=re.I):
            own = re.search(r"(?<![A-Za-z0-9])" + re.escape(subject)
                            + r"(?![A-Za-z0-9])", clause, re.I)
            counterpart = re.search(r"(?<![A-Za-z0-9])" + re.escape(other)
                                    + r"(?![A-Za-z0-9])", clause, re.I)
            if own and not counterpart:
                if _missing_terms(clause, statement + support, excluded):
                    raise LLMResponseError("比较子句借用了另一原文的技术组件依据。")
                if (re.search(r"全精度|full[- ]precision", clause, re.I)
                        and not re.search(r"全精度|full[- ]precision", support, re.I)):
                    raise LLMResponseError("比较子句的精度设定缺少该原文的直接摘录支持。")
                if _unreported_precision(clause, support):
                    raise LLMResponseError("比较子句不能从冻结／低秩说明推断未报告的原始精度。")
    accepted, _ = audit_grounded_claims([GeneratedClaim(text, list(proof_ids))], proofs)
    if not accepted:
        raise LLMResponseError("比较结论未通过其逐字摘录范围内的来源检查。")
    # These exact IDs were explicitly selected by the model above. No lookup
    # for additional supporting passages or citation guesses is performed.
    return proof_ids
