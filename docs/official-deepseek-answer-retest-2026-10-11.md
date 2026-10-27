# 官方原题 DeepSeek 回答重测

日期：2026-10-11。用户明确要求继续重测 DeepSeek 回答；本轮只执行此前剩余的三道原题，每题一次，不自动重试。修复仍保留在 `codex/stage94-official-failed-qa-retest` 工作区，没有创建分支、提交或推送。

## 结果

**真实 DeepSeek 调用 3 次；机器判定 2/3；逐页复核严格接受 1/3。** 失败输出均保留，没有重新生成直到通过，也没有修改原题或事实金标。

| 原题 | 机器判定 | 原文与最终答案复核 | 结论 |
| --- | --- | --- | --- |
| BERT 与 Transformer Encoder 的承袭/丢弃关系 | 通过 | 双来源关系结论正确，标明架构推断，引用锚点有效 | 接受 |
| GPT-1/2/3 参数量演进 | 不完整 | GPT-3 数值正确；缺 GPT-1 明确参数量和 GPT-2 表格精确数值 | 不接受 |
| GPT-1/2/3 Context Window 演进 | 通过 | GPT-2/3 有据；GPT-1 被明确拒答，完整性规则发生误判 | 不接受 |

这不是整套九题的统一回归通过率，也不是 39 份 PDF 的整体问答准确率。前一轮 BGE 的 68/68 是单文档原文检索探针结果，不代表本轮三个跨论文答案应自动通过。

## 调用与数据边界

- Provider：`deepseek`；配置模型：`deepseek-flash`；使用现有 Responses 适配器和生产 `ResearchAgent.ask`，不是 mock 或人工编写答案。
- BGE 服务 `/health` 均返回 200；`/info` 分别确认 `BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3`；Qdrant `/healthz` 返回 200。
- 每题选取 18 条强证据，逐文档检索后交错取样；最终证据均为 `hybrid` + `reranked`。任一检索降级会在生成前拒绝云端调用。
- Dense 来自真实 BGE-M3；Sparse 仍为哈希稀疏表示/BM25，不声称运行 BGE-M3 learned sparse。
- 仅发送五篇已声明的公开论文的检索摘录，没有发送整篇 PDF 或私有论文。GPT-2 是既有公开补充报告，不属于官方 39 件。
- 五份输入先核对原 SHA-256。复用 stage94 隔离索引，恢复 1085 个 chunk 到一次性 SQLite；不访问正式业务数据库，也禁止向旧索引建库、回填、覆盖或删除。
- 本轮 parser 调用为 0，沿用旧索引的历史解析结果，**不是五份论文按当前解析器重新入库的端到端验收**。
- 执行的是主机当前工作区代码，记录了后端代码哈希。运行后检查与代码快照一致；尚未重建正式 backend/worker 镜像，也未做前端页面复测。

## 已确认有效的修复

### BERT 关系结论

BERT 物理第 3 页的 Model Architecture 与 Transformer v1 第 2 页的 Encoder and Decoder Stacks 进入最终证据。最终关系声明同时引用两份论文，明确将保留编码器结构、去除生成式解码器栈标记为架构推断，而非冒充 BERT 原文的逐字陈述。Transformer 第 5 页与 BERT 第 1 页补充的注意力范围和双向上下文描述也逐页核对。本次生产完整性门禁不再误将有效的关系结论标记为不完整。

### GPT-3 的上下文来源

真实检索后的 E3 来自 GPT-3 **物理第 43 页、`p43-b11`**，明确给出完整的 2048-token context window。它不是第 8 页模型参数表里的隐藏维度数值。最终生成保留了此项，证明此前 GPT-3 上下文证据缺失的问题在本次检索/生成链路上得到解决。

## 本轮剩余问题及定位

### 参数量：关键表没有进入最终证据

GPT-2 物理第 4 页 Table 2 及同页实验段落可联合支持原始 GPT 基线与最大 GPT-2 参数量。但本题送给模型的六条 GPT-2 摘录中，仅保留了基线等价说明和摘要中的约数，未包含关键参数表。

