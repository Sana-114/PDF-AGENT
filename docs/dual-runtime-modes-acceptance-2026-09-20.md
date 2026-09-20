# 双运行模式验收（2026-09-20）

## 验收目标

为比赛演示与日常开发提供可重复的一键启动入口：低资源环境使用无需模型下载的轻量模式，完整验收使用 BGE-M3 与 BGE Reranker 标准模式。两种模式必须复用同一份业务数据，并能通过实际 Provider 配置而不只是容器状态判定切换成功。

## 实现范围

| 项目 | 轻量模式 | 标准模式 |
| --- | --- | --- |
| 启动命令 | `.\scripts\start.ps1 -Mode lite` | `.\scripts\start.ps1 -Mode standard`；GPU 增加 `-Gpu` |
| Dense Embedding | `hash-ngram-v1`，384 维 | `BAAI/bge-m3`，1024 维 |
| Reranker | 关闭 | `BAAI/bge-reranker-v2-m3`（TEI） |
| 模型容器 | 主动停止 | 启动并检查 `/info` |
| 业务数据 | 保留并复用 | 保留并复用 |
| LLM | 继续使用 `.env` 中的 Provider | 继续使用 `.env` 中的 Provider，并提示 DeepSeek 配置状态 |

`docker-compose.lite.yml` 只覆盖 Backend 与 Worker 的检索环境，不复制基础编排。标准模式继续使用 `local-bge` Profile；`docker-compose.bge-gpu.yml` 仅在 `-Gpu` 时叠加。停止命令默认不删除容器、命名卷或模型缓存。

## 启动与诊断门禁

启动前检查 Docker Engine、首次启动所需宿主机端口、Docker 内存、LLM Provider、DeepSeek Key 是否为空，以及所选 TEI 运行镜像是否已缓存。任何检查都不会输出 Key 内容。标准模式低于 20 GiB 时明确警告；`-NoBuild` 只跳过项目镜像构建，不承诺跳过缺失外部镜像和模型权重的下载。

启动后 `check_runtime.ps1` 验证：

1. PostgreSQL、Redis、Qdrant、MinIO、Backend、Worker、Beat 和 Frontend 均处于运行状态；
2. Backend `/api/v1/health` 与 Frontend HTTP 可访问；
3. Celery Worker 能响应定向 Ping；
4. 轻量模式实际配置为 `embedding=hash`、`reranker=none`；
5. 标准模式实际配置为 `embedding=openai-compatible`、`reranker=tei`，并且两个 BGE `/info` 端点可访问。

## 本机实测

测试环境的 Docker 内存上限为 15.4 GiB。轻量模式启动成功，两个 BGE 容器均停止，核心服务、Celery Ping 和 `hash/none` Provider 门禁全部通过；一次稳定快照中八个核心容器合计约 759 MiB。随后使用本机已缓存的 GPU TEI 镜像切换标准模式，两个 BGE 容器均达到 healthy，核心服务、Celery Ping、`openai-compatible/tei` Provider 门禁以及 8001/8002 `/info` 检查全部通过。

标准模式稳定后的一次 `docker stats --no-stream` 快照：

| 组件 | 内存 |
| --- | ---: |
| BGE-M3 Embedding | 5.284 GiB |
| BGE Reranker | 5.827 GiB |
| Backend | 88.07 MiB |
| Worker | 166.9 MiB |
| Frontend | 50.94 MiB |
| 其余 Beat、PostgreSQL、Redis、Qdrant、MinIO 合计 | 约 296.6 MiB |

两个模型服务合计约 11.1 GiB，是标准模式的主要内存开销，因此当前脚本在 20 GiB 以下保留显式警告。标准模式与轻量模式之间的双向切换均已实测；默认停止命令也已验证可以停止全部服务并保留命名卷，随后能从停止状态恢复标准模式。资源快照用于说明当前机器的量级，不作为所有平台的固定资源承诺。

## 结论与边界

双模式的一键启动、切换、状态检查和保留数据停止路径已经打通。轻量模式保证在不部署 BGE 权重时仍可演示完整产品流程；标准模式用于真实语义召回和 Cross-Encoder 重排。仓库不提交模型权重，首次使用某个 TEI 镜像或模型仍依赖网络下载；CPU 与 GPU TEI 镜像分别缓存，已经缓存 GPU 镜像不代表 CPU 镜像也已缓存。
