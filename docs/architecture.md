# PaperPilot 初版架构

## 目标

初版聚焦一条可验证的纵向链路：PDF 上传、内容指纹、异步解析、结构化页面块、版本候选提醒和原文访问。问答、OCR、翻译、检索推荐、引用图谱均通过稳定的数据边界逐步接入。

## 组件

| 组件 | 职责 |
| --- | --- |
| Next.js Web | 上传、文献列表、状态轮询、版本提醒 |
| FastAPI | 文献 CRUD、文件访问、结构化结果 API |
| Celery + Redis | 长时 PDF 解析任务、重试和状态更新 |
| PostgreSQL | 文献元数据、任务状态、版本关系 |
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

## 下一阶段边界

1. 为 Document AST 增加 section、table、figure、formula、reference 节点。
2. 将页面块分层切块，写入 Qdrant dense/sparse named vectors。
3. 定义 Claim/Evidence 问答响应，证据包含 document、page、bbox、block。
4. 接入 PDF.js，通过 block 坐标实现答案高亮与引用跳转。
5. 将本地存储实现替换为 S3/MinIO 实现，保持 API 不变。

