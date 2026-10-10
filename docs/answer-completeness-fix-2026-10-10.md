# 第 91 阶段：答案完整性与确定性表格比较

## 生产问答变化

- 生成提示明确最终答案只来自 `claims` 数组：承袭／去除结论必须同时写入有双来源的声明，不能只出现在未发布的 `answer` 正文。
- 参数量与上下文问题逐一检查 GPT 模型名及其数值；“从 GPT-1 到 GPT-3”也要求中间的 GPT-2。数字列表、缺失声明、只回答别的模型，都不算完成。训练序列证据不直接当作 Context Window。
- 相关多文问题在实际 Ask／Conversation 路径自动按文献分散检索，而非只在评测脚本中启用。
- 未完成的字段保留有据的部分答案，明确标记“尚未完整回答”，`insufficient_evidence=true`，轨迹保存 `audit_answer_completeness`。前端显示“部分回答”；常规成功标签改为“附原文引用”，不暗示独立科学审稿已完成。
- 不新增模型重试或隐式补调用。新规则只覆盖目前明确支持的题型，不宣称能语义判断任意论文结论。

## BLEU 的可追溯补全

当问题明确指定模型变体、单一语言方向及 BLEU 提升，且所引 Markdown 表格的多级列头无歧义时，从 BLEU 列找到模型行，取表内非该模型系列参照行的最高值，包含 ensemble 候选。忽略成本列及其他语言方向；缺失列头、歧义任务或冲突表格时不猜测。

工具产出模型分数、表内参照基线及带表达式的推导差值，仍经过普通引用／数值校验。不是把测试金标写进生产代码，也不把“表内最好”无条件推广为全球最优。

用第 88 阶段真实、已保存的原文证据离线重放：实际 Transformer 表格补出了 28.4、ConvS2S Ensemble 的 26.36 和 `推导：28.4 - 26.36 = 2.04`，三项都引用原 E1。报告 `backend/tmp/stage91-completeness/replay.json`；原报告及首轮 4/9 成绩不修改。

## 验证与边界

后端全量 **355 项**、前端 **29 项**、Ruff、TypeScript 通过。新增 12 项测试覆盖多级列头、英德／英法、ensemble、负差值、缺失／冲突表格、模型归属、训练序列与窗口区别、双来源关系、当前问题与历史分离、单次生成及部分答案。

真实 BERT 联合结论与 GPT-1 跨来源归属仍须有限模型复验；提示改进和规则通过不等于真实模型已生成合格结论。全篇翻译、其他模块或新的科学事实不在本阶段范围。

```powershell
python backend/scripts/replay_answer_completeness.py `
  --report backend/tmp/stage88-official/first-round.json `
  --output backend/tmp/stage91-completeness/replay-new.json
```

该脚本只重用保存的生成／证据，零新检索、零模型调用；它不是新一轮 RAG 答题成绩。
