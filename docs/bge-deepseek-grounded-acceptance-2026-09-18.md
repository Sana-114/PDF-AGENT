# BGE + DeepSeek 完整 RAG 验收（2026-09-18）

## 验收目标

在第 49 阶段 BM25 + DeepSeek 验收基础上，证明系统实际经过以下完整链路，而不是只显示模型配置后静默退回词法检索：

`PDF Chunk → BGE-M3 → Qdrant Dense/Sparse RRF → BM25 融合 → BGE Reranker → DeepSeek → PDF 锚点`

在线生成只使用公开论文 *Attention Is All You Need*（arXiv `1706.03762v1`）；本地私有论文没有发送给 DeepSeek。

## 运行配置

- Embedding：`BAAI/bge-m3`，1024 维；
- Vector DB：Qdrant `v1.19.1`；
- Reranker：`BAAI/bge-reranker-v2-m3`，TEI `/rerank`；
- LLM：`deepseek-flash`；
- GPU 推理镜像：`ghcr.io/huggingface/text-embeddings-inference:89-1.9`；
- BGE Collection：`paperpilot_chunks_v1_semantic_d790e73745_1024`；
- 公开论文索引：110 个 Chunk，110 个 Point。

模型权重位于 Docker 命名卷，DeepSeek Key 位于被 Git 忽略的 `.env`，两者均不进入仓库。

## 服务与索引检查

真实服务探针返回：

| 检查 | 结果 |
| --- | ---: |
| BGE-M3 输出维度 | 1024 |
| Reranker 相关句 Top-1 | 通过 |
| PDF 预期 Chunk | 110 |
| 写入 Qdrant Point | 110 |
| 存储后 Point 核对 | 110 |
| 单文重建耗时 | 1173 ms |

## 检索对比

| 指标 | BM25 基线 | BGE + Qdrant + Reranker |
| --- | ---: | ---: |
| 用例通过率 | 100%（4/4） | 100%（4/4） |
| 证据召回率 | 100%（5/5） | 100%（5/5） |
| MRR | 0.875 | 1.000 |
| 锚点有效率 | 100% | 100% |
| 检索模式匹配率 | 不适用 | 100% `reranked` |
| 热查询 P50 | 13 ms | 234 ms |
| 热查询 P95 | 15 ms | 415 ms |

BGE 的四个问题均把金标准证据排到第 1。增加的延迟来自本地语义向量和 Cross-Encoder 推理，换取了更稳定的 Top-1 排名；时间来自小样本本机验收，不应外推为生产吞吐量。

## DeepSeek 端到端结果

| 指标 | 结果 |
| --- | ---: |
| 用例通过率 | 100%（5/5） |
| 答案事实召回率 | 100%（6/6） |
| 证据召回率 | 100%（5/5） |
| 锚点有效率 | 100% |
| 引用合法率 | 100% |
| 声明接地率 | 100% |
| 无依据拒答率 | 100%（1/1） |
| Provider 匹配率 | 100% |
| 检索模式匹配率 | 100% `reranked` |
| LLM fallback 率 | 0% |
| 总延迟 P50 / P95 | 2294 ms / 3613 ms |

## 防止“伪 BGE 通过”

两个评测 CLI 新增 `--require-retrieval-mode reranked`。开启后会检查每一条返回证据的真实模式；如果向量服务或重排服务异常并自动退回 `lexical`/`hybrid`，即使答案碰巧正确，严格评测仍会失败。单元测试覆盖了该反例。

显式 `reindex_vectors.py` 会核对数据库 Chunk 数、实际写入数与 Qdrant Point 数，避免旧模型 Collection、部分索引或首次查询懒加载影响评测可信度。

## 一键复现

GPU 模式：

```powershell
.\scripts\run_grounded_rag_e2e.ps1 `
  -Retriever configured `
  -LocalBge `
  -Gpu
```

CPU 模式省略 `-Gpu`。脚本按顺序加载 `.env` 与 `.env.bge`，因此能同时保留 DeepSeek 配置与 BGE 覆盖项。动态报告保存于 `backend/tmp/grounded-rag/`，不会进入 Git。

## 当前边界

本次数据集规模较小，证明的是链路真实性与比赛指定论文上的可复现正确性，不是通用检索排行榜。官方扫描 PDF、541 页教材和多文冲突问答到位后，仍需使用相同的强制模式门禁扩展验收。
