# PaperPilot

PaperPilot 是一个以原文证据为核心的科研助手 Agent 系统。本仓库当前处于 `0.2.0` 开发阶段，已打通 PDF 入库、精确内容去重、异步解析、基础 Document AST、arXiv 版本识别、相似版本提醒，以及带页码和坐标锚点的第一版文献问答。

## 当前能力

- 单篇或批量 PDF 上传，校验真实 PDF 内容和大小限制；
- 基于 SHA-256 的完全重复检测；
- Celery 后台解析和状态跟踪；
- PyMuPDF 基线解析，输出页码、文本块、坐标和阅读顺序；
- 从正文识别 arXiv ID/版本并生成紧凑语义指纹；
- 对同一 arXiv 或高相似标题文献生成版本提醒；
- 自动把页面块切分为不跨页的检索 Chunk，保留章节、页码、块 ID 和 BBox；
- 中英混合 BM25 风格检索，可限定单篇文献或跨文献查询；
- Claim/Evidence 问答响应、证据门控、有效引用 ID 校验和执行轨迹；
- 内置 `search_evidence`、`get_document_outline` Skills 和可扩展注册表；
- 可切换 LLM Provider：默认抽取式零密钥模式，或 OpenAI Responses API；
- Web 端证据问答、相关度展示和原文页码跳转；
- 文献列表、原文访问、结构化结果读取、重解析和删除；
- Docker Compose 编排 PostgreSQL、Redis、Qdrant、MinIO、API、Worker 和 Web。

> 当前版本的语义指纹用于候选预警，不等于最终语义去重模型。当前检索为无需模型的词法基线；OCR、Docling/GROBID、Embedding、Qdrant 混合检索和重排器仍待接入。

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
| GET | `/api/v1/documents/{id}/file` | 原始 PDF |
| GET | `/api/v1/documents/{id}/content` | Document AST |
| POST | `/api/v1/documents/{id}/reparse` | 重新解析 |
| DELETE | `/api/v1/documents/{id}` | 删除文献及本地文件 |
| GET | `/api/v1/agent/status` | 当前 Provider、模型和 Skills 状态 |
| GET | `/api/v1/agent/skills` | 可供 Harness 调用的 Skill 清单与输入 Schema |
| POST | `/api/v1/agent/ask` | 文献问答，返回 Claim、Evidence 和执行轨迹 |

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
ruff check app tests

Set-Location ..\frontend
npm run typecheck
npm run build
```

## 下一里程碑

1. 增加 Docling、GROBID、PaddleOCR 解析路由；
2. 为 541 页教材实现逐页落盘和断点恢复；
3. 建立 Qdrant Dense/Sparse 混合检索与 Cross-Encoder 重排；
4. 接入 PDF.js，利用现有 BBox 证据实现页内高亮；
5. 使用 `1706.03762v7.pdf`、`v1.pdf` 和扫描版建立自动回归集。

更完整的边界说明见 [docs/architecture.md](docs/architecture.md)。
