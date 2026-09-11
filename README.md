# PaperPilot

PaperPilot 是一个以原文证据为核心的科研助手 Agent 系统。本仓库当前处于 `0.2.0` 开发阶段，已打通 PDF 入库、精确内容去重、异步解析、版式感知 Document AST、arXiv 版本识别、相似版本提醒，以及带页码和坐标锚点的第一版文献问答。

## 当前能力

- 单篇或批量 PDF 上传，校验真实 PDF 内容和大小限制；
- 按完整题名、DOI 或 arXiv ID 检索 Semantic Scholar、arXiv 和 Crossref，并一键导入开放 PDF；
- 远程导入仅接受“来源 + 论文 ID”，由服务端重新解析可信 HTTPS 地址、校验跳转、大小和 PDF 文件头；
- 保存外部索引、DOI、arXiv ID、落地页、许可证和检索元数据，导入仍复用 SHA-256 去重；
- 从已解析 References 批量下钻被引论文，按 DOI/arXiv 精确标识或题名、作者、年份匹配候选；
- Web 端展示可解释匹配分，默认勾选可信开放论文并去重后批量下载入库；
- 支持持久化 arXiv 关键词/分类订阅，Celery Beat 默认每 6 小时自动获取最新论文；
- 结合订阅条件、本地文献题名和点赞历史计算相关性，并综合新鲜度与代码可用性排序；
- 从 arXiv 元数据提取 GitHub 链接，并可选调用 GitHub Repository Search 补充高置信代码仓库；
- 支持论文推荐点赞/踩，后续刷新时把正负反馈纳入个性化兴趣画像；
- 从本地 Document AST 的 References 建立库内有向引用图，每条边保留页码、原文和匹配理由；
- 联合入度与 PageRank 计算基石分，并标记基石、桥接、边缘衍生、外围和孤立论文；
- Web 端以交互 SVG 展示引用拓扑、关系置信度和节点指标，可从图谱直接打开原文；
- 从摘要、引言、结论和 Future Work 章节提取带页码/BBox 的综述证据；
- 按论文时间与局域引用关系整理研究脉络，生成经过证据 ID 白名单校验的领域综述；
- 将未来方向区分为“仍值得探索”和“可能已有进展”，后者同时提供较新论文证据供人工核验；
- 基于 SHA-256 的完全重复检测；
- Celery 后台解析和状态跟踪；
- 长文档按默认 25 页分批解析，页面分片与进度 manifest 原子落盘，失败后从最近完整批次恢复；
- PyMuPDF 版式感知解析，输出页码、文本块、字体、坐标、栏位和稳定阅读顺序；
- 抽取标题、作者、机构、摘要和一/二/三级层级目录；
- 抽取矢量表格的行、单元格、Markdown、图表题注及其页码/BBox 锚点；
- 识别嵌入图片区域、公式文本候选、参考文献条目和附录节点；
- 自动区分原生文本、扫描图像和混合 PDF，并对低文本页执行中英文 OCR；
- 从正文识别 arXiv ID/版本并生成紧凑语义指纹；
- 对同一 arXiv 或高相似标题文献生成版本提醒；
- 自动把页面块切分为不跨页的检索 Chunk，保留章节、页码、块 ID 和 BBox；
- 将摘要、表格、图注、公式候选和参考文献建立为独立证据 Chunk，旧索引可按需自动升级；
- 中英混合 BM25 风格检索，可限定单篇文献或跨文献查询；
- 根据问题中的表格、公式、摘要或引用意图进行结构类型加权；
- 将每个证据 Chunk 写入 Qdrant `dense` / `sparse` 命名向量并用 RRF 融合召回；
- 将 Qdrant 结果与本地 BM25 再次进行 RRF 融合，向量服务异常时自动降级；
- 可切换 OpenAI-compatible Embedding 服务，接入 BGE-M3 等真实学习型语义向量；
- 可切换 Cohere-compatible 或 Hugging Face TEI Cross-Encoder 服务，对有限候选集重排并保留召回分数；
- 可选 `local-bge` Docker Profile，一条命令编排 BGE-M3、BGE Reranker、模型缓存和健康检查；
- Claim/Evidence 问答响应、证据门控、有效引用 ID 校验和执行轨迹；
- 版本化离线 RAG 评测集，输出 Case Pass Rate、Evidence Recall、MRR、锚点有效率和 P50/P95 延迟；
- 内置 `search_evidence`、`get_document_outline`、`get_document_structure`、`get_document_table` Skills 和可扩展注册表；
- 可切换 LLM Provider：默认抽取式零密钥模式，或 OpenAI Responses API；
- Web 端证据问答、正文/表格/公式/引用来源标签和相关度展示；
- 内置 PDF.js 阅读器，支持站内阅读、翻页、缩放、三级标题树导航和当前章节联动；
- 点击问答证据后按页码和 BBox 定位原文，高亮区域随阅读器缩放保持对齐；
- 正文编号引用与 References 条目双向跳转，侧栏汇总每条文献的正文引用位置；
- 阅读器支持中英文划词/选段翻译，保留公式、代码、引用和数字，零密钥模式明确拒绝伪翻译；
- 支持当前页段落对齐翻译、中英目标语言切换、双栏双向同步滚动和会话内页级缓存；
- 支持整篇后台翻译、逐页原子缓存、实时进度展示和失败后的断点续跑；
- 文献列表、原文访问、结构化结果读取、重解析和删除；
- Web 端展示长文档已处理页数、百分比和断点可恢复状态；
- Docker Compose 编排 PostgreSQL、Redis、Qdrant、MinIO、API、Worker 和 Web。

