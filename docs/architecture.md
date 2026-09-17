# PaperPilot 初版架构

## 目标

当前阶段已经形成一条可验证的纵向链路：PDF 上传、内容指纹、异步解析、版式感知 Document AST、版本候选提醒、证据检索、受约束回答、站内原文阅读、学术追踪、局域引用图谱以及证据约束综述。

## 组件

| 组件 | 职责 |
| --- | --- |
| Next.js Web | 上传、阅读、问答、翻译、学术追踪、引用图谱、综述、写作与数据可视化 |
| FastAPI | 文献、Agent、检索推荐、局域图谱、证据综述和 CSV 分析 API |
| Celery + Redis | PDF 解析、整篇翻译、arXiv 定时刷新、重试和状态更新 |
| Agent Harness | 检索、证据门控、Provider 调用、引用校验和 Trace |
| Skill Registry | 带 Pydantic 输入 Schema 的本地工具发现与执行 |
| LLM Provider | 默认抽取式兜底；可选 OpenAI Responses API 问答与学术翻译 |
| Writing Guard | 将检索证据转换为论文框架，并从持久化学术元数据重建 References |
| Translation Guard | 分段调用生成式 LLM，并强制恢复公式、引用、代码、数字与术语占位符 |
| Visualization Engine | 审计 CSV 字段，生成可复核图表数据、Figure Caption 与 Matplotlib 脚本 |
| Diagram Compiler | 将用户关系路径规范化为图，并编译 Mermaid、DOT、TikZ 与 Matplotlib 脚本 |
| Embedding Provider | 默认离线 Hash 基线；可选 OpenAI-compatible BGE-M3 等语义服务 |
| Reranker | 默认关闭；可选 Cohere-compatible 或 TEI Cross-Encoder 候选重排 |
| BGE Runtime | 可选 TEI Docker Profile，独立运行 BGE-M3 与 BGE Reranker |
| PostgreSQL | 文献元数据、任务状态、版本关系、Chunk 索引、术语库与人工审校译稿 |
| 文件卷 | MVP 的 PDF 与 Document AST 存储 |
| Qdrant | Dense/Sparse 命名向量、Payload 过滤和 RRF 混合召回 |
| MinIO | 后续替换本地文件卷的对象存储 |

## 解析器边界

`app.parsers.base.DocumentParser` 是统一入口。当前 `PyMuPDFParser` 提供本地、无外部服务依赖的版式基线，已经支持字体感知标题、双栏阅读顺序、作者/机构、摘要、层级目录、有框矢量表格、图注引导的无框表格、嵌入图像、公式文本及规范化 LaTeX 候选、参考文献和附录。后续可按标准测试集中的失败类型新增：

- `DoclingParser`：复杂布局、跨页表格、公式和图片增强；
- `GrobidEnricher`：作者机构、参考文献和引用坐标；
- `OcrParser`：无文本层 PDF 的 PaddleOCR 路径；
- `ParserRouter`：按文本密度和页面类型选择解析链路。

所有解析器输出同一个 Document AST，并保留 `page_number`、`bbox`、`block_id` 和 `reading_order`，供 RAG 引用和阅读器跳转复用。

解析完成后，系统对同一 arXiv ID 或高相似题名候选计算正文指纹重合度。命中候选只产生待处理提醒，不自动删除文件。`GET /documents/{id}/duplicates/diff` 重建两个 AST 的有序正文，报告指纹重合度、页数/文本量/结构节点变化及标题集合差异；候选修订段落必须返回来源版本中的页码和 Block ID，并明确标为“可能新增/删除”，不把词项差异包装成语义结论。`POST /documents/{id}/duplicates/resolve` 要求用户明确选择保留库中版本、用当前版本替换或两版并存。删除路径同时清理原文件、解析结果、翻译缓存、向量索引和来源记录；两版并存只关闭当前提醒，不改写任一 PDF。

## 阅读器联动

前端通过轻量级 `GET /documents/{id}/outline` 获取递归标题树，不下载包含所有页面块的完整 AST。阅读器将标题按原始层级渲染，点击节点后切换到对应页；翻页或从问答证据进入时，以当前页之前最近出现的标题作为活动章节。目录可折叠，移动端以抽屉形式覆盖画布。PDF 文件接口支持 Range 请求并暴露 `Accept-Ranges`、`Content-Length` 与 `Content-Range`，为超长文档保留按需加载能力。

