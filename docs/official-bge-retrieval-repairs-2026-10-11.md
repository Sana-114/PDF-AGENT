# 官方 BGE 检索修复与回归

日期：2026-10-11。修复仍在 `codex/stage94-official-failed-qa-retest` 工作区，未创建分支、提交或推送。本轮 DeepSeek 生成调用为 0。

## 本轮结果

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 原来未通过的 8 个原题，第一次复测 | 7/8 | 保留失败报告；BLOOM 首页署名仍未进候选 |
| 限定首页范围后的 8 题复测 | 8/8 | 原题、原始片段、物理页号、Top-10 均不变 |
| 32 个 PDF 的全部检索探针 | 68/68 | 作者/署名 32、方法/数值 32、引用 4；均为实际混合召回及 BGE 重排 |
| 后端离线回归 | 434/434 | JUnit 保存；0 失败、0 错误、0 跳过；7 条既有依赖警告 |
| Ruff / Git 差异格式 | 通过 | 包括新脚本与负例测试 |

68 个检索探针覆盖原来的 32 个 PDF、10,391 个块。全量检索原始报告直接判定 68/68，不是保存证据重审后的拼接成绩。指定原句在指定物理页出现，同时验证证据属于查询文档、节点实际存在且坐标有效；另行核对 68 个命中证据均与原生金标块坐标重叠。

全量新金标文件与旧金标文件的 SHA-256 完全相同：`c49e69ac83422925296957a7e55578730a970cc13861d4a4b48cdc16af62416d`。没有改题、换页、降低 Top-K 要求或用等价片段替换指定原句。原来的 58/68 原始成绩、60/68 重审成绩与本轮 7/8 的失败记录均保留。

## 生产代码修复

- 增加有限的中英领域术语扩展，例如涌现、监督、小时、速度、分位、并行和推理。词表不包含测试文件名、预期数值或作者答案，不修改原文。
- 非引用问题降低结构化参考文献的 BM25 权重，并在最终证据预算中优先正文。明确询问引用、参考文献、DOI 的问题保留引用路径；不把泛称“文献”自动判成引用查询。
- 核心方法与摘要性问题增加少量同作用域的摘要候选，经过同一个 BGE 重排器后，再保留有分数门槛的有限摘要覆盖。最多使用约三分之一预算，每篇最多两条；不强制所有答案来自摘要或第 1 页，原始分数、片段与锚点不变。
- 作者/署名查询可以用已经解析的首个署名构造检索词，但返回的仍是实际文本块，不生成缺失姓名、完整名单或机构对应关系。完整有序作者证据的原有严格来源要求不变。
- BLOOM 最后的漏召回来自“全篇排序截断后才筛首页”。现在首页范围在 BM25 评分与 Top-K 截取之前限定，避免长署名页被后部短贡献说明挤掉。无可验证署名时不会造出署名证据。

新增负例覆盖：引用问题不被正文策略覆盖；低分候选不强行提升；作者候选必须来自实际文本与指定文档；无法完整解析名单时不伪造序号；摘要不固定在第一页；只读索引拒绝创建、删除与自动回填。

## 本地模型与测试隔离

实际模型为 `BAAI/bge-m3`（1,024 维稠密向量）与 `BAAI/bge-reranker-v2-m3`。稀疏部分仍是现有哈希稀疏与 BM25，不是 BGE 学习型稀疏向量。

Docker 恢复后启动了本项目已有的 PostgreSQL、Redis、Qdrant 和 MinIO 容器，没有重建、清理或删除数据卷。BGE-M3、Reranker、Qdrant 健康接口均返回 HTTP 200；backend、worker、frontend 的容器健康状态也恢复。

复测采用新的输出目录、进程内 SQLite 和只读的原隔离 Qdrant 集合。逐文核对 PDF/AST 哈希，以及当前块与既有载荷的页码、文本、节点 ID、坐标和数量。只读适配器禁止创建集合、删除文档、索引重建及自动向量回填。没有改正式文献库或既有测试向量。

三次实际运行合计 84 次查询向量批次、84 次高层重排调用；重排器内部会分批，不能把此数字当成 HTTP 请求总数。没有云端生成、自动重试或新全文解析。全量 68 题的检索耗时之和约 21.141 秒，不包含载荷恢复、来源准备和服务启动。

## 证据和复现

- 原文页面审查：`backend/tmp/retrieval-repairs/source-audit-1/`
- 第一次 8 题复测：`backend/tmp/retrieval-repairs/failed-8-1/report.json`
- 第二次 8 题复测：`backend/tmp/retrieval-repairs/failed-8-2/report.json`
- 全量检索：`backend/tmp/retrieval-repairs/full-68-1/report.json`
- 后端回归：`backend/tmp/retrieval-repairs/full-unit-3.xml`
- 对外摘要与哈希：`docs/evidence/official-bge-retrieval-repairs-2026-10-11.json`

`backend/tmp` 的 PDF、页面图及原文片段不进入公开仓库。脚本与评估数据位于 `backend/scripts` 和 `backend/evals`。准备好旧隔离集合、解析报告及原始 PDF 后，可只读复测；输出目录必须是新的：

```powershell
python backend/scripts/evaluate_official_retrieval.py --corpus 科研小助手/pdf --manifest backend/tmp/basic-repairs/sources-2/manifest.json --probes backend/evals/official_remaining_retrieval.json --parse-report backend/tmp/basic-repairs/parse-2/report.json --override-report backend/tmp/basic-repairs/codex-web-1/report.json --query-report backend/tmp/basic-repairs/bge-3/report.json --output-dir backend/tmp/your-new-run/bge
```

无旧隔离集合时，去掉 `--query-report`，按脚本原有流程建立新的隔离索引；不能把生产集合冒充测试集合。需按现有项目指南安装依赖，先确认两个模型和 Qdrant 健康。

## 尚未验收

这证明原文证据可以被检索，不证明 DeepSeek 最终回答或跨文推理正确。之前 BERT 关系、参数排序和上下文窗口三类失败问答仍需真实生成复测，不能用本轮成绩覆盖旧问答成绩。

另 7 个官方文件（含扫描件和 541 页教材）没有在本轮重新解析。作者—机构对应、完整作者名单、所有表格单元格与公式准确性、双语阅读及其他比赛模块不属于本轮全面验收。

生产运行镜像本轮未重建；当前修复已在本地源代码与隔离测试中生效，容器健康不代表其已装载全部未提交修复。最终录屏前仍需刷新运行版本并做界面端到端检查。
