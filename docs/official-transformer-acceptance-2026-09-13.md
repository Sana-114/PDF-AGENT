# Transformer 官方版本样本验收（2026-09-13）

## 验收范围

本轮使用 arXiv 官方固定版本 `1706.03762v7.pdf` 与 `1706.03762v1.pdf`，复现赛题规定的“先将 v7 入库，再上传 v1”顺序。PDF 二进制文件和动态 JSON 报告不进入 Git，仅提交下载入口、验收脚本、解析修复和本报告。

| 文件 | SHA-256 | 大小 | 页数 |
| --- | --- | ---: | ---: |
| `1706.03762v1.pdf` | `0A9BFF1BE3575CF9FE26A4A3D3A9175BC791ECEC90DE8B9DEBA4CFF75D2B9476` | 2,128,686 B | 15 |
| `1706.03762v7.pdf` | `BDFAA68D8984F0DC02BEACA527B76F207D99B666D31D1DA728EE0728182DF697` | 2,215,244 B | 15 |

验收环境使用 PyMuPDF 1.26.4 和 `pymupdf-layout` 解析器。通过 Poppler 渲染两版首页与第 11 页，人工确认双栏正文、作者区、公式/引用标记和参考文献版式均可正常读取。

## 解析结果

每 5 页生成一次解析检查点，两份文档均通过 `docs/pdf-regression-corpus.json` 中的页数、文本量和标题断言。

| 指标 | v1 | v7 |
| --- | ---: | ---: |
| 标题 | Attention Is All You Need | Attention Is All You Need |
| 作者 | 8 | 8 |
| 原生文本字符 | 36,854 | 39,498 |
| 抽取文本字符 | 37,307 | 39,981 |
| 标题树节点 | 25 | 25 |
| 表格 | 5 | 5 |
| 图 | 3 | 3 |
| 公式候选 | 11 | 10 |
| 参考文献 | 36 | 40 |
| 解析耗时 | 17.263 s | 18.590 s |
| Python 峰值内存 | 6.95 MB | 6.60 MB |
| 警告 | 0 | 0 |

真实样本首次运行发现页边竖排 arXiv 标识被错误选为标题，且 v1 的“作者、机构、邮箱同块”布局导致作者漏提取。本阶段已按几何方向排除竖排页边标识，按标题与 Abstract 的纵向区域抽取作者，并根据脚注标记拆分姓名和机构。随后再次执行官方样本，标题与 8 位作者均正确恢复。

## 版本去重与修订差异

`evaluate_version_pair.py` 直接复用生产环境的指纹、重复判定、版本建议与证据差异服务，结果如下：

- 两份文件均识别为 arXiv `1706.03762`，版本分别为 v7 与 v1；
- 正文指纹重合度为 `0.6203`，身份分为 `1.0000`，综合重复分为 `0.8291`；
- 命中生产阈值并触发重复预警；
- 建议为“库中已有更新的 arXiv v7，建议保留现有版本并将本次 v1 作为历史版本”；
- 两版页数相同，v1 比 v7 少抽取 2,674 个字符、少 4 条参考文献，公式候选多 1 个；
- 修订证据保留页码和块 ID，能够定位 v7 新增的参考文献、授权说明和致谢等段落；
- arXiv 页边戳与小字号编号脚注已不再污染标题树或标题差异。

## 可复现命令

```powershell
.\scripts\fetch_pdf_corpus.ps1 `
  -PaperIds transformer-v1,transformer-v7 `
  -Proxy http://127.0.0.1:7897

New-Item -ItemType Directory -Force backend\tmp\stage39
Copy-Item output\pdf\regression-corpus\1706.03762v*.pdf backend\tmp\stage39
Copy-Item docs\pdf-regression-corpus.json backend\tmp\stage39\manifest.json

docker compose exec -T backend python scripts/evaluate_pdf_corpus.py `
  /app/tmp/stage39 `
  --manifest /app/tmp/stage39/manifest.json `
  --output /app/tmp/stage39/parser-report.json `
  --batch-pages 5 `
  --strict

docker compose exec -T backend python scripts/evaluate_version_pair.py `
  /app/tmp/stage39/1706.03762v7.pdf `
  /app/tmp/stage39/1706.03762v1.pdf `
  --output /app/tmp/stage39/version-pair-report.json `
  --batch-pages 5 `
  --strict
```

## 边界

本报告完成的是官方矢量 PDF 的解析、服务级重复判定和版本差异验收。数据库写入、Celery 队列和前端冲突操作已有独立自动化测试，但本轮脚本未通过浏览器执行上传流程；扫描版 OCR 与 541 页教材仍需在后续官方样本阶段单独验收。

后续第 41 阶段逐页复核发现，本报告中的 5 个 PyMuPDF 表格结果包含第 13–15 页注意力连线图的 3 个误检。当前解析器先用页面表格编号提示筛选候选页，v1 输出 2 个可靠矢量网格；Table 1/2 的复杂无边框结构仍待专用表格适配器恢复。历史数值保留用于说明当时的原始运行结果，不再把 5 解释为 5 张可靠表格。

第 44 阶段加入图注引导的文本对齐适配器后，Table 1/2 已分别恢复为 4 列和 5 列结构，并与第 9 页的两个矢量网格去重，因此当前 v1 可靠表格总数为 4。详细算法、内容抽样和公式候选边界见 [无框表格与公式候选验收](borderless-table-formula-acceptance-2026-09-16.md)。
