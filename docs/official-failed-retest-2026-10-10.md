# 第 94 阶段：官方失败问答的五题真实复测

日期：2026-10-10。基于第 93 阶段修复后的运行版本，只复测第 88 阶段严格验收失败的五道原题。结论：**机器检查 3/5，原文复核后的端到端接受 2/5**。剩余三题的原因不同，不能都归为模型没有能力回答，也不能只凭必需数值命中宣称无幻觉。

本轮是新生成的五题子集，不是原始九题的全量复跑。第 88 阶段原始 4/9 结果保持不变；不将不同版本、不同轮次的通过项拼成一个新的九题成绩。当前仍未达到官方问答最终交付门禁。

## 授权、语料与隔离

- 本轮授权上限为 5 次 DeepSeek 调用；实际 5 次，每题一次，自动重试为零，复核另用零次。模型 `deepseek-flash`。
- 使用四篇官方公开论文及一篇原有公开 GPT-2 补充论文。未发送本地私有论文、未启动整篇翻译，也未改变正式文献库。
- 沿用 `backend/evals/official_first_round.json` 的原问题、事实金标和文件 SHA-256；只新增选题参数，不改写原题使其更容易通过。
- 内存 SQLite、临时解析目录与新建 Qdrant 集合隔离运行。1085 个解析块对应 1085 个实际已索引 Point；集合状态 `green`。保留测试集合供定位，不删除其他集合。
- 返回证据共 74 条，74/74 标记为 `hybrid` 召回及 `reranked` 重排，锚点检查无失败。这证明本轮检索和重排执行，不证明每个所需字段都已召回或每条最终结论都正确。

| 文件 | 来源 | 页数 | 本轮块数 |
| --- | --- | ---: | ---: |
| `1706.03762v1.pdf` | 官方公开 Transformer v1 | 15 | 113 |
| `N19-1423.pdf` | 官方公开 BERT | 16 | 145 |
| `language_understanding_paper.pdf` | 官方公开 GPT-1 | 12 | 139 |
| `2005.14165v4.pdf` | 官方公开 GPT-3 | 75 | 470 |
| `gpt2-openai-2019.pdf` | 已准备的公开补充论文，非官方 39 件之一 | 24 | 218 |

## 逐题结果

| 原题 ID | 机器检查 | 原文核查 | 端到端接受 | 结论 |
| --- | --- | --- | --- | --- |
| `sixth-author` | 通过 | 通过 | 通过 | 完整有序作者证据已被检索，回答第 6 作者 Aidan N. Gomez、论文所列 University of Toronto |
| `bleu-improvement` | 通过 | 通过 | 通过 | 28.4、先前最佳 26.36 和工具推导 2.04 均发布且有同表证据 |
| `bert-inheritance` | 失败 | 核心结论有依据 | 不通过 | 承袭和去除分别形成双来源结论，但生产完整性规则误判为缺项 |
| `gpt-parameter-evolution` | 通过 | 不通过 | 不通过 | 必需数量正确，额外叙述颠倒模型规模排序并混淆论文归属 |
| `context-evolution` | 失败 | 不完整 | 不通过 | GPT-3 的真正上下文段落未进入最终证据，模型安全地保留缺项 |

### 作者：已修复首轮证据缺失

Transformer v1 物理第 1 页作者网格中的第 6 位确为 Aidan N. Gomez，列出的机构为 University of Toronto。新作者证据保留完整顺序、真实块和坐标，不再只检索到前半组作者。

原页脚注还说明相关工作在 Google Brain 完成；论文机构不等于当前任职。新回答未发布与脚注冲突的就业陈述，但正式演示宜表述为“论文所列机构”，不解释为已核查作者当前单位。

### BLEU：真实生成加证据工具补全

Transformer v1 物理第 8 页 Table 2 的 EN-DE 列：Transformer big 为 28.4，包含集成模型的先前最高值为 ConvS2S Ensemble 的 26.36；同一任务同一表的精确减法为 `28.4 - 26.36 = 2.04`。

模型写出了数量及“超过 2.0”的描述，其使用 Unicode 减号的精确推导未通过数值发布校验。确定性工具随后依据同表操作数补充了三条中文结论，最终发布答案完整。本题成绩属于完整 Agent 链路，不宣称精确差值由模型单独可靠计算。英文与中文混排、重复结论仍是呈现问题，未计为事实失败。

### BERT：完整性规则误报，不撤销真实失败记录

新回答的承袭结论同时引用 BERT 物理第 3 页与 Transformer 第 2 页；去除结论也引用这两篇，加上 BERT 第 13 页 Figure 3 和 Transformer 第 5 页的注意力说明。由双向编码器与原始编码器—解码器的对比推导出未采用解码器、编码器—解码器注意力和因果生成掩蔽，回答明确标注为推断。

