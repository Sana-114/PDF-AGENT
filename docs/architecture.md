# PaperPilot 初版架构

## 目标

当前阶段聚焦一条可验证的纵向链路：PDF 上传、内容指纹、异步解析、结构化页面块、版本候选提醒、证据检索、受约束回答和原文跳转。OCR、翻译、检索推荐和引用图谱将通过稳定的数据边界逐步接入。

## 组件

| 组件 | 职责 |
| --- | --- |
| Next.js Web | 上传、文献列表、证据问答、页码跳转、版本提醒 |
| FastAPI | 文献 CRUD、Agent 状态、Skills 和问答 API |
| Celery + Redis | 长时 PDF 解析任务、重试和状态更新 |
| Agent Harness | 检索、证据门控、Provider 调用、引用校验和 Trace |
| Skill Registry | 带 Pydantic 输入 Schema 的本地工具发现与执行 |
| LLM Provider | 默认抽取式兜底；可选 OpenAI Responses API |
| PostgreSQL | 文献元数据、任务状态、版本关系和 Chunk 索引 |
| 文件卷 | MVP 的 PDF 与 Document AST 存储 |
| Qdrant | 后续混合检索和证据 Chunk 索引 |
| MinIO | 后续替换本地文件卷的对象存储 |

## 解析器边界

`app.parsers.base.DocumentParser` 是统一入口。当前 `PyMuPDFParser` 提供快速基线，后续新增：

- `DoclingParser`：布局、阅读顺序、表格、公式和图片；
- `GrobidEnricher`：作者机构、参考文献和引用坐标；
- `OcrParser`：无文本层 PDF 的 PaddleOCR 路径；
- `ParserRouter`：按文本密度和页面类型选择解析链路。

所有解析器输出同一个 Document AST，并保留 `page_number`、`bbox`、`block_id` 和 `reading_order`，供 RAG 引用和阅读器跳转复用。

## 抗幻觉问答链路

```text
Question
   ↓
ResearchAgent Harness
   ↓ calls
search_evidence Skill → page-local DocumentChunk → ranked EvidenceAnchor
   ↓ evidence score gate
Extractive Provider / OpenAI Responses API
   ↓ citation allow-list validation
Answer + Claims + Evidence + Trace
```

`EvidenceAnchor` 同时保存 `document_id`、`page_number`、`block_ids`、`bbox`、`section` 和原文摘录。LLM 只能引用本次检索生成的 `E1...En`，Harness 会在响应前再次校验引用白名单。默认抽取式 Provider 完全不调用外部模型，可用于无密钥演示和离线回归测试。

## 下一阶段边界

1. 为 Document AST 增加 section、table、figure、formula、reference 节点。
2. 将现有 page-local Chunk 写入 Qdrant dense/sparse named vectors。
3. 接入 Cross-Encoder 重排并用标注问答集校准证据阈值。
4. 接入 PDF.js，通过现有 block 坐标实现答案高亮与引用跳转。
5. 将本地存储实现替换为 S3/MinIO 实现，保持 API 不变。
