# 紧密数值列、相邻表格与写作摘要出处续验

日期：2026-10-14。前轮 [解析与代码清理](official-parser-cleanup-acceptance-2026-10-14.md) 及其失败记录保留。机器凭据见 [本轮证据清单](evidence/official-dense-table-acceptance-2026-10-14.json)。

## 本轮结论

相邻表格标题错配和紧密小数列已修复，六张固定表的标题锚点、上下位置及 34 条选定数据行通过独立原页校验。写作补入真实摘要正文的独立证据，一次真实 DeepSeek 生成确认此前的方法概述改为引用支持它的摘要块。整份写作仍有主语、适用范围和限定词问题；全表准确性、最后运行版及六模块整体不放行。

范围为既有官方公开文献的隔离副本。没有改原件、正式文献库、旧测试成绩或已保存输出；没有新建、提交、推送或合并 Git 分支。仍在 `codex/stage94-official-failed-qa-retest`，HEAD `a119f5609cda544e5d587588c5a338051da129a1`。

## 修复及工程边界

1. **紧密数值列**：在原宽间距规则之外，识别行尾独立数值的窄间距边界；加千位分组、正文数字和上标反例，不改原值、不填不存在的值。整数首列和复杂表头不是本次全面证明的范围。
2. **标题与表格一对一匹配**：每页候选统一做增广路径匹配，不再让两个标题分别选中同一网格。把标题原 bbox 保留到快照，再传给最终 caption block；原生表格与恢复网格重叠时也保留该身份。该几何算法不是所有版式的语义正确性保证。
3. **写作方法概述出处**：只取已检索论文、前两页可见 Abstract／摘要标题后的真实正文块，保留独立页码、块号和 bbox。不从标题或元数据编造摘要。延续最多 10 块／9,500 字符的补充预算，必要跨页续段优先。
4. **补充证据标签**：新增 `local_context`，摘要分数为 0、无 recall mode，不把本地上下文当独立向量召回或重排命中；后端模型与前端类型一起更新。原检索结果的 ID、正文、位置不替换。
5. **复用验证工具**：在已有解析候选脚本中加 `--table-expectations`，对保存 AST 做离线检查；没有再增加重复的解析程序。独立期望值仅存在 eval 数据，不进入生产解析器。

本次按 PDF 技能用 Poppler 渲染完整原页，并逐页对照；不凭解析器输出反推答案。原生解析版本为 `1.26.4-layout100`，OCR 基础版本为 `1.26.4-ocr-layout95`。本轮没有重新验收官方扫描 PDF 或 541 页教材。

## 解析与表格验证

| 范围 | 结果 | 限制 |
| --- | --- | --- |
| 首轮四篇原生 PDF／120 页 | 基础检查通过 | 早于最后写作改动，指纹单独保留 |
| 最后候选 20 篇／651 页 | 20/20 基础检查通过；20 组 PDF／AST SHA 校验无差异 | 不等于全 39 文件最后同版或逐字／全表准确 |
| BERT 物理第 7 页 Table 2、3、4 | 三个标题分别对应自己的表；23 条数据行通过 | 不将多级表头扁平化结果等同完整 colspan 还原 |
| ViT 第 16 页 Table 7 | 六条数据行及标题边界通过 | 图表假阳性等其他页问题仍保留 |
| Transformer v1 第 8 页 Table 2 | 两条选定 BLEU 行、标题锚点通过 | 仅两列 BLEU，不含所有计算成本单元格 |
| T5 第 14 页 Table 1 | 三条数据行及标题边界通过 | 不证明 T5 全部表格完整 |

独立转录文件：[六表期望](../backend/evals/official_table_identity_rows.json)。校验结果：[初次](../backend/tmp/official-dense-table-2026-10-14/table-gold-1/report.json)、[更正测试标注后](../backend/tmp/official-dense-table-2026-10-14/table-gold-2/report.json)。

初次是 5/6，原因是验收标注误把 Transformer 表标题写成在表下；完整原页明确在表上。仅更正该布尔标注，没有改数值或生产解析来迎合答案；[原期望快照](../backend/tmp/official-dense-table-2026-10-14/expectations-v1.json) 的 SHA 与初次报告一致，保留初次失败。更正后 6/6、34 条选定数据行通过。BERT 原页的一处空单元格继续保留为空，不补造横线。

原页截图在 `backend/tmp/official-dense-table-2026-10-14/source-pages/`：`bert-7`、`vit-16`、`transformer-8`、`t5-14`；另有八张写作出处原页，共十二张本轮截图。Poppler 原文 xref／字体警告保留在工具输出，未对 PDF 原件修补。

## 真实写作确认

