# PaperPilot

PaperPilot 是一个以原文证据为核心的科研助手 Agent 系统。本仓库当前处于 `0.2.0` 开发阶段，已打通 PDF 入库、精确内容去重、异步解析、版式感知 Document AST、arXiv 版本识别、相似版本提醒，以及带页码和坐标锚点的第一版文献问答。

## 当前能力

- 单篇或批量 PDF 上传，校验真实 PDF 内容和大小限制；
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
- Web 端证据问答、正文/表格/公式/引用来源标签、相关度展示和原文页码跳转；
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
```

`EMBEDDING_DIMENSIONS` 必须与服务实际返回维度一致。模型响应会校验数量、顺序、维度和有限数值；服务不可用时查询自动降级到 BM25。不同模型使用独立的 Qdrant Collection 命名空间，首次查询会补建该模型的索引，不会把不同维度的向量写入同一集合。直接运行后端而不是 Docker 时，应把 `host.docker.internal` 改成推理服务的实际地址（本机通常为 `localhost`）。

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
| POST | `/api/v1/documents` | 上传 PDF，字段名为 `file` |
| GET | `/api/v1/documents` | 文献列表 |
| GET | `/api/v1/documents/{id}` | 文献详情 |
| GET | `/api/v1/documents/{id}/progress` | 已完成页数、百分比和断点恢复状态 |
| GET | `/api/v1/documents/{id}/file` | 原始 PDF |
| GET | `/api/v1/documents/{id}/content` | Document AST |
| POST | `/api/v1/documents/{id}/reparse` | 重新解析 |
| DELETE | `/api/v1/documents/{id}` | 删除文献及本地文件 |
| GET | `/api/v1/agent/status` | 当前 Provider、模型和 Skills 状态 |
| GET | `/api/v1/agent/skills` | 可供 Harness 调用的 Skill 清单与输入 Schema |
| POST | `/api/v1/agent/ask` | 文献问答，返回 Claim、Evidence 和执行轨迹 |

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
3. 接入 PDF.js，利用现有 BBox 证据实现页内高亮；
4. 使用 `1706.03762v7.pdf`、`v1.pdf` 和扫描版建立自动回归集。

更完整的边界说明见 [docs/architecture.md](docs/architecture.md)。
