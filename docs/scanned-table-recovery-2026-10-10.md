# 第 92 阶段：官方扫描表格恢复

## 修复与实测

扫描 PDF 没有原生文字层，旧无边框检测却只读取原生 words；现改为使用页级 OCR 的词坐标，并兼容“标题与整张表共用一个 OCR 块”。有线框／无边框候选按位置去重，不重复累计。OCR 解析版本升级，避免沿用旧算法的检查点。

对官方 `1706.03762v1_img.pdf` 重新解析全部 15 页，输入 SHA-256 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d` 不变：36,600 字符、8 位作者、39 条参考文献，耗时 59.458 秒。表格候选由首轮 **1 个增至 4 个**；表 2 的 Transformer big **28.4**、ConvS2S Ensemble **26.36** 两个指定行值正确。原始未覆盖报告：`backend/tmp/stage92-scanned/official-full-first.json`。

第 8、9 页原图已渲染目视核对；第 9 页复杂超参数表仍有列归属、小数点错误，表 1 公式及表 4 部分合并行也未完全还原。这里只证明候选与上述两个行值，**不宣称四表全部精确**。生产解析新增 OCR 表格注意提示。严格作者名单仍与原文金标不符，报告保留 `failed`，未降低标准、未修订姓名金标。

## 回归与复现

后端 **357 项**通过；新增两项覆盖空原生层与合并标题块，已有有线框／图片回归继续通过。无 LLM 调用，不修改官方 PDF 或生产文献库。

```powershell
python backend/scripts/evaluate_scanned_recovery.py `
  --pdf 科研小助手/pdf/1706.03762v1_img.pdf `
  --manifest docs/official-pdf-manifest.json `
  --output backend/tmp/stage92-scanned/new-full-report.json
```

脚本使用本地 OCR 环境；已有输出拒绝覆盖，严格元数据失败返回非零。`inspect_scanned_layout.py` 可逐页导出候选和原页 PNG 供核对。姓名特殊字符及复杂表格单元格精度仍需继续修复，生成回答也须另行有限验收。
