# PaperPilot 初版架构

## 目标

当前阶段聚焦一条可验证的纵向链路：PDF 上传、内容指纹、异步解析、版式感知 Document AST、版本候选提醒、证据检索、受约束回答和原文跳转。翻译、检索推荐和引用图谱将通过稳定的数据边界逐步接入。

## 组件

| 组件 | 职责 |
| --- | --- |
| Next.js Web | 上传、文献列表、证据问答、页码跳转、版本提醒 |
| FastAPI | 文献 CRUD、Agent 状态、Skills 和问答 API |
| Celery + Redis | 长时 PDF 解析任务、重试和状态更新 |
| Agent Harness | 检索、证据门控、Provider 调用、引用校验和 Trace |
| Skill Registry | 带 Pydantic 输入 Schema 的本地工具发现与执行 |
| LLM Provider | 默认抽取式兜底；可选 OpenAI Responses API |
| Embedding Provider | 默认离线 Hash 基线；可选 OpenAI-compatible BGE-M3 等语义服务 |
| PostgreSQL | 文献元数据、任务状态、版本关系和 Chunk 索引 |
| 文件卷 | MVP 的 PDF 与 Document AST 存储 |
| Qdrant | Dense/Sparse 命名向量、Payload 过滤和 RRF 混合召回 |
| MinIO | 后续替换本地文件卷的对象存储 |

## 解析器边界

`app.parsers.base.DocumentParser` 是统一入口。当前 `PyMuPDFParser` 提供本地、无外部服务依赖的版式基线，已经支持字体感知标题、双栏阅读顺序、作者/机构、摘要、层级目录、矢量表格、嵌入图像、公式候选、参考文献和附录。后续可按标准测试集中的失败类型新增：

- `DoclingParser`：复杂布局、跨页表格、公式和图片增强；
- `GrobidEnricher`：作者机构、参考文献和引用坐标；
- `OcrParser`：无文本层 PDF 的 PaddleOCR 路径；
- `ParserRouter`：按文本密度和页面类型选择解析链路。

所有解析器输出同一个 Document AST，并保留 `page_number`、`bbox`、`block_id` 和 `reading_order`，供 RAG 引用和阅读器跳转复用。

当前 AST 的顶层结构为：

```text
Document
├── title / authors / affiliations / abstract
├── outline[]                  # 带 children 的层级目录
├── pages[].blocks[]           # 阅读顺序、栏位、字体、BBox
├── tables[].cells[]           # 行列号、文本、单元格 BBox
├── figures[] / formulas[]     # 图注或原文本候选及位置
├── references[]               # 标签、完整条目、来源块和 BBox
└── appendices[]               # 附录目录子树
```

表格检测仅在原生文本页执行，避免对 OCR TextPage 重复做不可靠的网格推断；全页扫描图不会被误当成论文插图。公式节点明确标记为 `text_candidate`，在接入公式识别模型前不声称能够无损还原 LaTeX。

解析前先对最多 12 个均匀分布的页面进行有界诊断，将文档标记为 `native_text`、`scanned_image`、`hybrid` 或 `empty`。扫描和混合文档由 `SelectiveOcrParser` 逐页检查文本密度，只对低文本页调用 PyMuPDF 集成的 Tesseract TextPage；Docker 镜像提供 `eng+chi_sim` 语言数据。OCR 结果沿用相同的页面块、BBox、Chunk 和 Evidence 数据结构。

## 抗幻觉问答链路

```text
Question
   ↓
ResearchAgent Harness
   ↓ calls
search_evidence Skill → BM25 + Qdrant Dense/Sparse → RRF → ranked EvidenceAnchor
   ↓ evidence score gate
Extractive Provider / OpenAI Responses API
   ↓ citation allow-list validation
Answer + Claims + Evidence + Trace
```

`DocumentChunk` 同时包含普通页面块和摘要、表格、图注、公式、参考文献等结构化节点。检索器会把章节名一并用于 BM25，并依据问题中的结构意图做类型加权。例如“表格中的分数”和“完整参考文献”会优先命中独立表格或引用节点，而不是大段正文。

每个 Chunk 同步为一个 Qdrant Point，使用 `dense` 和 `sparse` 两个命名向量。Qdrant 先对两路候选执行 RRF，再与原有 BM25 排名做一次偏重精确匹配的应用层 RRF。当前默认的 `HashEmbeddingProvider` 无需下载模型，便于离线回归和故障演示；它是向量基础设施基线而非真正的语义模型。`OpenAICompatibleEmbeddingProvider` 可连接 BGE-M3 等学习型模型的 `/v1/embeddings` 推理服务，负责批量请求和严格响应校验，同时继续使用 Hash Sparse 保留关键词召回。

向量集合按 Provider、模型签名和维度隔离。切换语义模型时创建新的 Collection，并由现有按需回填机制补建索引，从而避免模型或维度不兼容污染旧向量。远程推理异常不会使解析任务失败，查询会回退至 BM25；仓库默认不捆绑大模型权重，部署方可按 CPU/GPU 环境选择推理服务。

Qdrant 不可达、索引失败或本地开发关闭向量检索时，`HybridRetriever` 会返回 BM25 结果。解析任务不会因向量服务故障而失败；后续查询会根据数据库 Chunk 数量自动补建缺失索引。

`EvidenceAnchor` 同时保存 `document_id`、`page_number`、`block_ids`、`bbox`、`section`、`source_type` 和原文摘录。LLM 只能引用本次检索生成的 `E1...En`，Harness 会在响应前再次校验引用白名单。默认抽取式 Provider 完全不调用外部模型，可用于无密钥演示和离线回归测试。

## 下一阶段边界

1. 接入 Cross-Encoder 重排并用标注问答集校准证据阈值。
2. 为向量模型升级增加蓝绿 Collection 和断点批量重建。
3. 接入 PDF.js，通过现有 block 坐标实现答案高亮与引用跳转。
4. 为复杂扫描表格和公式增加专用识别适配器。
5. 将本地存储实现替换为 S3/MinIO 实现，保持 API 不变。