生产规则却要求“承袭”和“去除”必须同时出现在同一条双来源声明中，因两条合格声明分开而发布了不完整提示。这是过严的结构规则，不是原题缺少两篇证据。本轮不在复核时偷偷取消生产失败标记：事实审核通过，端到端仍失败。下一修复应分别检查两个关系各自的双来源支撑，同时保留单来源拼接、缺去除关系等负例门禁。

### 参数量：数字白名单不足以消除语义错误

最终声明保留了 GPT-1 约 117M（明确由 GPT-2 等价声明推断）、GPT-2 表中 1542M、GPT-3 175B／1750 亿。第 90 阶段单位转换修复在新生成中实际生效。

但模型额外发表了两个错误：

1. GPT-2 物理第 4 页写的是第二小模型对应 BERT 最大模型，回答改成第二大。表中四档为 117M、345M、762M、1542M，顺序倒置不是等义翻译。
2. 关于两个数量级的比较来自 GPT-3 物理第 31 页，声明开头却称由 GPT-2 论文提出，括号又反过来承认 GPT-3 来源。引用 E9 的实际论文是 GPT-3，不能以附加括号掩盖主句归属错误。

机器只匹配必需事实，未检查这些附加声明，因此机器通过不能替代逐条原文审核。应补充可审计的模型—数值—排序关联与论文归属检查，必要时拒绝发布无法验证的额外事实，不能靠删除本轮错误记录来改变成绩。

### 上下文：召回到同页表格不等于召回到所需正文

最终 GPT-3 证据含物理第 8 页 Table 2.1，却不包含该表下方第 2.1 节明确的 `n_ctx=2048` 上下文句；第 43 页附录的同一上下文说明也未入选。表中的 2048 是 GPT-3 XL 的 `d_model` 单元格，不可用来证明最大窗口。

模型在证据不足时明确保留 GPT-3 缺项，没有捏造 2048。这是安全性表现，但不是完整三文对比。下一步需在生成前做字段导向的候选召回及表格相邻正文补充，然后仍经过 BGE 重排和来源校验，不硬编码题目答案、不增加未授权模型补救调用。GPT-1 自身的 512-token 训练长度也不能未经限定直接当最大窗口；GPT-2 的 512→1024 修改可作为注明来源的对比依据。

## 验证工具改动与离线回归

- 真实评测脚本新增 `--case-ids`，在索引和云调用前拒绝空、重复、未知选题或超预算选题，并在报告中记录原数据集题数、所选原题 ID 和实际集合名。
- 复核脚本支持明示的子集，同时要求子集结果与源文件检查项一一对应；旧九题报告仍可复核。新增独立的五题源文件复核政策，不复用第 88 阶段检查结论。
- 后端离线回归 **373 项通过**，Ruff 通过。前端代码本阶段未改动；第 93 阶段的 29 项是历史回归成绩，不计为本轮重跑。
- 本地 backend、worker、frontend、BGE embedding 和 reranker 均健康，backend 与 `http://localhost:3300/` 返回 HTTP 200。生产功能代码保持第 93 阶段版本；评测工具改动不要求重新构建服务。

## 保留记录与离线复核

原始生成及复核文件保存于忽略目录 `backend/tmp/stage94-official/`，不提交 PDF、密钥或完整运行时答案。摘要、文件哈希和实际集合名见 `docs/evidence/official-failed-retest-2026-10-10.json`。本轮实际执行命令如下；**这是历史记录，再次执行会产生新的 API 费用，须先获得新授权**：

```powershell
docker compose -f docker-compose.yml -f docker-compose.bge-gpu.yml --env-file .env --env-file .env.bge --profile local-bge exec -T -e PYTHONPATH=/app backend python /tmp/stage94-official/evaluate.py `
  --corpus /tmp/stage94-official/corpus --supplements /tmp/stage94-official/supplements `
  --dataset /tmp/stage94-official/dataset.json --output /tmp/stage94-official/results/targeted-retest.json `
  --case-ids sixth-author bleu-improvement bert-inheritance gpt-parameter-evolution context-evolution `
  --max-model-calls 5 --allow-cloud
```

零模型调用的复核可在安装后端依赖后运行；输出路径必须不存在，脚本禁止覆盖原记录：

```powershell
python backend/scripts/review_official_first_round.py `
  --report backend/tmp/stage94-official/targeted-retest.json `
  --dataset backend/evals/official_first_round.json `
  --policy backend/evals/official_failed_retest_review.json `
  --output backend/tmp/stage94-official/review-copy.json
```

返回 `accepted=2`，退出码 1 表示仍有失败，属于本轮真实结果，不应当被启动脚本自动重试。物理页、引用片段和作者脚注均按 PDF 阅读核查流程复核；这些检查发现了机器数值命中未能识别的语义错误。并非独立学术同行评审。

## 下一步

先离线修复 BERT 关系完整性误报、GPT 参数比较的语义与来源约束、GPT-3 上下文段落召回，再申请只针对剩余失败题的有限新生成。复杂扫描表格单元格依然是独立待完善项。本轮不更新最终技术 PDF 为“全部通过”，不宣称可录制未通过的官方对比镜头，不改动现有公网部署配置。
