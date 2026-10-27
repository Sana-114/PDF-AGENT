# PaperPilot 科研助手

本目录为 2026-10-27 预提交源码精简版。PaperPilot 支持 PDF 管理与解析、基于原文的单篇／多篇问答、双语阅读、论文发现与追踪、引用图谱与研究综述、写作框架和 CSV 图表。

标准模式采用 BGE-M3、BGE Reranker、Qdrant 与 DeepSeek Flash。前端为 Next.js／React，后端为 FastAPI，任务队列采用 Celery／Redis，业务状态保存于 PostgreSQL。

## 版本与材料

Git 基线：`a119f5609cda544e5d587588c5a338051da129a1`，包含本地未提交修复。源码历史：https://github.com/Sana-114/PDF-AGENT 。本包不是 main 的导出；实际源码按压缩包旁的 `source-manifest.json` 核对。

完整材料包的 `01-introduction` 提供当前介绍文档，`03-video` 提供视频与字幕，`04-evidence` 提供最近验收记录的精简汇总。历史报告、过期草稿、视频及 PDF 制作工具未放入本源码包；应用功能、测试用例及其引用的辅助脚本保留。

此前未发表、未参加其他比赛、未获奖，非现成项目改造。使用开源依赖、预训练模型和第三方 API，不将这些依赖宣称为自研成果。按项目方要求暂不署名。

## 安装启动

准备 Docker Desktop／Docker Compose 与 PowerShell；标准模式需下载模型并可访问模型服务。GPU 模式还需兼容的 NVIDIA 显卡与容器环境。

在本目录执行：

```powershell
Copy-Item .env.example .env
Copy-Item .env.bge.example .env.bge
```

在 `.env` 中设置自己的配置：

```dotenv
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=填写自己的密钥
```

随后执行：

```powershell
pwsh -File scripts/start.ps1 -Mode standard
pwsh -File scripts/status.ps1
```

使用 GPU 时在启动命令后添加 `-Gpu`。前端默认 `http://localhost:3000`，实际以 `.env` 的 `FRONTEND_HOST_PORT` 和日志为准。上传一份自有或公开 PDF，等待解析和索引完成，再选择论文提问并点击证据检查原页。

模板默认 `LLM_PROVIDER=mock`，启动脚本默认 `lite`；未修改配置不能视为真实 BGE＋DeepSeek 链路。停止服务使用 `pwsh -File scripts/stop.ps1`。首次下载镜像与模型需要时间和额外磁盘空间。

## 目录与复现

| 路径 | 用途 |
| --- | --- |
| backend/app | API、PDF 解析、检索、Agent、生成与任务实现 |
| frontend/app、components、lib | 页面、阅读器与前端逻辑；lib 内保留单元测试 |
| backend/tests、backend/evals | 离线回归用例和评价输入 |
| backend/scripts | 检索／PDF 验收、维护和测试依赖；保留被引用的辅助工具 |
| frontend/e2e | 浏览器功能验收；录制工具已移出提交包 |
| scripts | 启停、部署、健康检查与主要验收入口 |
| docs | 架构、部署、模型、RAG 评价、技术方案及样本清单 |
| deploy | 公网代理配置 |

后端离线测试：建立 Python 虚拟环境，在 backend 中安装 `requirements.txt` 与 `requirements-dev.txt` 后运行 `python -m pytest`。前端在 frontend 中执行 `npm ci`、`npm test`、`npm run typecheck`。真实模型和浏览器验收需要先启动相关服务、导入公开样本并配置环境；不会随测试包附送已有数据库或私有密钥。

OCR 测试还需要 Tesseract 语言包 `eng`、`chi_sim`，作者识别增强使用 `pol`。Dockerfile 已安装相应包；宿主机测试需安装语言包并将 `TESSDATA_PREFIX` 指向实际 tessdata 目录，否则 OCR 用例会失败。语言包不随源码 ZIP 分发。

辅助脚本可能指定原验收机器的数据路径。真实论文用例需按参数提供本机文件；保留脚本意味着可继续复现，不能保证空环境不配置就能得到历史成绩。

## 技术与部署文档

- [系统架构](docs/architecture.md)
- [BGE 服务配置](docs/local-bge.md)
- [公网部署](docs/public-deployment.md)
- [RAG 评价方法](docs/rag-evaluation.md)
- [技术方案及历史测试记录](docs/competition-technical-report.md)

公网配置保留但未上线。部署指南中的 git clone 可获取历史版本；复现本次本地修复应以本源码包为准。正式交付前仍需冻结代码、补齐验收及公网检查。

## 验收范围

10 月 26 日后端离线回归 1,746 项通过；这不是论文问答准确率。数学关系校审、保存证据问答及新的 BGE＋DeepSeek 浏览器题分别有小样本记录，不能相加为所有论文全部通过。公式恢复、复杂 OCR、学术翻译质量、作者年制引用兼容和在线推荐等仍有边界，见完整材料包的当前介绍及验收汇总。

源码包不含真实环境文件、模型权重、官方论文、上传数据、数据库、依赖缓存或原始 API 请求。配置模板仅供初始化，公网部署需另设凭据。