问答证据携带的 PyMuPDF `bbox` 使用页面点坐标。前端以 PDF.js 报告的原始页面宽高将其裁剪并转换为百分比矩形，再作为独立覆盖层叠加到页面上；因此切换缩放比例不会改变高亮与原文的相对位置。无坐标证据仍保留页码定位，并在阅读器状态栏明确提示未高亮，避免伪造位置精度。

参考文献导航通过轻量级 `GET /documents/{id}/references` 同时返回结构化 References 和正文编号引用位置。后端只接受 AST 中真实存在的数字标签，并跳过参考文献条目自身，支持逗号分组与有界范围；前端仅对已知标签生成经过 HTML 转义的可点击文本层标记。点击正文 `[N]` 时，阅读器把点击点换算回 PDF 坐标，从同页同标签候选中选择最近的 AST 引用块，记录来源后跳到 References 条目并高亮 BBox；用户可一键返回原点击位置，或循环浏览该文献的上一处/下一处正文引用。侧栏仍按条目聚合全部出现位置，未知编号不会生成链接，避免把公式编号误认为引用。

阅读器文本层支持浏览器原生选区。选中中英文段落后，前端自动推断目标语言并调用 `POST /agent/translate`；翻译面板同时保留原文与译文。翻译复用已配置的 LLM Provider，但使用独立 Structured Output Schema 和“只翻译、不补充”的学术翻译指令。抽取式零密钥 Provider 会显式返回不可用错误，因此界面不会展示未经模型翻译的占位结果。

页级双语模式调用 `POST /documents/{id}/translations/pages/{page}`。服务端从 AST 读取当前页文本块，在 80 段/24000 字符的有界预算内批量请求 Provider，并强制模型完整、唯一地回传原始 `block_id`。响应保留每段的原文、译文和 BBox；阅读器将 PDF 与译文并排展示，以归一化滚动进度实现左右双向联动，并提供显式开关防止用户需要独立查看某一侧时被反向拖动。页级结果使用同一持久化结构缓存，因此刷新浏览器或移除 API Key 后仍能读取已经完成的译文。

整篇翻译通过 Celery `documents.translate` 任务逐页调用同一段落对齐函数。每页先写临时文件再原子替换，随后更新 manifest 中的完成页集合；进程中断不会产生半页结果。Manifest 绑定源 PDF SHA-256、目标语言、Provider、模型和总页数，恢复任务时只跳过签名相符的完整页面。前端每 2.5 秒读取任务状态并展示完成页数；当前阅读页一旦进入缓存即可提前显示，无需等待整篇结束。

任务达到 `completed` 后，`GET /documents/{id}/translations/{language}/export` 按页码重新读取全部原子缓存，并在发现缺页时拒绝生成不完整文件。Markdown 导出保留页码锚点、Block ID、原文与译文；自包含 HTML 对论文题名和所有段落执行 HTML 转义，并提供适合屏幕、移动端和打印的双栏样式。导出过程只编排已经核验并落盘的译文，不再次调用模型或改写公式、代码、数字和引用。

当前 AST 的顶层结构为：

```text
Document
├── title / authors / affiliations / abstract
├── outline[]                  # 带 children 的层级目录
├── pages[].blocks[]           # 阅读顺序、栏位、字体、BBox
├── tables[].cells[]           # 行列号、文本、单元格 BBox 与真实页码
├── tables[].segments[]        # 跨页分段的页码、BBox 和行范围
├── figures[] / formulas[]     # 图注、原文本、LaTeX 候选及位置
├── references[]               # 标签、完整条目、来源块和 BBox
└── appendices[]               # 附录目录子树
```