> 当前版本的语义指纹用于候选预警，不等于最终语义去重模型。当前 OCR 使用 Tesseract 中英文基线。默认 Embedding 仍是零密钥的确定性 Hash-Ngram 工程基线；真实语义向量和 Cross-Encoder 已提供兼容接口及可选本地编排，但普通启动不会下载模型权重。公式节点是带位置锚点的文本候选，并非可靠的 LaTeX 反演；扫描页表格结构恢复和 Docling/GROBID 仍待接入。

## 目录结构

```text
backend/
  app/api/          FastAPI 路由
  app/core/         配置和数据库
  app/models/       SQLAlchemy 模型
  app/agent/        Agent Harness、内置 Skills 和注册表
  app/llm/          LLM Provider 接口及实现
  app/parsers/      可替换的解析器接口
  app/services/     存储与内容指纹
  app/workers/      Celery 任务
  tests/            后端测试
frontend/
  app/              Next.js 页面与样式
  lib/              API 客户端和类型
docs/               架构与后续设计
scripts/            本地开发脚本
```

## 快速启动

要求：Docker Desktop 和 Docker Compose。

```powershell
Copy-Item .env.example .env
docker compose up --build
```

或直接运行：

```powershell
.\scripts\dev.ps1
```

启动后访问：

- Web：http://localhost:3000
- API 文档：http://localhost:8000/docs
- Qdrant：http://localhost:6333/dashboard
- MinIO Console：http://localhost:9001

若 Windows 将 `3000`、`6333`、`6334` 或 `6379` 纳入系统保留端口范围，可在 `.env` 中设置
`FRONTEND_HOST_PORT`、`QDRANT_HTTP_PORT`、`QDRANT_GRPC_PORT` 和 `REDIS_HOST_PORT`
改用其他宿主机端口；同时更新 `FRONTEND_ORIGIN` 以及供本机工具使用的 `QDRANT_URL`、
`REDIS_URL`。容器之间仍使用原始服务端口，无需修改后端连接地址。

开发环境中的 MinIO 默认密码只用于本地启动，上线前必须修改。

Docker 镜像已经安装 `eng` 和 `chi_sim` Tesseract 语言数据。默认使用 `chi_sim+eng`，避免中文大字号标题被英文模型优先误判；可通过 `OCR_LANGUAGES` 覆盖。非 Docker 启动时，需要自行安装对应语言包并设置 `OCR_TESSDATA`；原生文本 PDF 不依赖 OCR 环境。

解析任务默认每完成 25 页就在 `PARSED_DIR` 写入一个独立页面分片，并原子更新 manifest。可通过 `PARSE_BATCH_PAGES` 调整批次大小；批次越小，故障后重跑页数越少，但磁盘写入次数越多。最终 AST、Chunk 和向量索引提交成功后，临时检查点会自动清理。

