# 数值事实完整性修复验收（第 66 阶段，2026-09-27）

## 修复目标

第 65 阶段的独立浏览器调用暴露了生成阶段的漏答：BGE 已找到 GPT-2/3 参数量或 GPT-1 训练序列长度的原文，DeepSeek 有时仍未把数值写入最终声明。本轮只处理这两类**被明确请求、且入选原文已有数值**的缺失，不把“已检索到”误称为“已回答”。

比较服务按论文逐一检查首轮声明：参数量须与该论文原文中的数量一致，训练序列长度须有该论文训练序列的 token 数和对应引用。英文所有格提问（例如 GPT-1's training sequence）只检查被点名的论文。若缺失，服务只向已有原文锚点发起一次定向补充请求；新增声明与首轮声明一起经过原有 Evidence ID、数值白名单和来源校验。补充失败或校验后仍缺失时，会保留已核验的部分结果、给出警告并标记 `insufficient_evidence`，不会把漏答标为完整答案。可在响应 `trace` 的 `llm.audit_numeric_completeness` 步骤观察触发状态。

## 实测结果

| 检查 | 结果 | 报告 |
| --- | --- | --- |
| BGE-M3 + BGE Reranker + DeepSeek Flash 后端独立三轮 | 3/3 轮、15/15 用例通过 | `backend/tmp/stage65-release/comparison-1790517183-summary.json` |
| 完整浏览器独立调用两轮 | 每轮 5/5 用例通过；每轮 14/14 指定事实均有原文页码锚点；拒答及 PDF 引用跳转通过 | `backend/tmp/stage66-fact-completeness/browser-full-1.json`、`browser-full-2.json` |
| GPT-1/2/3 浏览器专项再调用两次 | 每次 6/6 指定事实通过 | `backend/tmp/stage66-fact-completeness/browser-gpt-3.json`、`browser-gpt-4.json` |
| 可控补救路径 | 单元测试覆盖首轮漏答、有效补充、虚构 999B 被拒、GPT-1 训练序列与 GPT-3 context window 分离、每篇论文的真实数值匹配、双次 Provider 调用审计 | `backend/tests/test_paper_comparison.py`、`backend/tests/test_real_pdf_comparison_evaluation.py` |

完整浏览器脚本现记录 `numeric_audit` 状态。上述真实调用的该字段均为空：它们的首轮生成恰好已经覆盖指定数值，**没有实测触发补救调用**；这只能证明修复未破坏正常路径。补救分支的正确性目前由可控测试支持，不能把这组真实通过率外推为任意论文或提问的稳定正确率。后端全量测试、Ruff 检查、前端 15 项测试、TypeScript 类型检查与 E2E 脚本语法检查均通过。

## 复现与边界

在已配置本地 DeepSeek 密钥及 BGE 服务的环境执行：

```powershell
pwsh -NoProfile -File scripts/run_real_pdf_comparison_e2e.ps1 -Gpu -Rounds 3
$reportPath = (Get-ChildItem backend/tmp/stage63-real-pdf/*-round-3.json | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
node frontend/e2e/realPdfComparison.mjs --url http://localhost:3200 --backend-report $reportPath --output backend/tmp/stage66-fact-completeness/browser-full-2.json
```

第二条命令需要已运行的前后端和第一条命令生成的后端报告。运行会调用付费模型；本地 PDF、机器报告与密钥均不提交 Git。

本轮是**有限的数值完整性规则**，并非通用事实完整性证明。它尚未覆盖 BLEU、GPU 型号、所有表格数值或非数值语义关系；即便数值与引用在同一段原文中，也仍需人工检查所述语义关系。官方原始扫描件、在线演示和比赛材料验收也不由本轮证明。