原生文本页先用页面中的学术表格编号提示筛选候选页：有框表格调用矢量网格分析，无框表格只在可信图注下方根据词坐标聚合文本行，并要求至少两行拥有重复列锚点；两种结果按区域包含率去重。这避免在 500 页以上文档的纯正文页扫描绘图对象，也避免把 `Table 2 summarizes ...` 一类正文句子识别成新图注。若上一页表格贴近页底而下一页没有图注，解析器只对原生文本续页执行一次受限网格补检；列数、归一化左右边界和重复表头全部一致时才加入候选。随后跨页续接器还要求页面相邻和明确的同编号续表或重复表头，合并时删除重复表头，并为每个分段与单元格保留真实页码。RAG 按分段生成独立证据 Chunk，第二页数据不会跳回第一页。OCR 页使用独立的图注引导栅格恢复：只有出现 `Table N / 表 N` 时才分析横纵线交点并对空单元格局部二次 OCR，只有出现 `Figure N / 图 N` 时才根据相邻墨迹带生成裁剪区域。全页扫描图本身不会被误当成论文插图。复杂合并单元格、旋转表格、没有重复表头或续表标记的跨页表格，以及无图注图像仍需要专用模型或适配器。

公式节点保留原始文本、Block ID、页码和 BBox，并把明确的 Unicode 数学符号、希腊字母、上下标及 `sum/sqrt` 记法规范化为 `latex` 字段，标记为 `normalized_latex_candidate`。该字段同时进入独立公式证据 Chunk，便于检索，但它是确定性候选而不是数学 OCR 真值；二维分式、矩阵、根号范围和多行对齐仍需 Pix2Text/Mathpix 等公式识别适配器。

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

写作 Copilot 以用户 idea 构造专门的检索问题，复用 ResearchAgent 获得经过白名单校验的 Claim 和 Evidence。框架的事实段落只由这些 Claim 与原文摘录构成；References 则完全绕过模型输出，按本次证据涉及的 `document_id` 查询 Document 和 PaperSource。格式化器只输出实际存在的作者、年份、Venue、DOI 或 arXiv ID，并向前端暴露 `verified_fields` 与 provenance，防止模型生成的虚构书目进入稿件。

独立学术翻译工作台复用同一 LLM Provider，但在调用模型前先识别 LaTeX、代码块、行内代码、数字引用、URL、DOI 和独立数值，并替换为唯一的代码式占位符。用户术语表也进入同一保护链路，恢复时使用用户指定的目标术语。模型必须逐段返回原始 `segment-id`，服务端检查 ID 集合及每个占位符恰好出现一次；缺段、额外段、空段或占位符损坏都会拒绝整个结果，不展示貌似完整的译文。

文本按原始空行拆段，并在最多 40 段或 12000 字符的请求预算内分批发送，最终按原位置恢复分隔符。摘要模式要求模型使用简洁的期刊摘要表达，但不补造原文缺失的目的、方法或结果；正文模式保留标题、列表、段落作用和论断强度。默认抽取式 Provider 仍明确返回不可用，只有配置支持翻译的生成式 LLM 后才会产生译文。

翻译工作区在 PostgreSQL 中持久化 `TranslationGlossary` 与 `AcademicTranslationDraft`。术语库由名称和语言方向唯一约束，每条记录保存经过 Schema 清洗、去重且不超过 50 项的映射。译稿保存源文、人工可编辑译文、语言方向、文档类型、术语库关联以及生成模型来源；删除术语库只解除关联，不级联删除译稿。

模型输出默认为 `draft`。只有用户明确提交审校操作时才写入 `reviewed` 和服务器时间戳；若后续 PATCH 修改译文但没有同时重新确认审校，服务端强制清除审校时间并退回 `draft`。Markdown 在浏览器端从当前可见源文和译文确定性构造，不再次调用 LLM，因此导出不会引入新的生成内容。

## 科研数据可视化链路

CSV 可视化端点只接受有界的 CSV/TSV 上传，自动识别 UTF-8、GB18030、分隔符、重复列名和不规则行。原始值不会被覆盖；字段审计分别报告类型、缺失数、去重数、数值范围与均值。折线图用于日期序列，柱状图用于分类比较，用户也可显式选择图表类型以及 X、Y 和分组列。三维雷达图要求至少三个数值指标，并在当前展示记录内逐指标执行 min-max 归一化；透视层高仅用于区分记录，不编码额外数据。

