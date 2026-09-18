# RAG 检索与生成评测

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

## DeepSeek 端到端抗幻觉评测

`evaluate_grounded_rag.py` 在检索评测之上调用真实 Agent 和配置的 LLM，并分别检查：

- 回答是否覆盖预期事实，检索证据是否命中；
- 页码、块 ID、BBox 是否可回到 PDF 原文；
- 每条声明是否只引用本次返回的证据 ID；
- 声明是否被金标准证据或其引用原文中的关键型号/数值支持；
- 无依据问题是否明确拒答，以及是否发生抽取式 fallback；
- 实际 provider 是否与数据集要求的 `deepseek` 一致。

PDF 文本会被视为不可信引用内容，模型不得执行正文中嵌入的命令。评测匹配会容忍 PDF 抽取产生的数字、公式间空格，但不会放宽答案事实或新数值检查。

配置好本地 `.env` 后，可一键上传公开 v1 PDF、等待异步解析并执行离线与在线两级严格验收：

```powershell
.\scripts\run_grounded_rag_e2e.ps1
```

默认使用可重复的 BM25 `lexical` 基线；需要验收当前 Qdrant/Embedding/Reranker 配置时使用：

```powershell
.\scripts\run_grounded_rag_e2e.ps1 -Retriever configured
```

启用仓库内置 BGE-M3、Qdrant、BGE Reranker 与 DeepSeek 的完整 GPU 链路：

```powershell
.\scripts\run_grounded_rag_e2e.ps1 `
  -Retriever configured `
  -LocalBge `
  -Gpu
```

CPU 模式省略 `-Gpu`。`-LocalBge` 会同时加载 `.env` 与 `.env.bge`：前者保留 DeepSeek Key，后者覆盖 Embedding/Reranker 配置；脚本还会实际请求两个 BGE 服务、显式重建当前公开文献的向量，并要求全部评测证据的 `retrieval_mode=reranked`。任何 Qdrant、Embedding 或 Reranker 故障导致的 BM25/hybrid fallback 都会使严格验收失败，而不是被汇总指标掩盖。

结果保存在 `backend/tmp/grounded-rag/`，不会提交到 Git。脚本不会打印 API Key，也不会向模型发送库中的其他私有文献；仅数据集中选中的公开 arXiv 文献会进入请求上下文。

也可以单独为当前 Embedding 模型重建指定文献的 Collection：

```powershell
docker compose --env-file .env --env-file .env.bge exec backend `
  python scripts/reindex_vectors.py --document-id <DOCUMENT_ID> --strict
```