沿用前轮固定英文 idea，仅发送已声明公开 ViT、CLIP、LoRA 的有限摘录。本轮 **1 次 DeepSeek 请求，0 自动重试**，模型 `deepseek-flash`。检索是词法候选＋实际 TEI BGE 重排（`http://localhost:8002`），不是稠密向量全链路验收。24 个 `reranked` 标签包含继承标签的邻段，不能当成 24 次独立重排请求。

原始结果：[输出](../backend/tmp/official-dense-table-2026-10-14/writing-generation-1/writing-outline.json)、[请求及返回](../backend/tmp/official-dense-table-2026-10-14/writing-generation-1/calls.json)、[机器报告](../backend/tmp/official-dense-table-2026-10-14/writing-generation-1/report.json)。三篇首页书目身份、三个章节、证据 ID 有效性通过；原始 11 条，发布 11 条，25 个证据；机器报告为 `source_checks_passed`，不是科学质量通过。

人工原页审查：[独立续记](../backend/tmp/official-dense-table-2026-10-14/manual-writing-review.json)，不改原机器报告。

- 第 0 条原问题关闭：现在引用 `E19`／物理第 1 页／`p1-b7` 真实摘要正文，概述和预训练／迁移条件均在所引块内；不再引用同页中无关的 NLP 介绍段。标签为 `local_context`、分数 0。
- 第 10 条跨页问题确认：第 9／10 页各有独立引用 `E12`／`E16`，没有只靠未结束前半段补全结论。
- 第 1 条保留：把研究比较三类架构写成 ViT 与 ViT 自我比较，主语需要修正。
- 第 2 条保留：预训练规模结论省略下游微调到 ImageNet 的范围条件。
- 第 7 条限定词保留：指定数值和比较对象有来源，但把 “can reduce” 强化为无条件 “reduces”。
- 第 5 条核心内容有原页依据，但 CLIP 双栏引用摘录穿插左栏脚注；这不是干净的文本阅读顺序验收。
- 其余核心陈述按原页逐条核对，仍不把一次样本写成稳定性／统计准确率。11 条内容也超过预期的简洁写作预算，需要结构与论证质量收口。

没有本轮新综述生成。前轮综述的初始化条件、内部 P 标签可读性及 Future Work 最新解决状态未被本次写作确认覆盖。

## 回归与可复现凭据

最后 app 指纹 `afa28edeb73dadc3848f0873fb5bbbbdfa7f35838090aef48cd0e99b4decef50`，126 个 Python app 文件；依赖 SHA `b0e75e31a3ca8f034e095fd0667e3be7e380975548c5acce90db94f96ed7283f`。20 篇重解析、真实写作及后端全回归使用该 app；最终核对未变化。

- 后端全回归：**1,051/1,051**，0 failure／error／skip；[JUnit](../backend/tmp/official-dense-table-2026-10-14/backend-1.xml)。最初两个表格反例和新增枚举失败记录均保留，不覆盖失败 XML。
- 前端：**38/38**，`tsc --noEmit` 通过；[本轮日志](../backend/tmp/official-dense-table-2026-10-14/frontend-checks.txt)。这不是最新浏览器／生产镜像验收。
- Ruff：整个 `backend/app` 及本轮解析脚本、三份相关测试通过，不宣称整个仓库所有历史脚本都已规范化。
- 前轮 57 项清单重新核对：40 份不可变结果产物 SHA 不变，8 个代码路径按本次修复变化；旧记录没有改写。最新产物哈希见本轮机器清单。

可用现有工具复查保存的候选，不需再解析或调用模型：

```powershell
python backend/scripts/prepare_official_parser_candidate.py `
  --fixture backend/tmp/official-dense-table-2026-10-14/parser-20-1 `
  --output-dir backend/tmp/official-dense-table-2026-10-14/table-gold-new `
  --table-expectations backend/evals/official_table_identity_rows.json
```

使用项目依赖环境，输出目录必须尚不存在；本地大体积 PDF／AST／云请求产物按现有忽略规则不上传公开仓库。仓库保留脚本、独立期望和摘要记录，提交前另整理评审可访问的测试依据。

## 后续门禁

先处理写作条件与比较主语、双栏脚注顺序及原生图表假阳性，再做有限最后同版的生成与实际运行 API／浏览器确认。当前 Docker 与 BGE 健康恢复，但应用仍是旧镜像；本轮未构建、重启服务或安装源码，不宣称网页已体现修复。官方全库最后同版、实时检索／订阅、Future Work 时间截点、多用户边界、干净安装、正式技术 PDF／视频、公网／账号、密钥轮换与 Git 发布仍分别待验。
