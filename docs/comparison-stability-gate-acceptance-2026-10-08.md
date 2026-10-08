# 第 72 阶段：跨文献稳定性评测门禁

日期：2026-10-08

## 实现与比赛要求

对应模块二的多文献推理、原文定位与证据不足时拒答。在原有七篇论文、五组问题基础上，数据集升级为 `real-pdf-bge-deepseek-comparison-2026-09-v3`，包含五组正向比较、两组拒答案例、17 条必需事实。

新增两组问题：

- Transformer 与 GPT-2 的 LayerNorm 位置差异：同一条最终比较声明必须同时引用 Transformer 第 2 页和 GPT-2 第 4 页的指定段落；分别罗列两篇事实或只有一侧引用不能通过。
- BERT 与 GPT-1 的精确 GPU 型号及数量：缺少两侧原文支持时必须拒绝比较，不能把 TPU 当作 GPU，也不能填入模型记忆中的硬件信息。

新增汇总脚本 `backend/scripts/summarize_comparison_stability.py`，报告每轮／每案例通过率、事实命中率与遗漏率、安全拒答率、证据集合 Jaccard、声明数量范围和延迟。默认发布阈值要求全部轮次、案例和必需事实通过、遗漏率为零、安全拒答率为 100%。

缺失案例计入预期运行次数和事实遗漏；重复案例、数据集／Provider／检索模式不一致、模型变化均会失败。拒答中包含不安全声明时不能计为安全拒答。阈值比较保留完整精度，避免四舍五入掩盖少量失败。

## 验证结果

- 19 项针对性单元测试通过，包括双来源引用、缺失案例、事实遗漏、错误 Provider、模型变化和不安全拒答。
- 本阶段 Python 文件 Ruff 检查通过。
- PowerShell 脚本使用 PowerShell 7，并在后端容器中汇总报告，无需宿主机额外安装 Python。

本阶段没有执行新版七案例的真实三轮模型评测。之前第 65／66 阶段的数据集仍是五组问题，其成绩不能外推到本阶段新增案例。

## 复现

单元测试不调用外部 API：

```powershell
Push-Location backend
python -m pytest tests/test_real_pdf_comparison_evaluation.py tests/test_comparison_stability_evaluation.py
ruff check scripts/evaluate_real_pdf_comparison.py scripts/summarize_comparison_stability.py tests/test_real_pdf_comparison_evaluation.py tests/test_comparison_stability_evaluation.py
Pop-Location
```

真实评测命令（会将公开论文检索片段发送给配置的 DeepSeek，并产生 API 费用）：

```powershell
pwsh -NoProfile -File scripts/run_real_pdf_comparison_e2e.ps1 -Gpu -Build -Rounds 3
```

机器报告保存到 `backend/tmp/`，不提交到仓库。新增 GPU 拒答用例仍需真实评测确认服务行为。
