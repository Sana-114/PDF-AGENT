# 真实 PDF 的 BGE + DeepSeek 跨论文验收（第 63 阶段，2026-09-26）

## 验收边界

本轮接续第 62 阶段的确定性检索金标，实际执行五篇原始论文的 **PDF 上传与解析 → BGE-M3 向量索引与检索 → BGE Reranker 重排 → DeepSeek Flash 生成跨论文结论 → 逐条引用与数值审计**。使用的 Transformer v1、BERT v2、GPT-1、GPT-2、GPT-3 v4 文件与 SHA-256 固定在 `docs/pdf-regression-corpus.json`；论文原件、模型权重、API Key、运行时回答和报告均不提交到 Git。

每个评测用例都按固定 SHA-256 解析数据库中的唯一 ready 文档，调用生产使用的 `PaperComparisonService`，保存最终发布的回答和模型原始结构化 claims。门禁要求 `deepseek` Provider、一次成功的 `llm.compare_documents` 调用、所有证据均为 `reranked`、有效 PDF 页码/块/坐标锚点、每条结论的有效引用和数值出处。若 BGE 服务失败而退回 `hybrid`，即使答案文本正确也判失败。

## 金标与实测

金标数据见 `backend/evals/real_pdf_comparison_grounded.json`。本地 Docker 标准 BGE 模式、DeepSeek Flash 下连续两轮直接评测及一次最终镜像的一键脚本复跑，均为 **3/3 用例、8/8 必需原文事实通过**；两个正向用例分别引用两篇与三篇原文，反向用例拒绝虚构两个训练任务的精确 kg CO2e 数字。所有返回证据为 `reranked`，生成步骤均为 `ok`，没有静默回退。最终脚本核对了 1,071 个文本块与向量点，发布的 19 条结论也逐条对照了引用原文。

| 用例 | 原始 PDF 证据 | 两轮结果 |
| --- | --- | --- |
| BERT 对 Transformer | Transformer v1 第 2 页的 Encoder 残差与 LayerNorm；BERT v2 第 2–4 页的双向 Encoder、MLM | 2/2 必需事实，跨文献结论通过 |
| GPT-1/2/3 演进 | GPT-1 第 5 页的 512-token **训练序列**；GPT-2 第 4 页的 117/345/762/1542M、512→1024 和 LN 输入位置；GPT-3 第 8 页的 175B、2048 和 pre-normalization | 6/6 必需事实，三文献覆盖通过 |
| 缺失数值拒答 | Transformer 与 BERT 原文未在本轮引用证据中提供双方精确的 kg CO2e 排放量 | 0 条发布结论，明确证据不足 |

GPT-1 原文直接写的是 **512-token 训练序列**，不能单凭这一句把它改称为独立报告的“context window”。GPT-2 原文另有“context size from 512 to 1024 tokens”，GPT-3 则明写 `nctx = 2048`。本轮人工查看了上述 PDF 原页和 BERT/Transformer 的架构原页，确认金标与页面一致。两轮直接评测报告保存在忽略目录 `backend/tmp/stage63-e2e-pass1.json`、`backend/tmp/stage63-e2e-pass2.json`；最终一键脚本报告保存在 `backend/tmp/stage63-real-pdf/`。它们是当时生成的运行结果，不是仓库中的固定答案。

## 发现与修复

第一次真实跨论文调用虽然得到 DeepSeek 的答案，证据却标为 `hybrid`。TEI 日志表明一次发送 36 个候选，超过本地服务的 32 条最大批量并返回 HTTP 422。现在 `TeiReranker` 以 16 条微批调用，再按全局索引合并分数；新增 36 条候选回归测试，防止“模型生成成功”掩盖“重排失败”。

GPT-1/2/3 原先的长问题只触发一次宽泛检索，关键 context/参数段落容易被架构概述挤掉。对于冒号后明确枚举的比较维度，现在追加逐维度检索、去重并为各维度保留证据，然后再用原有多样性选取填充预算。提示词同时要求每个数值直接引用包含该数值的段落；缺少某一维度时保留其他有证据的维度，而不是编造缺失值或误称论文没有记载。

## 复现

先在仓库根目录准备 `.env`、`.env.bge`（不要提交密钥），下载固定语料：

```powershell
.\scripts\fetch_pdf_corpus.ps1
.\scripts\run_real_pdf_comparison_e2e.ps1 -Gpu -Build
```

没有 NVIDIA 容器运行时则去掉 `-Gpu`，使用 CPU BGE。`-Build` 会重建后端与 Worker；日常重复验收可省略。脚本启动所需服务、探测 BGE、上传并等待五篇 PDF、严格重建这五篇的 1024 维向量索引，再运行 `backend/scripts/evaluate_real_pdf_comparison.py --strict`。最终完整 JSON 保存在忽略目录 `backend/tmp/stage63-real-pdf/`，脚本失败时也尽量保留报告。上述脚本会调用 DeepSeek API，产生相应费用；运行前确认 Key 可用。首次启动 BGE 需要下载模型，模型权重留在 Docker 缓存，不随代码提交。

## 不宣称的部分

这是五篇论文、三个问题的严格端到端抽样验收，不是所有论文或所有问题的正确率证明。自动门禁验证金标事实的 **回答关键词、指定原文、页码与同一条 claim 的引用关联**，并审计可提取的数字；复杂非数值语义仍需人工复查。拒答只针对本轮原文证据无法支持的精确排放量比较，不证明这些数值在其他资料中不存在。本轮也不覆盖官方原始扫描版、541 页教材的 LLM 回答、真实争议的冲突判定或人工确认状态。
