# 公网部署基线验收（2026-09-20）

## 目标

将开发/本地演示编排扩展为可提交比赛在线演示的单机生产基线，同时避免把数据库、模型服务和密钥直接暴露到公网。

## 已实现

- `docker-compose.prod.yml` 作为生产 Override，不复制基础服务定义；
- 使用 Compose `!override` 清除 PostgreSQL、Redis、Qdrant、MinIO、Backend、Frontend 与 BGE 的宿主机端口；
- 只有 Caddy 发布 80/tcp、443/tcp 和 443/udp；
- Caddy 按 `/api/*` 转发 Backend，其余请求转发 Frontend，并自动管理 HTTPS；
- 全站启用 bcrypt 评审账号门禁和基础安全响应头；
- Caddy 证书与配置状态使用独立命名卷；
- 所有容器日志使用 10 MiB × 5 文件轮转，避免默认 JSON 日志无限增长；
- PostgreSQL、MinIO 和应用 `DATABASE_URL` 已从硬编码改为可配置，同时保留本地默认值；
- `.env.production` 被 Git 忽略，模板不包含真实 Key 或密码；
- `scripts/deploy.sh` 支持 Lite、标准 CPU、标准 GPU的 config/start/status/logs/stop；
- 部署预检拒绝示例域名、占位密码、无效 HTTPS 地址、无效 bcrypt 哈希和缺失的 DeepSeek Key。

## 静态验收结果

Lite、标准 CPU 和标准 GPU 三套 Compose 合并均通过 `config --quiet`。部署脚本通过真实 Bash 语法解析；示例配置会被占位秘密门禁拒绝，一组临时虚构但相互一致的值能够通过域名、bcrypt、DeepSeek HTTPS 和数据库 URL 全部门禁。Caddyfile 通过官方 `caddy validate`。对 Lite 最终模型执行端口枚举，结果为：

| 服务 | 公网发布端口 |
| --- | --- |
| Caddy | 80/tcp、443/tcp、443/udp |
| Backend、Frontend | 无 |
| PostgreSQL、Redis、Qdrant、MinIO | 无 |
| BGE-M3、BGE Reranker | 无 |

参数化基础 Compose 后重新应用本地标准 GPU 模式，PostgreSQL、Backend、Worker、Frontend、BGE-M3 与 Reranker 全部健康，实际 Provider 仍为 `openai-compatible/tei`。本轮没有伪造公网成功状态：仓库环境没有真实域名、云安全组或公网服务器，因此证书签发与外部访问必须在目标服务器完成。现有本地标准模式继续在 `localhost:3200` 运行，不受生产 Override 影响。

## 安全边界

评审账号是反向代理层的共享门禁，不是应用级用户系统。它适合限制比赛演示入口，但不能提供文献、对话和反馈的多租户所有权隔离。真实公网部署还必须轮换此前暴露过的 API Key、使用随机数据库/MinIO 密码、限制 SSH 来源、建立备份恢复演练，并避免存放无权公开的数据。
