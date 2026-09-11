# PaperPilot 初版架构

## 目标

当前阶段已经形成一条可验证的纵向链路：PDF 上传、内容指纹、异步解析、版式感知 Document AST、版本候选提醒、证据检索、受约束回答、站内原文阅读、学术追踪、局域引用图谱以及证据约束综述。

## 组件

| 组件 | 职责 |
| --- | --- |
| Next.js Web | 上传、阅读、问答、翻译、学术追踪、引用图谱、综述与 Future Work 时间线 |
| FastAPI | 文献、Agent、检索推荐、局域图谱和证据综述 API |
| Celery + Redis | PDF 解析、整篇翻译、arXiv 定时刷新、重试和状态更新 |
| Agent Harness | 检索、证据门控、Provider 调用、引用校验和 Trace |
| Skill Registry | 带 Pydantic 输入 Schema 的本地工具发现与执行 |
| LLM Provider | 默认抽取式兜底；可选 OpenAI Responses API 问答与学术翻译 |
| Embedding Provider | 默认离线 Hash 基线；可选 OpenAI-compatible BGE-M3 等语义服务 |
| Reranker | 默认关闭；可选 Cohere-compatible 或 TEI Cross-Encoder 候选重排 |
| BGE Runtime | 可选 TEI Docker Profile，独立运行 BGE-M3 与 BGE Reranker |
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

## 阅读器联动

前端通过轻量级 `GET /documents/{id}/outline` 获取递归标题树，不下载包含所有页面块的完整 AST。阅读器将标题按原始层级渲染，点击节点后切换到对应页；翻页或从问答证据进入时，以当前页之前最近出现的标题作为活动章节。目录可折叠，移动端以抽屉形式覆盖画布。PDF 文件接口支持 Range 请求并暴露 `Accept-Ranges`、`Content-Length` 与 `Content-Range`，为超长文档保留按需加载能力。

问答证据携带的 PyMuPDF `bbox` 使用页面点坐标。前端以 PDF.js 报告的原始页面宽高将其裁剪并转换为百分比矩形，再作为独立覆盖层叠加到页面上；因此切换缩放比例不会改变高亮与原文的相对位置。无坐标证据仍保留页码定位，并在阅读器状态栏明确提示未高亮，避免伪造位置精度。

参考文献导航通过轻量级 `GET /documents/{id}/references` 同时返回结构化 References 和正文编号引用位置。后端只接受 AST 中真实存在的数字标签，并跳过参考文献条目自身，支持逗号分组与有界范围；前端仅对已知标签生成经过 HTML 转义的可点击文本层标记。点击正文 `[N]` 可跳到对应条目并高亮 BBox，侧栏则按条目聚合正文出现位置，用于反向跳回原文。未知编号不会生成链接，避免把公式编号误认为引用。

阅读器文本层支持浏览器原生选区。选中中英文段落后，前端自动推断目标语言并调用 `POST /agent/translate`；翻译面板同时保留原文与译文。翻译复用已配置的 LLM Provider，但使用独立 Structured Output Schema 和“只翻译、不补充”的学术翻译指令。抽取式零密钥 Provider 会显式返回不可用错误，因此界面不会展示未经模型翻译的占位结果。

页级双语模式调用 `POST /documents/{id}/translations/pages/{page}`。服务端从 AST 读取当前页文本块，在 80 段/24000 字符的有界预算内批量请求 Provider，并强制模型完整、唯一地回传原始 `block_id`。响应保留每段的原文、译文和 BBox；阅读器将 PDF 与译文并排展示，以归一化滚动进度实现左右双向联动。页级结果使用同一持久化结构缓存，因此刷新浏览器或移除 API Key 后仍能读取已经完成的译文。

整篇翻译通过 Celery `documents.translate` 任务逐页调用同一段落对齐函数。每页先写临时文件再原子替换，随后更新 manifest 中的完成页集合；进程中断不会产生半页结果。Manifest 绑定源 PDF SHA-256、目标语言、Provider、模型和总页数，恢复任务时只跳过签名相符的完整页面。前端每 2.5 秒读取任务状态并展示完成页数；当前阅读页一旦进入缓存即可提前显示，无需等待整篇结束。

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

解析前先对最多 12 个均匀分布的页面进行有界诊断，将文档标记为 `native_text`、`scanned_image`、`hybrid` 或 `empty`。扫描和混合文档由 `SelectiveOcrParser` 逐页检查文本密度，只对低文本页调用 PyMuPDF 集成的 Tesseract TextPage；Docker 镜像提供中英文语言数据，并默认用 `chi_sim+eng` 防止中文标题被英文模型优先误判。文本块归一化只移除连续汉字之间的 OCR Span 空隙，不改变拉丁词、代码或公式间距。OCR 结果沿用相同的页面块、BBox、Chunk 和 Evidence 数据结构。

长文档解析使用页面批次检查点。Worker 默认每 25 页生成一个只包含原始文本块、页面尺寸、表格快照和图片锚点的 JSON 分片，分片写入成功后再原子替换 `manifest.json`。Manifest 绑定源文件 SHA-256、解析器名称/版本、总页数和批次大小；任一条件变化都会从第一页重建，避免错误复用旧结果。任务重试时只重跑最后一个未完整提交的批次，全部分片合并后仍输出统一的 `schema_version=0.2.0` Document AST。最终 JSON 也采用原子替换，数据库 Chunk 和向量索引提交后才清理检查点。

