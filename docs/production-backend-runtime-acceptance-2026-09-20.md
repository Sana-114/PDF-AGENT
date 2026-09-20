# 生产 Backend 运行时验收（2026-09-20）

## 改造目标

默认 Compose 原先以 `uvicorn --reload` 运行 Backend，并把整个 `backend/` 目录挂载到 Backend、Worker 与 Beat。该配置适合开发，但会启动文件监视进程，且容器行为依赖宿主机工作区，不适合作为比赛演示和在线部署默认值。

## 生产配置

- Backend 使用镜像内代码运行 `uvicorn app.main:app --workers ${WEB_CONCURRENCY:-1}`；
- Backend、Worker 和 Beat 只挂载持久化 `app_data`，不挂载源码；
- Backend 增加 `/api/v1/health` 容器健康检查；
- Worker 使用定向到自身节点的 Celery `inspect ping` 健康检查；
- Frontend 等待 Backend healthy 后启动；
- Backend、Worker 与 Beat 设置自动重启策略；
- `docker-compose.dev.yml` 才恢复源码挂载、`uvicorn --reload` 和 `next dev`；
- `scripts/check_runtime.ps1` 一次检查全部核心服务、Backend、Frontend 与 Celery，可选严格要求两个 BGE 服务。

## 镜像上下文修复

首次构建发现本地 `backend/tmp/` 中的动态 PDF 与验收产物进入 Docker 上下文，使传输体积达到约 358 MB。更新 `.dockerignore` 后，`tmp/`、测试缓存和测试目录不再进入生产镜像，实测构建上下文下降到约 9 KB。运行所需的 `app/`、`scripts/` 和 `evals/` 仍保留。

## 验收结果

- 生产 Compose 与开发 Override 均通过 `docker compose config --quiet`；
- Backend、Worker、Beat 三个生产镜像构建成功；
- Backend 容器命令不含 `--reload`，仅挂载 `/app/data` 命名卷；
- Worker 容器仅挂载 `/app/data`，健康状态为 healthy；
- Backend HTTP 健康检查、Frontend HTTP 检查与 Celery Worker Ping 全部通过；
- 开启 `-RequireBge` 后，BGE-M3 Embedding 与 BGE Reranker `/info` 检查均通过；
- PostgreSQL、Redis、Qdrant、MinIO、Backend、Worker、Beat、Frontend 及两个 BGE 服务均保持运行。

本轮不修改业务 API、数据库模型、RAG 或 LLM 行为，仅将默认容器运行方式从开发模式切换为可复现的生产模式。
