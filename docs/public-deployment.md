# PaperPilot 公网部署指南

## 推荐拓扑

比赛演示采用单台 Ubuntu 服务器运行 Docker Compose。公网请求只进入 Caddy；Caddy完成 HTTPS、评审账号校验和路径转发，其他服务只在 Compose 网络中通信。

```text
Browser ── HTTPS :443 ── Caddy
                           ├── /api/* ── Backend :8000
                           └── /*      ── Frontend :3000

Backend / Worker ── PostgreSQL + Redis + Qdrant + MinIO
                 └── optional BGE-M3 + BGE Reranker
```

Lite 模式建议至少 4 vCPU、8 GiB RAM、80 GiB SSD；标准 CPU 建议至少 8 vCPU、32 GiB RAM；标准 GPU 建议 32 GiB 系统内存和能够同时容纳两个模型的 NVIDIA GPU。具体资源仍需用官方 PDF 数据集压测后确定。

## 1. 域名和防火墙

1. 为域名添加指向服务器公网 IPv4 的 A 记录；存在可用 IPv6 时再添加 AAAA。
2. 云厂商安全组只向公网开放 TCP 80、TCP/UDP 443。
3. SSH 22 只允许管理者固定 IP，优先使用 SSH Key 并关闭密码登录。
4. 不开放 3000、8000、8001、8002、5432、6379、6333、6334、9000 或 9001。

Docker 发布端口可能先于 UFW 的 INPUT/OUTPUT 规则处理，因此必须同时依靠云安全组和 `docker-compose.prod.yml` 的端口移除规则，不能只执行 `ufw deny`。

## 2. 安装运行环境

在受支持的 Ubuntu LTS 上通过 Docker 官方 APT 仓库安装 Docker Engine、Buildx 和 Compose Plugin，然后确认：

```bash
docker version
docker compose version
```

本项目生产 Override 使用 `!override` 完全移除基础 Compose 端口，需要 Docker Compose 2.24.4 或更高版本。标准 GPU 模式还需要 NVIDIA Driver 与 NVIDIA Container Toolkit，并应先确认 `nvidia-smi` 正常。

## 3. 获取代码与创建环境文件

```bash
git clone git@github.com:Sana-114/PDF-AGENT.git
cd PDF-AGENT
cp .env.production.example .env.production
chmod 600 .env.production
```

标准模式额外执行：

```bash
cp .env.bge.example .env.bge
chmod 600 .env.bge
```

`.env.production` 和 `.env.bge` 均被 Git 忽略。不要把它们复制进 README、Issue、日志或演示截图。

## 4. 配置 HTTPS 地址和秘密

生产文件至少需要修改：

```dotenv
PUBLIC_DOMAIN=paper.your-domain.com
FRONTEND_ORIGIN=https://paper.your-domain.com
NEXT_PUBLIC_API_BASE_URL=https://paper.your-domain.com/api/v1

POSTGRES_PASSWORD=使用URL安全字符生成的随机密码
COMPOSE_DATABASE_URL=postgresql+psycopg://pdfagent:同一个随机密码@postgres:5432/pdfagent
MINIO_ROOT_PASSWORD=另一个随机密码

LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-flash
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=部署前重新生成的Key
```

`NEXT_PUBLIC_API_BASE_URL` 会编译进前端静态资源，因此域名改变后必须重新构建 Frontend。对话或截图中暴露过的 Key 在部署前必须轮换。

生成评审账号密码哈希时不要把明文放进命令参数；Caddy 在连接到 TTY 且省略 `--plaintext` 时会隐藏输入：

```bash
docker run --rm -it caddy:2.11.4-alpine caddy hash-password --algorithm bcrypt
```

把输出放入单引号，避免 Compose 把 bcrypt 中的 `$` 当成插值：

```dotenv
DEMO_USERNAME=reviewer
DEMO_PASSWORD_HASH='$2a$...'
```

Basic Auth 只作为比赛评审的共享边缘门禁。当前应用没有用户所有权隔离，不应用于接收互不信任用户的私密论文。

## 5. 启动与检查

```bash
chmod +x scripts/deploy.sh

# 低资源、无本地 BGE
./scripts/deploy.sh lite config
./scripts/deploy.sh lite start

# BGE-M3 + BGE Reranker CPU
./scripts/deploy.sh standard start

# BGE-M3 + BGE Reranker GPU
./scripts/deploy.sh standard-gpu start
```

启动脚本先检查秘密、域名、HTTPS URL、Docker 和 Compose 合并结果，再构建并等待健康检查。首次标准模式还要下载 TEI 镜像与两个模型，默认最多等待 900 秒；可通过 `DEPLOY_TIMEOUT_SECONDS` 调整。

运行维护命令：

```bash
./scripts/deploy.sh lite status
./scripts/deploy.sh lite logs
./scripts/deploy.sh lite stop
```

停止只停止容器，PostgreSQL、PDF/AST、Qdrant、MinIO、模型缓存和 Caddy 证书命名卷都会保留。不要运行 `docker compose down -v`。

## 6. DNS 与 HTTPS 验收

确认 DNS 生效且安全组开放 80/443 后访问：

```text
https://paper.your-domain.com/
```

浏览器应先显示评审账号验证，通过后首页和 `/api/v1/health` 均应返回 HTTPS 内容。Caddy 证书数据保存在 `caddy_data` 命名卷，更新容器时不会重新申请。

随后完成一次真实业务验收：上传 PDF、等待 Worker 解析、执行带证据问答、打开 PDF 页码锚点，并在标准模式下确认回答 Trace 使用 BGE 重排结果。

## 7. 更新、备份与恢复

更新前先保存当前 Git 提交号并备份。拉取代码后，用原模式重新执行 `start`，脚本会重建发生变化的镜像：

```bash
git rev-parse HEAD
git pull --ff-only
./scripts/deploy.sh lite start
```

至少备份 PostgreSQL、`app_data`、`qdrant_data` 和 `minio_data`；模型缓存可以重新下载，Caddy 数据卷应保留以避免证书状态丢失。云磁盘快照不能替代 PostgreSQL 逻辑备份，必须定期执行 `pg_dump`，并在独立环境验证恢复流程。

## 8. 上线边界

- 当前共享评审账号不提供应用内租户隔离、用户注册、权限回收或数据所有权。
- 上线前应限制评审人数，并避免存放未授权公开的论文或隐私数据。
- 建议在云平台增加磁盘、CPU、内存和容器退出告警，并为 DeepSeek 设置余额告警。
- 正式多用户版本必须增加登录、Document/Conversation 所有权、访问审计、速率限制和配额。

## 官方参考

- [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Docker Compose production](https://docs.docker.com/compose/how-tos/production/)
- [Compose merge 与 `!override`](https://docs.docker.com/reference/compose-file/merge/)
- [Docker packet filtering 与 UFW 边界](https://docs.docker.com/engine/network/packet-filtering-firewalls/)
- [Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https)
- [Caddy Basic Auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