最终答案正确保留了“第二小模型”的排序，不再出现旧轮的“第二大”误述；GPT-3 规模也有据。可是它拒绝给出 GPT-1 具体数值，GPT-2 仅回答摘要约数，不能通过原题的精确三模型比较。生产门禁对 GPT-1 缺失发出了不完整警告。

**下一步：** 先离线检查参数表在召回、重排和逐文档裁剪中的位置，完善通用参数字段检索及表格与相邻说明的证据覆盖；不能硬编码原题答案，不能把源文约数悄悄改写为表格精确值。当前报告只确认“最终证据缺失”，不冒称已经区分出具体哪一级丢弃了该表。

### 上下文：过度拒答与完整性误判

GPT-2 第 4 页上下文变更说明和 GPT-3 第 43 页窗口定义都已进入生成证据。模型仍以 GPT-1 自身论文没有明确窗口声明为由拒答，没有给出基于后续论文比较的、注明来源局限的 GPT-1 配置推断。

该拒答同时含 GPT-1、512 和上下文关键词，机器事实匹配因而命中；生产完整性规则的否定模式也没有识别“未被所提供证据明确给出”这类表达。**这是已发现的门禁漏判，不是完整回答。** 独立源文与声明复核将此题保留为失败，不改写旧响应的标志来制造通过。

**下一步：** 补齐多样拒答表达的负例测试，使部分回答仍显示未完成；明确允许引用后续论文的配置比较作带来源说明的推断，同时继续禁止将训练序列长度或模型隐藏维度当成窗口证据。

## 可复核材料

原始模型输出、生产最终声明、实际发送的证据摘录和 Trace 保存在忽略目录：

- `backend/tmp/official-answer-retest-2026-10-11/run-1/report.json`
- `backend/tmp/official-answer-retest-2026-10-11/review-1.json`
- `backend/tmp/official-answer-retest-2026-10-11/source-pages-1/`：核验原文件哈希后渲染的 12 张完整引用页及源文记录。
- `backend/tmp/official-answer-retest-2026-10-11/unit-1.xml`

源文复核政策为 `backend/evals/official_remaining_three_retest_review.json`。原九题数据集、stage88 原报告和 stage94 失败报告均未覆盖或改写。文件哈希摘要见 `docs/evidence/official-deepseek-answer-retest-2026-10-11.json`。复核是代码助手逐页检查，不是独立专家审稿。

后端全量离线回归：**438 项通过，0 失败、0 错误、0 跳过**；Ruff 对后端 app/scripts/tests 检查通过；`git diff --check` 通过。单元测试通过并不能覆盖本报告已暴露的回答语义漏判，下一步应补相应负例。

## 重测工具

`evaluate_official_first_round.py` 新增 `--query-report`：只读复用本工具生成的、完整的公开论文隔离索引；验证原始数据集/文件身份及集合命名空间，禁止自动回填。其安全边界有离线测试。`inspect_official_answer_sources.py` 只在本地渲染最终声明实际引用的完整页，不调用模型、不给答案自动判真。

下列是本轮命令的参数记录。**不得把它作为自动重试；重新执行会产生费用，需要新的调用授权及新的输出路径。** 主机需使用已安装依赖，进程内将 Qdrant 指向 7333、Embedding 指向 8001、Reranker 指向 8002，并配置真实模型；密钥只从本地环境配置读取，不打印、不写入命令或公开文件。

```powershell
python backend/scripts/evaluate_official_first_round.py `
  --corpus "科研小助手/pdf" --supplements output/pdf/regression-corpus `
  --dataset backend/evals/official_first_round.json `
  --query-report backend/tmp/stage94-official/targeted-retest.json `
  --output backend/tmp/official-answer-retest-2026-10-11/run-1/report.json `
  --case-ids bert-inheritance gpt-parameter-evolution context-evolution `
  --max-model-calls 3 --allow-cloud
```

本轮到此停止云端生成。优先处理两项失败和完整性误判的本地修复，再按明确调用额度重测，之后才能宣称三题全部完成。
