# 第 78 阶段：新版真实模型三轮基线

日期：2026-10-09。代码基线 `a26acbc`；数据集 `real-pdf-bge-deepseek-comparison-2026-09-v3`；运行编号 `1791528334`。

## 实际运行

恢复本地 Docker 与标准模式，前端 `http://localhost:3300/`、后端、Worker 与两个 BGE 服务检查通过。核对七篇公开 PDF 的 SHA-256，重建并核查 1,405 个 BGE-M3 向量；所有文档期望数量与已存数量一致。

按上轮说明的公开论文范围调用 DeepSeek 官方 `https://api.deepseek.com/responses`，模型为 `deepseek-flash`。只使用 Transformer、BERT、GPT-1／2／3、ViT、ResNet 的固定检索摘录和测试问题，不发送其他文献或完整 PDF。

七案例三轮完成，未使用抽取式降级冒充模型成绩。

| 指标 | 实际结果 |
| --- | --- |
| 每轮案例通过 | 6/7、6/7、5/7 |
| 总案例通过 | 17/21（80.95%） |
| 金标事实匹配 | 50/51（98.04%），下述一项为词形检查误报 |
| 安全拒答门禁 | 3/6（50%） |
| 完整轮次门禁 | 0/3，发布门禁失败 |

## 两处定位

1. GPU 精确硬件问题三轮都未通过。模型说明入选片段缺少 GPU 型号与数量，但后端把两条无硬件信息的单来源说明组合为双来源声明，令 `insufficient_evidence=false`。第三轮也存在无关架构比较。应校验请求的硬件字段，而非只凭引用了两篇论文就认定回答完成；不得把 TPU、层数或注意力头数当作 GPU 配置。
2. 第三轮 GPT 演化问题被记为漏掉 GPT-1 的 512-token 训练序列。原始回答实际写明 “GPT-1 trains … sequences of 512 tokens”，引用 GPT-1 第 5 页；现有金标只匹配 `training`／`trained`／`训练`，未包含 `trains`。这是评测词形缺口，不是此次模型事实遗漏。修改必须升级数据集版本并留存旧成绩，不能覆盖原报告或降低双来源／页码要求。

其余比较及碳排放拒答三轮通过，LayerNorm 同一声明双来源门禁 3/3。固定样本成绩不等同任意科研问题正确率。

## 复现与机器证据

```powershell
pwsh -NoProfile -File scripts/run_real_pdf_comparison_e2e.ps1 -Gpu -Rounds 3
```

旧版原始报告保留在忽略目录：

- `backend/tmp/stage63-real-pdf/bge-deepseek-comparison-1791528334-round-{1,2,3}.json`
- `backend/tmp/stage65-release/comparison-1791528334-summary.json`

本阶段只记录真实基线与诊断，不宣布验收通过。下一阶段修复 GPU 字段拒答与金标词形，先定向复测失败项，保留旧版记录；原第 77 阶段 PDF 的“未执行”状态是当时快照，将在新结果核对后更新。公网配置及全部数据卷保持不变。
