# 离线 RAG 检索评测

评测器直接调用检索层，不调用 LLM。每个用例用文献身份、问题和一组可接受的证据特征描述金标准，避免把生成模型的措辞偏好混入召回质量。

## 指标

- `case_pass_rate`：一个用例的全部证据期望都命中且锚点有效时计为通过；
- `evidence_recall`：所有证据期望中被 Top-K 找到的比例；
- `mean_reciprocal_rank`：每个问题首个相关证据排名倒数的平均值；
- `anchor_valid_rate`：命中证据中具备页码、块 ID 和有限 BBox 的比例；
- `latency_ms_p50/p95`：单次检索延迟分位数。

缺失文献不会被静默跳过，而是记录为 `missing_document` 并计入失败。报告只保留最多 320 字符的证据预览，不复制论文长段正文。

## 运行

现有中文论文基线：

```powershell
Set-Location backend
python scripts/evaluate_retrieval.py evals/local_chinese_thesis.json `
  --retriever configured `
  --output ../output/evals/local-rag-configured.json `
  --summary-only `
  --strict
```

比赛指定 Transformer v1 入库后：

```powershell
python scripts/evaluate_retrieval.py evals/attention_v1.json `
  --retriever configured `
  --output ../output/evals/attention-v1.json `
  --summary-only `
  --strict
```

`--retriever lexical` 固定使用 BM25 基线；`configured` 使用当前环境配置的 Qdrant、Embedding 和 Reranker 链路。两份报告可用于判断接入模型后是真正提升了排名，还是只增加了复杂度。

## 用例格式

`documents` 支持 `document_id`、文件名、标题子串或 arXiv ID/版本定位。每个 `expectations` 至少包含一组 `text_contains_any`，还可以限定合法页码、证据类型和文献标题。一个事实出现在多处时，应列出所有同样可靠的页码，避免把更完整的表格证据误判为错误。