Docker Compose 默认启用 Qdrant 混合检索。本地无 Docker 启动默认保持 `VECTOR_SEARCH_ENABLED=false`，使用 BM25；如已有 Qdrant，可在 `.env` 中开启：

```dotenv
VECTOR_SEARCH_ENABLED=true
QDRANT_URL=http://localhost:6333
EMBEDDING_PROVIDER=hash
EMBEDDING_DIMENSIONS=384
```

向量索引以 Chunk UUID 为 Point ID，Payload 保存文献、页码、BBox、节点类型和原文。上传、重解析、删除以及首次查询旧文献时都会自动同步索引。
Compose 固定 Qdrant Server `v1.19.1`，Python 客户端固定 `1.19.0`，避免 `latest` 漂移造成 API 兼容问题。

### 真实语义 Embedding 配置

若已有实现 OpenAI-compatible `POST /v1/embeddings` 的本地或远程推理服务，可在 `.env` 中切换到 BGE-M3 等学习型模型：

```dotenv
EMBEDDING_PROVIDER=openai-compatible
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DIMENSIONS=1024
EMBEDDING_BASE_URL=http://host.docker.internal:8001/v1
EMBEDDING_API_KEY=
EMBEDDING_TIMEOUT_SECONDS=60
EMBEDDING_BATCH_SIZE=32
```

`EMBEDDING_DIMENSIONS` 必须与服务实际返回维度一致，`EMBEDDING_BATCH_SIZE` 不得超过推理服务的客户端批量上限（本地 TEI 默认均为 32）。模型响应会校验数量、顺序、维度和有限数值；服务不可用时查询自动降级到 BM25。不同模型使用独立的 Qdrant Collection 命名空间，首次查询会补建该模型的索引，不会把不同维度的向量写入同一集合。直接运行后端而不是 Docker 时，应把 `host.docker.internal` 改成推理服务的实际地址（本机通常为 `localhost`）。

### Cross-Encoder 重排序配置

若推理服务实现 Cohere-compatible `POST /v1/rerank`，可对 BM25 或混合召回后的有限候选集启用 BGE Reranker：

```dotenv
RERANKER_PROVIDER=cohere-compatible
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_BASE_URL=http://host.docker.internal:8002/v1
RERANKER_API_KEY=
RERANKER_CANDIDATE_K=12
RERANKER_RETRIEVAL_WEIGHT=0.25
```

重排分数占最终分数的 75%，第一阶段召回分数占 25%，避免重排器完全覆盖强精确匹配。API 会校验返回索引、重复项和有限数值；重排服务异常时保留原候选顺序，问答仍可继续。默认 `RERANKER_PROVIDER=none`，因此初次启动不下载模型、不需要密钥。

### 一键启用本地 BGE

仓库提供可选的 Hugging Face Text Embeddings Inference（TEI）编排。默认快速启动不会创建模型容器；只有显式指定 `local-bge` Profile 才会下载 BGE-M3 与 BGE Reranker 权重：

```powershell
Copy-Item .env.bge.example .env.bge
docker compose --env-file .env.bge --profile local-bge up --build -d
docker compose --env-file .env.bge --profile local-bge ps
```

CPU 是默认模式，首次启动需要下载并缓存两个模型，耗时取决于网络和磁盘。若 Docker Desktop 已配置 NVIDIA GPU，可叠加 GPU Override：

```powershell
docker compose -f docker-compose.yml -f docker-compose.bge-gpu.yml `
  --env-file .env.bge --profile local-bge up --build -d
```

容器健康后运行端到端烟雾检查，验证 1024 维 Embedding 和中文重排结果：

```powershell
docker compose --env-file .env.bge exec backend python scripts/check_bge_services.py `
  --embedding-url http://bge-embedding --reranker-url http://bge-reranker
```

模型保存在 `bge_embedding_cache` 与 `bge_reranker_cache` 命名卷中，重启无需重复下载。`HF_TOKEN` 对这两个公开模型不是必需的；如需配置，只写入被 Git 忽略的 `.env.bge`。详细排障和资源说明见 [`docs/local-bge.md`](docs/local-bge.md)。

## LLM 配置

默认配置不需要任何 API Key：

```dotenv
LLM_PROVIDER=mock
```

该模式只抽取并展示检索到的原文，不会补写缺失事实。若需生成式归纳，在根目录 `.env` 中配置：

