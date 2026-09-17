# DeepSeek Flash LLM 接入验收（2026-09-17）

## 接入范围

本阶段将 `deepseek-flash` 接入既有 Agent Harness，而不是新增一条绕过 RAG 的自由聊天路径。模型负责：

- 基于已检索 Evidence 生成带证据 ID 的归纳回答；
- 阅读器划词翻译、当前页分段翻译与整篇后台翻译；
- 学术写作、综述等已有生成链路中的受约束语言生成。

检索、证据分数门控、引用白名单、公式/代码占位符保护以及模型失败后的抽取式降级均保持不变。模型不能自行向 References 中添加没有元数据或证据支持的条目。

## Provider 适配

DeepSeek 官方接口支持 OpenAI Responses API 结构，项目通过 `DeepSeekResponsesProvider` 复用现有 JSON Schema 输出和响应解析。与 OpenAI Provider 的差异为：

- Provider 名称固定返回 `deepseek`；
- Base URL 为 `https://api.deepseek.com`；
- 模型为 `deepseek-flash`；
- 不发送 DeepSeek Schema 未声明的 `strict` 和请求侧 `store` 字段；
- 设置 `reasoning.effort=none`，优先保证短问答和逐段翻译延迟。

真实 API Key 只存在于根目录 `.env`。该文件由 `.gitignore` 排除，`.env.example` 和文档只保存无密钥模板。

## 自动化与真实调用验收

MockTransport 单元测试断言了请求 URL、Bearer Header 是否存在、模型 ID、Provider 名称、思考模式和 JSON Schema 结构，但测试密钥仅为固定的 `test-key`。真实密钥不会出现在断言、Git Diff 或测试输出中。

容器重新创建后，`GET /api/v1/agent/status` 返回：

```json
{
  "provider": "deepseek",
  "model": "deepseek-flash",
  "llm_configured": true,
  "translation_configured": true
}
```

随后通过项目自身的 `POST /api/v1/agent/translate` 发送最小英文术语 `neural network`，接口成功返回 `provider=deepseek`、`model=deepseek-flash` 以及中文译文“神经网络”。该验收证明环境变量、Factory、Provider、FastAPI 路由和 DeepSeek 远程接口已形成完整调用链。

## 安全与部署边界

- 公开仓库和比赛材料不得包含 `.env`、终端密钥输出或真实请求 Header。
- 聊天、截图或共享终端中出现过的密钥应在公开部署前轮换。
- `agent/status` 表示配置加载成功，不等价于远程服务持续健康；在线演示应额外监控一次低成本真实调用。
- DeepSeek 返回异常、超时或结构不合格时，问答链路回退为抽取式证据回答；翻译链路返回明确错误，不把原文伪装成译文。
- 当前没有实现流式输出、用量账单展示或 Provider 级熔断器。
