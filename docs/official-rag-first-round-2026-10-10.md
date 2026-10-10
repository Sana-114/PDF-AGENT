# 第 88 阶段：官方原题问答与划词翻译首轮验收

日期：2026-10-10。接续第 87 阶段的官方 PDF 解析门禁。本轮不是扩大随机问答，而是按赛题原题检查答案完整性、论文归属和原文锚点。

## 输入、调用和隔离范围

- 4 个官方文件：Transformer v1、`N19-1423.pdf`（BERT）、`language_understanding_paper.pdf`（GPT-1）、`2005.14165v4.pdf`（GPT-3）。官方目录没有 GPT-2 原文，使用既有公开 `gpt2-openai-2019.pdf` 补充，单独标记 `public_supplement`；五份文件都先核对 SHA-256。
- 生产 `ResearchAgent.ask` 配合真实 BGE 混合召回、BGE Reranker、DeepSeek Flash。多文问题扩大候选并按文献分散取样；这不是前端多轮会话、`compare` 端点或全部浏览器问答的验收。
- 使用内存数据库、独立解析目录和随机临时向量集合，不修改用户文献库，不发送库中的私人论文。问题中的“其中／这篇论文”用显式文献范围限定，不冒充已验证多轮指代。
- 授权上限 **10 次**；实际 **9 次原题问答＋1 次短划词翻译**。每题只生成一次，不自动重试。首页翻译复用已有缓存，离线复核零调用；Docker／前端访问故障均发生在模型请求之前，其失败报告保留。

## 逐题结果

原始机器门禁为 **5/9**。逐条核对最终发布声明和原文后，发现上下文题“512 被提到”不等于“明确回答 GPT-1”，修正来源归属检查并对同一批答案离线计分，结果 **4/9**；不是新增一轮模型测试。下表的原文页码为 PDF 物理页号。

| 原题 | 本轮判定 | 观察与原文检查 |
| --- | --- | --- |
| 第 6 作者及机构 | 未通过 | 最终安全拒答；首页检索块缺少完整、有序作者网格。原生 AST 已能识别 8 位作者，但问答没有使用完整元数据证据，不能把解析成功当作答对 |
| Adam 的 β1／β2 | 通过 | 0.9／0.98，与 Transformer v1 第 7 页及所引原文一致 |
| Base 训练 GPU | 通过 | 8 块 NVIDIA P100，与第 7 页一致 |
| 英德 BLEU、旧最佳及提升 | 未通过 | 回答 28.4 和“超过 2.0”，遗漏已召回表格中的旧最佳 26.36 及推导差值 2.04。第 8 页包含 ensemble 基线；不能改成 25.16／3.24 来迁就答案 |
| LayerNorm 引文作者及年份 | 通过 | 三位完整作者和 2016，与第 10 页参考文献一致 |
| BERT 的承袭与抛弃 | 未通过 | 分别回答了编码器和原始编码器—解码器事实，但未明确形成同时引用两篇原文的承袭／去除 Decoder 联合结论 |
| GPT-1／2／3 参数演化 | 未通过 | GPT-3 第 8 页的 175B 已找到；原始生成正确写为 1750 亿，却被数值发布门禁当作未出现的数字过滤。最终答案缺项；117M 也没有在同一有据声明中明确归属 GPT-1 |
| GPT-2 的 LN 位置调整 | 通过 | 引用 GPT-2 第 4 页：移至子模块输入，并在最后自注意力块后新增 LN；没有虚构 GPT-1 原文直接写了 post-norm |
| 三代 Context Window | 未通过 | 回答 GPT-2 从 512 增到 1024、GPT-3 为 2048，但明确表示 GPT-1 未给出。原机器仅匹配 512 误算覆盖 GPT-1；纠正后判为不完整，不将谨慎限定当作幻觉 |