```dotenv
LLM_PROVIDER=openai
LLM_MODEL=<可用的模型 ID>
LLM_API_KEY=<仅保存在本地的 API Key>
LLM_BASE_URL=https://api.openai.com/v1
```

适配器使用 Responses API 的 Structured Outputs，并在返回前剔除不存在的证据 ID。请勿把 `.env` 或密钥提交到 Git；仓库只保留 `.env.example`。

同一 Provider 也用于阅读器划词翻译。翻译请求使用独立的结构化输出约束，要求只翻译用户选中的原文并保留公式、代码、引用标记、模型名称、数字和段落结构。默认 `mock`/抽取式模式没有可靠的翻译能力，接口会返回可解释的 `503`，不会把原文或模板文本冒充译文。

双语阅读按当前页懒加载翻译，后端从 Document AST 提取有界文本块并要求模型按 `block_id` 一一返回。漏段、重复 ID、未知 ID 或空译文会被拒绝，避免中英段落错位；页级结果同时写入服务端持久缓存。超长页面最多提交 80 个文本块或 24000 个字符，并显式标记截断状态。

“整篇”翻译由 Celery 后台逐页执行，每页译文独立原子落盘，manifest 保存源文件指纹、Provider/模型签名、目标语言、完成页和错误状态。任务失败后再次启动会跳过已经完成的页面；源 PDF 或翻译模型发生变化时旧缓存自动失效。删除文献时对应译文缓存也会一并清理。

## 在线论文检索、追踪与导入

首页可直接输入论文完整题名、DOI 或 arXiv ID。题名检索优先使用 Semantic Scholar，DOI 在需要时回退到 Crossref，arXiv ID 使用官方 API 精确查询。三个元数据接口在基础模式下均无需密钥；如配置 `SEMANTIC_SCHOLAR_API_KEY` 可获得更稳定的调用额度，`SCHOLARLY_CONTACT_EMAIL` 用于标识 Crossref polite 请求。

一键导入不会接受浏览器传来的任意下载 URL。API 根据 `source` 和 `source_id` 重新查询来源，并仅允许 `SCHOLARLY_PDF_HOSTS` 中的 HTTPS 域名；下载时再次校验重定向链、文件大小和 `%PDF-` 文件头。默认白名单只包含 arXiv 与 Semantic Scholar PDF 域名，部署者可通过环境变量谨慎扩展。只有开放 PDF 可安全解析时按钮才可用，Crossref 兜底结果可能仅展示元数据。

文献解析完成后，可在文献卡片点击“下钻引用”。系统默认分析前 12 条 References，每次最多支持 20 条，并把公共 API 并发限制为 2。DOI 和 arXiv ID 使用精确匹配；普通条目按候选题名词覆盖率、作者姓氏和年份给出可解释分数，默认阈值为 `0.55`。只有达到阈值且具有可信开放 PDF 的首选项会自动勾选，用户仍可检查候选后再批量入库。可通过 `REFERENCE_DISCOVERY_CONCURRENCY` 与 `REFERENCE_MATCH_THRESHOLD` 调整并发和阈值。

“研究动态追踪”支持 arXiv 关键词、分类或两者组合订阅。查询按 `submittedDate` 降序获取最新条目，Celery Beat 默认每 360 分钟触发一次后台刷新；多个订阅之间默认等待 3 秒，遵守 arXiv 对连续 API 请求的友好调用建议。相关参数为 `ARXIV_REFRESH_INTERVAL_MINUTES` 和 `ARXIV_REQUEST_DELAY_SECONDS`。

推荐分由本地文献兴趣相关度、发布时间新鲜度和 GitHub 代码加分组成。相关度复用当前 Embedding Provider：配置 BGE-M3 时使用真实语义向量，服务失败时安全降级到 Hash-Ngram。点赞论文加入正向画像，点踩论文形成负向惩罚，因此反馈会影响下一次刷新。

系统优先使用摘要或 arXiv comment 中作者直接给出的 GitHub URL；如果没有直接链接，默认最多为每次刷新前 3 篇论文调用 GitHub Repository Search，并要求题名至少 60% 的词项重合。公共搜索存在更严格的速率限制，长期在线部署建议把只具备公共仓库读取能力的 Token 写入未提交的 `GITHUB_TOKEN`；也可设置 `GITHUB_CODE_SEARCH_ENABLED=false`，只保留直接链接识别。搜索补充结果表示高相关实现仓库，不宣称一定是作者官方代码。