分组数据按 X 轴类别和可选组别建立独立桶，仅使用有效数值计算算术平均值。误差棒可选择样本标准差、标准误或 `1.96 × SEM` 的正态近似 95% 置信区间；少于两个有效观测时不生成误差并返回告警。结构化响应同时携带每个绘制点的有效样本量，以及各序列基于全部已分析有效行计算的计数、均值、样本标准差、最小值和最大值。图表和生成的 Matplotlib 脚本共用同一组聚合结果，避免前后端重复计算产生口径漂移。

图注生成不调用 LLM。服务端从实际绘制点计算首尾变化、最高值和最低值，并将这些事实同时作为结构化 `caption_facts` 返回。缺失值保持为空，超出显示预算的数据采用首尾覆盖的等距抽样并给出警告，避免图表看似完整却改变数据含义。前端使用原生 SVG 渲染可交互预览和误差棒，可下载包含内联样式的独立 SVG；API 同时返回等价的 Matplotlib 脚本，以 300 DPI 输出用于论文排版和复现实验图。

## 架构拓扑编译链路

架构图输入采用显式关系路径，例如 `PDF 上传 -> 版式解析 -> Document AST`。服务端只把用户实际写出的名称注册为节点，按首次出现顺序去重，并合并重复边；纯自然语言但没有关系箭头的输入会被拒绝，而不会由模型补造组件。拓扑布局先对有向无环部分做分层排序，循环节点单独展开并保留原边，同时向用户返回告警。

规范化节点、边和坐标作为单一事实来源，前端 SVG 预览以及 Mermaid、Graphviz DOT、TikZ、Matplotlib 四种脚本都从它编译生成。各后端分别处理 HTML 实体、DOT 引号、LaTeX 特殊字符和 Python 字面量，避免组件名破坏输出语法。TikZ 脚本提示使用 XeLaTeX 处理中文，Matplotlib 脚本默认导出 300 DPI 透明背景 PNG。

离线评测器绕过 LLM，直接对 `LexicalRetriever` 或当前配置的 `HybridRetriever` 执行版本化 JSON 用例。文献通过 ID、文件名、标题或 arXiv ID/版本解析；证据按短文本特征、合法页码和结构类型匹配。聚合报告包含 Case Pass Rate、Evidence Recall、MRR、锚点有效率及 P50/P95 延迟，缺失文献作为显式失败。由此可以在更换 Embedding、Reranker 或融合权重时进行同一数据集对照。

## 部署级验收链路

`scripts/run_system_e2e.ps1` 不绕过 API、数据库或任务队列。它启动实际 Compose 服务，将 Transformer v7/v1 复制进受控临时目录，并在 Backend 容器中调用 `evaluate_system_workflow.py`：HTTP 上传创建数据库记录，Celery Worker 分批解析，API 再读取进度、Document AST、目录、引用和 PDF Range 数据，最后验证旧版本指向新版本的语义重复关系及结构化差异。上传内容附加唯一 PDF 注释且文件名使用随机测试 arXiv ID，既避免历史精确哈希记录短路本次解析，又不改变可见页面内容。

浏览器验收与 API 断言分离。API 评测先输出本轮随机 arXiv ID，Edge DOM 会话必须找到该唯一 ID，不能用页面固定示例文字代替动态数据；另一个隔离 Profile 负责截图，避免 Windows 下 Edge Profile 锁导致假失败。脚本在 200 个候选端口内探测可绑定端口，并让 Compose 的 `FRONTEND_ORIGIN` 与宿主机端口保持一致，因此不会因为 Hyper-V 保留端口或 CORS 产生误报。默认清理本轮创建的文献，报告、截图和浏览器 Profile 均位于被忽略的 `backend/tmp/e2e/`。

## 下一阶段边界

1. 扩充官方 PDF 标注用例，并据此校准召回、重排权重和证据阈值。
2. 为向量模型升级增加蓝绿 Collection 和断点批量重建。
3. 为复杂扫描表格、弱续接信号跨页表格和公式增加专用识别适配器。
4. 为科研图表增加散点图、箱线图、热力图，以及有明确多重比较校正的统计检验。
5. 为架构图增加分组、边标签、SVG/PDF 下载与人工拖拽后的坐标回写。
6. 为整篇翻译增加保持原位排版的双语 PDF 输出和多用户所有权。
7. 将本地存储实现替换为 S3/MinIO 实现，保持 API 不变。
