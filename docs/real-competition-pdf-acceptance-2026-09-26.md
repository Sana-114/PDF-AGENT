# 真实比赛场景 PDF 验收（第 62 阶段，2026-09-26）

## 数据来源与边界

本轮补齐了赛题点名的 **541 页真实教材**、BERT，以及 GPT-1/2 原始论文；沿用先前下载的 Transformer v1/v7 和 GPT-3 v4。固定下载入口与 SHA-256 见 `docs/pdf-regression-corpus.json`。PDF 二进制、临时索引和机器报告保存在 `.gitignore` 排除的目录，不进入 Git。首次下载需要联网；后续验收只读取本地文件。

| 文件 | 来源 | 页数 | 主要验证 |
| --- | --- | ---: | --- |
| `1706.03762v1.pdf` / `1706.03762v7.pdf` | arXiv 固定版本 | 15 / 15 | 双栏解析、版本演进、Transformer 原文证据 |
| `1810.04805v2.pdf` | arXiv 固定版本 | 16 | BERT 架构、作者机构、跨论文证据 |
| `gpt1-openai-2018.pdf` | OpenAI 原始报告 | 12 | Decoder 架构、序列长度、作者机构 |
| `gpt2-openai-2019.pdf` | OpenAI 原始报告 | 24 | 参数量、Context Window、LN 位置 |
| `2005.14165v4.pdf` | arXiv 固定版本 | 75 | GPT-3 参数量、上下文窗口、架构继承 |
| `UnderstandingDeepLearning_02_09_26_C.pdf` | 教材作者 GitHub v5.0.3 发布页 | 541 | 长文档分批解析、中断续跑、内存、章节检索 |

人工渲染检查了教材的封面、中间章节和末页，以及 BERT、GPT-1/2 首页。确认下载的是可阅读的原始论文/教材，而非只有文件名相似的占位 PDF。对原文的数值和架构断言，逐页核对后写入 `backend/evals/competition_real_pdfs.json`。

组委会原始 `1706.03762v1_img.pdf` **没有公开可验证的下载入口，也未在本机找到**。仓库现有同名文件是从 arXiv v1 栅格化生成的 15 页无文本层替代样本，不能称为“官方扫描版通过”。

## 实测结果

1. 真实 541 页教材以 25 页为一批，在第 50 页后模拟中断，恢复时从第 75 页继续；22 批全部完成。解析 1,312,857 字符、533 个目录节点、386 个图对象、1,225 个公式候选、1,187 条参考文献。总耗时 30.433 秒，Python 跟踪峰值 43.75 MB，低于本轮 900 秒 / 512 MB 阈值。机器报告：`backend/tmp/stage62-udl-long.json`。Windows 上没有进程 RSS 指标，因此 43.75 MB **不是**整进程总内存或 GPU 显存。
2. BERT、GPT-1、GPT-2 和教材四份真实 PDF 的解析金标准全部通过：准确页数、标题、作者、机构与参考文献阈值均达标。GPT-2 的脚注数字分隔作者原先只识别 4/6 位，现识别 6/6；教材版权页单词 `Department.` 原先被误判为机构，现不再输出。机器报告：`backend/tmp/stage62-strict-parser.json`。
3. 将 Transformer、BERT、GPT-1/2/3 和教材共六份真实 PDF 建成隔离的内存索引，执行 6 个问答检索用例、15 条逐页原文金标。**6/6 用例、15/15 证据命中，15/15 页码与坐标锚点有效**；平均倒数排名 0.8667。BERT 对 Transformer、GPT-1/2/3 演进两组跨文献覆盖均通过。PDF 的换行断词（如 `en- coder`）补充了检索 token，英文 `context window` 增加 `nctx` 查询扩展。机器报告：`backend/tmp/stage62-rag-eval.json`。
4. 既有的 15 页 Transformer **扫描替代样本**复跑 OCR 门禁通过：15/15 页走 OCR，识别正文 36,624 字符、8 位作者、25 个目录节点、36 条参考文献，用时 69.133 秒。这只验证可复现替代样本，不代表组委会原始扫描版的版式和质量。
5. 再按赛事顺序执行“v7 已在库、随后上传 v1”的版本判定：重复预警、现有 v7 更高版本建议、带页码/块定位的修订差异均通过。正文指纹重合度为 0.60，结合论文身份的判定分数为 0.82；前者不能解释为全文语义等价率。

第 3 项采用确定性 BM25 基线，**不调用 LLM、BGE 或外部 API**。两组“跨文献通过”只代表各论文的证据独立可检索，不代表模型已生成正确的对比结论，也不证明图表/公式的 LaTeX 语义完全准确。教材的“图对象”和“公式候选”是解析器检测数量，并非人工逐条确认的正确率。

## 复现

在仓库根目录先运行 `scripts/fetch_pdf_corpus.ps1` 下载语料。后端环境需安装 `backend/requirements.txt`，OCR 回归需配置含 `eng` 与 `chi_sim` 的 `TESSDATA_PREFIX`。以下命令可在后端容器中复现主要门禁（将主机语料挂载到容器后调整路径）：

```bash
python scripts/evaluate_long_document.py /path/to/UnderstandingDeepLearning_02_09_26_C.pdf \
  --batch-pages 25 --interrupt-after-pages 50 --expected-pages 541 --strict

python scripts/evaluate_pdf_corpus.py /path/to/parser-subset \
  --manifest /path/to/pdf-regression-corpus.json --strict

python scripts/evaluate_competition_pdf_rag.py \
  --corpus-dir /path/to/regression-corpus \
  --manifest /path/to/pdf-regression-corpus.json --strict
```

检索评测在每次运行时重建隔离索引，先校验每份 PDF 的 SHA-256；缺文件、字节不符、无原文锚点或少一个跨论文来源都会失败。`--output` 可保存完整 JSON 报告。金标数据、代码和命令均可提交到公开仓库；PDF、运行时报告与密钥不提交。

## 尚待验收

- 收到组委会**原始扫描 PDF** 后，应复跑 OCR、表格/图区域、引用跳转及扫描页坐标，不以栅格化替代样本充当其结果。
- 需要在实际部署的 BGE + DeepSeek 链路上，对同一组六篇论文逐条执行生成、引用白名单、数值审计、拒答和跨文献矛盾检查；本轮 BM25 检索门禁不能替代这些端到端结论。
- 对教材中表格、图表、公式、References 的抽取准确率，应补充逐页人工标注。当前报告只证明结构量与部分金标，不证明 541 页所有节点都正确。