## 局域引用图谱

“局域引用图谱”只分析当前文献库中已经完成解析的论文。系统先从每篇论文的 References 读取结构化条目，优先按 arXiv ID 精确匹配库内文献，再按规范化题名、词项覆盖率和文本相似度建立高置信有向边。它不会让 LLM 猜测引用关系；每条边都返回参考文献 ID、标签、所在页码、原文、匹配分和理由，便于评审复核。

图谱对有向边执行 PageRank，并将归一化 PageRank 与库内入度组合为“基石分”。得分最高且确实被库内论文引用的节点标记为基石论文；同时具有入边和出边的是桥接论文，只有出边的是边缘衍生论文，没有任何关系的是孤立节点。该分类只代表用户当前导入语料形成的局部拓扑，不等同于全领域引用影响力。

“自动综述与前沿探索”复用同一局域图谱，并从 Document AST 的摘要、引言、讨论、结论、限制和 Future Work 区域抽取原文证据。配置生成式 LLM 时，模型只能引用本次生成的 `R1...Rn` 证据 ID，服务端会丢弃不存在的引用；未配置 LLM 时使用抽取式 Provider，仍可完整演示论文时间线和 Future Work 证据。

系统不会仅凭文本相似就宣称旧方向已经解决。只有本地较新论文与原方向具有较高概念覆盖率，并在存在时叠加“新论文引用旧论文”的图关系，才标记为“可能已有进展”；界面保留原方向和后续进展两侧证据，要求用户最终确认。没有足够后续证据的方向保留为“仍值得探索”。

## 不使用 Docker 的本地启动

后端默认使用 SQLite，并以内联模式运行 Celery 任务，因此无需先启动 Redis：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
Set-Location backend
uvicorn app.main:app --reload
```

另开终端启动前端：

```powershell
Set-Location frontend
npm install
npm run dev
```

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | 服务健康检查 |
| GET | `/api/v1/discovery/papers?q=...` | 按题名、DOI 或 arXiv ID 检索论文 |
| POST | `/api/v1/discovery/import` | 从可信开放来源下载、去重并创建解析任务 |
| POST | `/api/v1/discovery/references/resolve` | 批量识别 References 中的被引论文及候选匹配分 |
| POST | `/api/v1/recommendations/subscriptions` | 创建 arXiv 关键词/分类追踪并提交首次刷新 |
| GET | `/api/v1/recommendations/subscriptions` | 查看订阅、最近刷新时间和错误状态 |
| POST | `/api/v1/recommendations/subscriptions/{id}/refresh` | 手动提交订阅刷新任务 |
| DELETE | `/api/v1/recommendations/subscriptions/{id}` | 删除订阅及其推荐记录 |
| GET | `/api/v1/recommendations` | 按综合推荐分读取最新论文 |
| POST | `/api/v1/recommendations/{id}/feedback` | 保存喜欢、不感兴趣或中性反馈 |
| POST | `/api/v1/citation-graph` | 从选定或全部已解析文献构建可解释的局域引用图谱 |
| POST | `/api/v1/reviews/generate` | 生成证据约束综述、论文时间线与 Future Work 状态 |
| POST | `/api/v1/documents` | 上传 PDF，字段名为 `file` |
| GET | `/api/v1/documents` | 文献列表 |
| GET | `/api/v1/documents/{id}` | 文献详情 |
| GET | `/api/v1/documents/{id}/progress` | 已完成页数、百分比和断点恢复状态 |
| GET | `/api/v1/documents/{id}/file` | 原始 PDF |
| GET | `/api/v1/documents/{id}/content` | Document AST |
| GET | `/api/v1/documents/{id}/outline` | 轻量级一/二/三级标题树与跳转锚点 |
| GET | `/api/v1/documents/{id}/references` | 参考文献条目、正文引用位置与 BBox 锚点 |
| POST | `/api/v1/documents/{id}/translations/pages/{page}` | 当前页段落对齐翻译，用于双语阅读 |
| POST | `/api/v1/documents/{id}/translations` | 创建或续跑整篇后台翻译任务 |
| GET | `/api/v1/documents/{id}/translations/{language}` | 查询整篇翻译进度与错误状态 |
| GET | `/api/v1/documents/{id}/translations/{language}/pages/{page}` | 读取已持久化的页级译文 |
| POST | `/api/v1/documents/{id}/reparse` | 重新解析 |
| DELETE | `/api/v1/documents/{id}` | 删除文献及本地文件 |
| GET | `/api/v1/agent/status` | 当前 Provider、模型和 Skills 状态 |
| GET | `/api/v1/agent/skills` | 可供 Harness 调用的 Skill 清单与输入 Schema |
| POST | `/api/v1/agent/ask` | 文献问答，返回 Claim、Evidence 和执行轨迹 |
| POST | `/api/v1/agent/translate` | 中英学术选段翻译，需配置生成式 LLM |

`GET /documents/{id}/content` 返回 `schema_version=0.2.0` 的 Document AST。除逐页 `blocks` 外，顶层包含 `authors`、`affiliations`、`abstract`、`outline`、`tables`、`figures`、`formulas`、`references` 和 `appendices`；所有可跳转节点均保留页码、块 ID 或 BBox。

问答示例：

```json
{
  "question": "论文使用了哪些训练超参数？",
  "document_ids": ["<document-id>"],
  "top_k": 6
}
```

## 测试和静态检查

```powershell
Set-Location backend
python -m pytest
ruff check app tests scripts