“通过”是本次固定问题的助手逐条来源复核，不是独立专家审稿或多轮稳定性证明。所有九题证据均标记混合召回及重排；这些标记和位置存在，也不能单独证明跨论文推理正确。

## 阅读器真实复验

真实 Edge、1920×1080：已有首页缓存、原生 PDF 短选区翻译、双向滚动三项通过。第 86 阶段的首次 HTTP 502 和本轮两次环境访问失败均保留，没有用静态回答替换。

- PDF 文本层实际选区：`mechanism. We propose a new simple network architecture, the Transformer,`；DeepSeek 返回对应中文，HTTP 200，页面显示真实翻译浮层，截图已检查。
- 左侧滚动进度 0.3503，对侧 0.3499；反向 0.1751／0.1754；暂停联动后左侧不动。
- 这是滚动进度映射，不是语义段落对齐。只证明一个短选区和一页缓存，不宣称整篇翻译及所有公式保护的真实质量已经验收。

## 证据与复现

机器摘要及完整报告 SHA 见 [提交到仓库的证据摘要](evidence/official-first-round-2026-10-10.json)。完整模型回答、引用摘录、失败项和截图保留本地，不覆盖旧结果：

- `backend/tmp/stage88-official/first-round.json`：9 次原始生成，机器 5/9。
- `backend/tmp/stage88-official/first-round-reviewed.json`：同一批答案的离线复核，4/9。
- `backend/tmp/stage88-official/reader-selection-after-port-recovery.json`：真实阅读器三项通过及截图路径。
- `reader-selection-first-round.json`、`reader-selection-after-environment-recovery.json`：模型调用前的环境失败。

零费用复核命令（后端依赖已安装，从仓库根目录执行；每次使用新的输出路径）：

```powershell
python backend/scripts/review_official_first_round.py `
  --report backend/tmp/stage88-official/first-round.json `
  --dataset backend/evals/official_first_round.json `
  --policy backend/evals/official_first_round_review.json `
  --output backend/tmp/stage88-official/review-new.json
```

未通过时返回退出码 1。脚本核对原始数据集 SHA，不改写答案、金标和原始报告。新策略要求模型名与数值出现在同一引用声明；BLEU 运算需同一正确来源中同时含两个操作数。这仍是规则检查，负面陈述、语义归属和推理合理性需要来源复核。

若重新执行实时首轮，必须另获模型调用授权。容器内独立准备五份 SHA 匹配的公开文件后可用：

```bash
python /app/scripts/evaluate_official_first_round.py \
  --corpus /app/tmp/new-official/corpus \
  --supplements /app/tmp/new-official/supplements \
  --dataset /app/evals/official_first_round.json \
  --output /app/tmp/new-official/results/first-round.json \
  --max-model-calls 9 --allow-cloud
```

这不是自动上传官方目录的脚本；不得指向私人文件，也不要在已有报告路径复跑。离线回归为后端 **326 项**、前端 **29 项**，Ruff 和 TypeScript 检查通过。

## 下一步：先修失败项，再有限复验

1. 为作者序号问题构造来自 AST 的完整、有序作者与机构证据，保留首页块及位置，不硬编码 Transformer 答案。
2. 将 BLEU 基线、差值及跨模型必答字段纳入答案完整性校验；计算标明“推导”，不能把差值冒充原文直接引用。
3. 为有据的 175B／1750 亿等单位换算建立确定性数值证明，保留抗幻觉门禁，不直接放行任意新数字。
4. 对 BERT 架构与 Context Window 做来源关联的联合结论校验，区分原文直接陈述和架构推断。
5. 修复后只请求针对失败项的有限模型复验；官方扫描件的姓名字符与表格恢复、其余论文批量解析和 20 篇图谱仍待独立推进。

本轮未完成完整比赛交付。技术 PDF 仍是第 82 阶段快照；新增失败与修复结果需在最终技术材料中同步。MP4、公网部署、评审账号与最终冻结仍待完成，现有部署配置保持不变。