`GET /documents/{id}/progress` 直接读取共享存储中的 manifest，因此无需为每一批页面写数据库。API 和 Worker 通过 Compose 的 `app_data` 共享同一检查点目录，前端每三秒刷新已完成页数；失败任务保留分片，删除文献时一并清理。

## 抗幻觉问答链路

```text
Question
   ↓
ResearchAgent Harness
   ↓ calls
search_evidence Skill → BM25 + Qdrant Dense/Sparse → RRF → Cross-Encoder → ranked EvidenceAnchor
   ↓ evidence score gate
Extractive Provider / OpenAI Responses API
   ↓ citation allow-list validation
Answer + Claims + Evidence + Trace
```

`DocumentChunk` 同时包含普通页面块和摘要、表格、图注、公式、参考文献等结构化节点。检索器会把章节名一并用于 BM25，并依据问题中的结构意图做类型加权。例如“表格中的分数”和“完整参考文献”会优先命中独立表格或引用节点，而不是大段正文。

每个 Chunk 同步为一个 Qdrant Point，使用 `dense` 和 `sparse` 两个命名向量。Qdrant 先对两路候选执行 RRF，再与原有 BM25 排名做一次偏重精确匹配的应用层 RRF。当前默认的 `HashEmbeddingProvider` 无需下载模型，便于离线回归和故障演示；它是向量基础设施基线而非真正的语义模型。`OpenAICompatibleEmbeddingProvider` 可连接 BGE-M3 等学习型模型的 `/v1/embeddings` 推理服务，负责批量请求和严格响应校验，同时继续使用 Hash Sparse 保留关键词召回。

向量集合按 Provider、模型签名和维度隔离。切换语义模型时创建新的 Collection，并由现有按需回填机制补建索引，从而避免模型或维度不兼容污染旧向量。远程推理异常不会使解析任务失败，查询会回退至 BM25；仓库默认不捆绑大模型权重，部署方可按 CPU/GPU 环境选择推理服务。

可选的 `HttpReranker` 只接收第一阶段的有限候选，调用 Cohere-compatible `/v1/rerank` Cross-Encoder 服务；`TeiReranker` 则调用 Hugging Face TEI 原生 `/rerank`，并兼容列表或 `ranks` 包装响应。最终排序融合 75% 归一化重排分数和 25% 原召回分数，并完整保留 Evidence 的文献、页码、BBox 和块 ID。响应索引无效、数值异常、超时或服务离线时，检索器保留 BM25/RRF 顺序降级，不中断问答链路。

Compose 中的 `local-bge` Profile 将 BGE-M3 Embedding 与 BGE Reranker 部署为两个独立 TEI 服务。两者具有独立模型缓存卷和 `/info` 健康检查；CPU 镜像为默认值，GPU Override 只改变运行镜像和设备请求。API 与 Worker 通过内部服务名访问模型，因此无需依赖宿主机端口；未启用 Profile 时现有 Hash/BM25 零模型路径完全不变。

Qdrant 不可达、索引失败或本地开发关闭向量检索时，`HybridRetriever` 会返回 BM25 结果。解析任务不会因向量服务故障而失败；后续查询会根据数据库 Chunk 数量自动补建缺失索引。

`EvidenceAnchor` 同时保存 `document_id`、`page_number`、`block_ids`、`bbox`、`section`、`source_type` 和原文摘录。LLM 只能引用本次检索生成的 `E1...En`，Harness 会在响应前再次校验引用白名单。默认抽取式 Provider 完全不调用外部模型，可用于无密钥演示和离线回归测试。

## 图谱与证据综述链路

局域引用图谱读取所有选定 Document AST 的 References，优先使用 arXiv ID 精确匹配，再使用有阈值的规范化题名覆盖率建立有向边。边从引用论文指向被引论文，并保存参考文献原文、页码、标签、正文提及次数、匹配分和理由。服务对局域有向图执行 PageRank，将其与入度归一化后组合为基石分；前端按基石、桥接、衍生、外围和孤立角色布局 SVG 节点。

证据综述从摘要、引言、讨论和结论提取 overview 证据，从限制、Future Work 标题及明确未来信号句提取方向证据。论文年份只使用学术来源元数据或 arXiv ID，不用上传时间冒充发表年份。若更晚论文的 overview 与旧方向达到概念覆盖阈值，且可选引用边进一步支持关系，方向会被标记为 `possibly_addressed`；系统不输出“已经解决”的强结论。

生成式综述复用 LLM Provider 的 Grounded Answer Schema，输入只包含有界的 `R1...Rn` 证据。响应中的 Claim 再经服务端证据白名单过滤；Provider 不可用时切换到抽取式综述，因此时间线、方向状态和原文跳转仍可离线运行。

离线评测器绕过 LLM，直接对 `LexicalRetriever` 或当前配置的 `HybridRetriever` 执行版本化 JSON 用例。文献通过 ID、文件名、标题或 arXiv ID/版本解析；证据按短文本特征、合法页码和结构类型匹配。聚合报告包含 Case Pass Rate、Evidence Recall、MRR、锚点有效率及 P50/P95 延迟，缺失文献作为显式失败。由此可以在更换 Embedding、Reranker 或融合权重时进行同一数据集对照。

## 下一阶段边界

1. 扩充官方 PDF 标注用例，并据此校准召回、重排权重和证据阈值。
2. 为向量模型升级增加蓝绿 Collection 和断点批量重建。
3. 接入论文题名、DOI 和 arXiv ID 自动检索下载，并保留来源与许可证元数据。
4. 为复杂扫描表格和公式增加专用识别适配器。
5. 将本地存储实现替换为 S3/MinIO 实现，保持 API 不变。
