# PaperPilot 科研助手

PaperPilot 将 PDF 阅读、证据问答、中英对照、论文发现、引用图谱及研究写作集中在一个工作区。标准模式使用 BGE-M3 召回、BGE Reranker 重排与 DeepSeek Flash 生成，前端为 Next.js／React，后端为 FastAPI／Celery，数据保存在 PostgreSQL、文件卷与 Qdrant。

本源码包来自固定 Git 提交，完整提交号见本文末尾及材料包的提交说明。完整开发历史：https://github.com/Sana-114/PDF-AGENT 。打包仅替换此 README 为提交版说明，其他所选文件读取自该提交，不混入本地未提交修改。

## 安装运行

准备 Docker Compose 和 PowerShell。标准模式首次启动需要下载镜像与模型；GPU 模式还需兼容的 NVIDIA 容器环境。先复制环境模板：

```powershell
Copy-Item .env.example .env
Copy-Item .env.bge.example .env.bge
```

在本地 .env 中填写自己的 LLM_API_KEY，并设置 LLM_PROVIDER=deepseek、LLM_MODEL=deepseek-flash、LLM_BASE_URL=https://api.deepseek.com。按本机环境配置 BGE，参阅 docs/local-bge.md。模板默认 mock 仅供开发，不是真实模型验收模式。

```powershell
pwsh -File scripts/start.ps1 -Mode standard
pwsh -File scripts/status.ps1
```

默认前端 http://localhost:3000，后端 http://localhost:8000，具体以环境模板及状态输出为准。上传公开 PDF 后等待解析，选定文献进行问答，点击证据核对原页。阅读器可打开中英对照，分别从原文、译文定位对应内容。

## 测试与复现

后端安装 backend/requirements.txt 与 requirements-dev.txt 后，在 backend 目录运行 pytest。宿主机 OCR 测试需要 Tesseract 的 eng、chi_sim、pol 语言包，并正确设置 TESSDATA_PREFIX。前端在 frontend 目录执行 npm ci、npm test、npm run typecheck。真实模型与浏览器验收还需要启动服务、配置自己的 API 密钥，并按脚本参数导入公开论文。

辅助验收脚本可能保留原验收目录参数，需调整为本机路径。测试代码保留不意味着无需模型、语料和系统依赖即可复现全部历史结果。本次材料整理未重新运行所有模型验收；当前范围见完整材料包中的 PaperPilot-introduction.pdf。

## 文档与来源

- [系统架构](docs/architecture.md)
- [BGE 配置](docs/local-bge.md)
- [公网部署](docs/public-deployment.md)
- [RAG 评价方法](docs/rag-evaluation.md)

项目此前未发表、未参加其他比赛、未获奖，不是基于某个现成项目改造。使用第三方开源框架、预训练模型及 API，团队和个人署名按项目方要求暂不填写。公网配置已保留，正式线上地址与评审账号尚待部署。

源码包排除真实环境文件、模型、原始 PDF、运行数据库、依赖缓存、旧录屏截图及材料制作工具。辅助脚本按运行和测试引用保留；测试直接引用的少量固定样本仍包含在源码中。完整仓库保留开发历史和被省略的辅助工具。
