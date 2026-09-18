# 多轮问答与 SSE 验收（2026-09-18）

## 本阶段目标

把既有单轮抗幻觉 RAG 升级为可恢复的连续问答工作台，同时保持“只有 PDF 原文能作为事实证据”的约束。本阶段新增：

- 数据库持久化会话与消息，支持创建、列表、恢复和删除；
- 在会话创建时固定单篇或全库检索范围；
- 最近 6 条、最多 6000 字符的有界上下文，用于理解省略和指代；
- 单独的检索查询，只组合最近用户问题，不把模型回答回灌为检索事实；
- SSE 状态、回答片段、Claims、Evidence、Trace 和最终消息事件；
- 前端会话历史、连续追问、渐进回答、逐条 Claim 引用和原文跳转。

## 抗幻觉边界

对话历史只进入“理解当前问题”的上下文提示。每轮回答都重新执行 `search_evidence`，生成阶段仍受最低证据分数、Evidence ID 白名单和结构化输出 Schema 约束。持久化的数据包括当时实际使用的原文锚点，恢复历史不会重新生成或伪造引用。

SSE 的事件顺序为：

```text
status → message_start → delta* → claims → evidence → trace → done
```

当前 `delta` 是结构化 Provider 调用完成后的渐进发送，不宣称为 DeepSeek 上游逐 Token 流。这样不会为了低延迟绕过完整 JSON、Claim/Evidence 校验和数据库一致性。

## 自动化检查

在包含 PostgreSQL、Qdrant、BGE-M3、BGE Reranker 和 DeepSeek 配置的 Compose 环境中执行：

- 后端 `python -m pytest`：144 项通过；
- 后端 `ruff check .`：通过；
- 前端 `npm test`：14 项通过；
- 前端 `npm run typecheck`：通过；
- 前端 `npm run build`：Next.js 生产构建通过，主页完成静态预渲染。

新增后端测试覆盖会话持久化、上下文提示与检索查询分离、无效文献范围拒绝，以及完整 SSE 事件契约。

## 真实链路验收

测试文献为已解析的 arXiv `1706.03762v1`《Attention Is All You Need》，检索链路使用 BGE-M3 + BGE Reranker，生成模型为 `deepseek-flash`。

第一轮问题：`What beta values were used for Adam?`

- 返回 `β1 = 0.9`、`β2 = 0.98`；
- Claim 引用 `E1`、`E2`；
- Evidence 带第 7 页等真实页码；
- 返回模型标识为 `deepseek-flash`。

第二轮问题：`Why was the second one set to that value?`

- 系统正确把 `the second one` 解析为 `β2`；
- 对论文未解释设置原因明确回答证据不足，没有补造理由；
- 同时只陈述原文可证实的 `β1 = 0.9`、`β2 = 0.98`、`ϵ = 10⁻⁹`，并引用 `E1`、`E2`；
- HTTP 响应为 `text/event-stream`，观测到 `status`、`message_start`、7 个 `delta`、`claims`、`evidence`、`trace`、`done`；
- 恢复会话得到 4 条按 `user/assistant/user/assistant` 排序的消息；验收后临时会话已删除。
- `http://localhost:3200/` 返回 200，服务端 HTML 包含问答标题与连续追问说明；1440×9000 长页面截图确认组件在完整首页中正常排版。

## 已知边界

- 当前没有账号体系，会话历史属于单实例共享数据；公网部署前需增加用户所有权和鉴权。
- 同一会话的并发写入尚未串行化，Web 界面已在一轮生成期间禁用重复发送，API 级并发控制留待后续。
- Provider 不可用时，既有 Harness 仍可按配置降级；SSE 会返回明确错误事件，不把失败结果保存为完整对话。
- 官方扫描版 PDF 和 541 页教材到位后，仍需复跑相同多轮问答与页码锚点验收。