Set-Location ..\frontend
npm run typecheck
npm run build
```

### RAG 检索评测

评测器不调用 LLM，分别验证词法基线和当前配置的混合检索链路。已有中文测试论文可直接运行：

```powershell
docker compose exec backend python scripts/evaluate_retrieval.py `
  evals/local_chinese_thesis.json `
  --retriever configured `
  --output /tmp/local-rag-configured.json `
  --summary-only `
  --strict
```

`backend/evals/attention_v1.json` 覆盖 Adam 超参数、训练 GPU 和 BLEU 分数；将比赛指定的 `1706.03762v1.pdf` 入库后即可执行。缺失文献会被明确计为失败，不会静默跳过。评测定义和扩展格式见 [docs/rag-evaluation.md](docs/rag-evaluation.md)。

### PDF 回归语料

公开测试文件不提交到 Git。网络可用时，在根目录运行以下命令下载固定版本的 Transformer v1/v7、BERT、RAG 和 500 页以上的 Understanding Deep Learning：

```powershell
.\scripts\fetch_pdf_corpus.ps1
# 需要显式代理时：
.\scripts\fetch_pdf_corpus.ps1 -Proxy http://127.0.0.1:7890
```

语料默认保存在 `output/pdf/regression-corpus/`。若已有一篇真实 PDF，可在 backend 容器内派生无文本层扫描版和精确 541 页压力版：

```powershell
docker compose exec backend python scripts/build_pdf_fixtures.py `
  /app/data/uploads/<source.pdf> /tmp/regression-corpus
```

执行完整解析并输出机器可读报告：

```powershell
docker compose exec backend python scripts/evaluate_pdf_corpus.py `
  /tmp/regression-corpus `
  --manifest /tmp/pdf-regression-corpus.json `
  --output /tmp/regression-corpus/report.json `
  --batch-pages 25 `
  --strict
```

验收清单位于 `docs/pdf-regression-corpus.json`，覆盖页数、文本层、OCR 输出量、标题、结构节点、耗时和 Python 峰值内存。下载 URL 均固定到论文版本；二进制 PDF 和动态报告由 `.gitignore` 排除。

## 下一里程碑

1. 用标准测试 PDF 评估当前版式基线，并按失败样本接入 Docling、GROBID 和 PaddleOCR；
2. 用本地 BGE-M3 / Reranker 跑完官方 PDF 离线评测，并据此校准融合权重；
3. 为领域综述增加主题聚类、跨论文争议识别和人工确认后的方向状态持久化；
4. 推进写作 Copilot、真实 References 校验、CSV 数据图表与学术 Figure Caption；
5. 准备公网部署、技术文档 PDF 和 5–8 分钟演示视频。

更完整的边界说明见 [docs/architecture.md](docs/architecture.md)。
