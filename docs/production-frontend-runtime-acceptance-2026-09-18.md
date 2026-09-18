# 生产前端运行时验收（2026-09-18）

## 问题与目标

原 Compose 前端直接运行 `next dev` 并挂载整个源码目录。在 BGE-M3、BGE Reranker、Langfuse 与业务服务同时运行时，Docker 内存接近上限，Turbopack 持久化出现 `Out of memory`。浏览器虽然得到服务端 HTML，但部分 hydration JavaScript 请求超过 30 秒，导致依赖 React `onClick` 的按钮看似无响应。

本轮目标是让比赛演示与在线部署默认使用稳定、低开销的生产前端，同时保留显式的开发热更新模式。

## 配置调整

- `frontend/Dockerfile` 改为 dependencies、development、builder、runner 多阶段构建；
- 依赖严格使用 `package-lock.json` 和 `npm ci`，并增加 BuildKit npm 缓存及网络重试；
- Builder 在镜像构建期注入 `NEXT_PUBLIC_API_BASE_URL` 并执行 `next build`；
- Runner 只复制 `.next/standalone` 与 `.next/static`，以 UID 1001 的非 root 用户运行 `node server.js`；
- 默认 `docker-compose.yml` 不再挂载源码或 `node_modules`，并增加健康检查和自动重启；
- `docker-compose.dev.yml` 显式恢复源码挂载、命名依赖卷和 `next dev`，仅用于开发。

## 验收结果

- 生产镜像内 Next.js 16.3.3 编译、TypeScript 检查和静态预渲染通过；
- 前端 Node 测试 14/14 通过；
- Compose 生产配置与开发 Override 均通过 `docker compose config --quiet`；
- 新容器健康检查通过，首页返回 HTTP 200；
- 首页引用的 8 个生产 JavaScript 资源全部成功返回，顺序检查总耗时 92 ms，无失败或超时；
- 前端容器空闲内存由约 471 MiB 降至约 29–37 MiB；
- 通过 Edge DevTools Protocol 执行真实浏览器点击：
  - 点击“知识库”后 URL 变为 `#library`，工作区标题与 PDF 上传区正确显示；
  - 点击“论文发现”后 URL 变为 `#paper-discovery`，论文检索区正确显示。
- 恢复 BGE-M3 与 BGE Reranker 后再次验收：两个模型均为 healthy，8 个生产 JS 资源在 102 ms 内全部返回；“创作工具 → 架构图”点击、Hash、选中态和工作区显示均正确。

本次修复只改变前端构建和运行方式，不修改 Agent、RAG、数据库或 BGE 模型行为。BGE 服务在构建时仅临时停止以释放内存，模型缓存没有删除，验收后已恢复。
