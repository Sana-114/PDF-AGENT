# 官方 PDF 基础修复与 BGE 检索首轮验证

日期：2026-10-10。工作区分支仍为 `codex/stage94-official-failed-qa-retest`，本轮没有创建分支、提交或推送，也没有调用 DeepSeek。

## 结果与边界

| 检查 | 结果 | 范围 |
| --- | --- | --- |
| 新增 32 个官方 PDF 的完整解析 | 32/32，1,297 页 | 本轮重新解析，不是重用旧 AST 判通过 |
| 基础结构与来源探针 | 32/32 | 标题片段、首位作者/团队、页序、文本量、最低结构数、一个参考文献来源片段与坐标 |
| 完整作者名单 | 7/7 | ZeRO、LoRA、GLM、GPT-4、Mistral、Instruction Pre-Training、Llama 3；名单与顺序均单独校验 |
| Codex 网页引用分隔补测 | 1/1 | 首页关键作者与第 15 页三条独立引用，要求三个不同条目 ID |
| 真实 BGE 检索，原始判定 | 58/68 | 固定 Top-10；32 个作者、32 个方法/数值、4 个引用探针 |
| 已保存检索证据重审 | 60/68 | 修正表格节点锚点的误判；没有重跑检索或修改原始报告 |
| 完整后端离线回归 | 424/424 | Ruff 与 Git 差异格式检查通过 |

这不代表全部比赛要求通过，也不是 DeepSeek 回答正确率。官方目录共 39 个 PDF，另 7 个文件此前已经测过，本轮没有将它们重新算作“当前版本已全部验收”。32 个文件仍未逐一验收完整机构对应关系、全部引用、表格单元格、公式还原、译文或跨论文推理。

## 生产解析修复

- 清理星号、菱形、段落号等作者脚注；区分真实邮箱与 `AI @ Meta` 团队署名；避免日期、机构及正文邮箱干扰作者栏。
- 利用首页字形间距分隔姓名，恢复原生文本层漏空格的连字姓名；保留原始正文文本和页面坐标，不从文件名或测试答案填充作者。Codex 本轮抽取 58 位作者，Llama 2 抽取 67 位，但这两篇未作为完整名单的精确金标验收。
- 支持分离重音符的作者—年份引用，以及无个人作者的网页/模型卡引用。人工核对 Codex 第 15 页后，恢复 CWE-327、CWE-780 和 A6:2017 三个独立条目。修正了初稿测试题中误写的 CWE-78，未把该错误题作为已执行验收。
- 矢量图聚类只流式保存路径矩形；表格识别排除有图注支持的图形区域，保留真实表格路径和跨页分段处理。PPO 的 Python 分配峰值从上一轮 323.59 MiB 降为本轮 5.71 MiB，保留 6 个图形节点；该指标是 `tracemalloc`，不是总内存或显存。
- 解析器修订号更新至 `layout96`，避免旧检查点被当成新解析结果。32 篇批量回归使用 `layout95` 快照；随后 `layout96` 的新增变化仅为网页 URL 的空格兼容，Codex 另行完整解析补测通过。

## BGE 测试方式

真实服务为 `BAAI/bge-m3`（1,024 维）与 `BAAI/bge-reranker-v2-m3`。使用学习得到的稠密向量、现有哈希稀疏召回和 BM25，再进行 BGE 重排；不是 BGE 自带的学习型稀疏向量。所有返回结果均按 `hybrid` + `reranked` 检查，不把退化的词法检索算通过。

金标从原始 PDF 的原生文本独立取样，保存页码、引文和原始块坐标；不从检索结果反推答案。四个引用问题分别显式配对 TensorFlow、CWE-327、Llama 3.1 模型卡的来源片段，避免“问题问 3.1、金标却取第一条 3”的错误。

使用进程内 SQLite 和独立测试 Qdrant 命名空间，最终选定 32 个文档、10,391 个块。两个建索引检查点在执行问题前停止并保留，分别为了纠正基准和继续使用持久 HTTP 连接。复用不变文档的向量，修正 Codex AST 后以新 UUID 索引，旧向量保留，查询只使用最终文档 ID。未修改生产数据库或原有文献库。

原始验证器误把只有 `table_id`、没有原生正文块 ID 的真实表格节点判为无效。修正后要求节点实际存在于 AST、属于相应页面、坐标与证据重叠；不存在的伪造 ID 仍拒绝。重审还对比原始 AST 的字节哈希与保存副本的 JSON 内容，不能仅因 JSON 缩进不同就判成来源变化。

## 未通过的 8 个严格检索探针

| 文件 | 探针 | 观察 |
| --- | --- | --- |
| `209_Emergent_Abilities_of_Larg.pdf` | 涌现能力定义 | 候选未命中指定首页片段，最终结果被参考文献占据 |
| `2206.07682v2.pdf` | 涌现能力定义 | 同上；两份同名论文不算两个独立主题 |
| `2101.03961v3.pdf` | 预训练速度提升上限 | 候选包含金标，重排后未进入 Top-10 |
| `2211.05100v4.pdf` | 首页作者/团队署名 | 候选未命中集体署名，返回了后部参考文献 |
| `2212.04356v1.pdf` | Whisper 训练小时数 | 未命中指定首页原句，需复核正文其他页的同义证据 |
| `2303.08774v6.pdf` | 律师资格考试分位 | 未命中指定首页原句，需复核正文与考试表格是否提供等价证据 |
| `2305.13048v2.pdf` | RWKV 训练/推理优势 | 未命中完整指定片段，需改善核心方法证据覆盖 |
| `2501.12948v2.pdf` | RL 推理训练方法 | 未命中指定首页原句，正文存在相关 RL 描述，需复核等价证据 |

这里的失败是“指定原文片段未在指定页的最终证据中出现”，不等于模型一定回答错误。本轮没有生成回答，不能据此判定最终结论。下一步应先区分真正的漏召回、重排丢失与同义证据位置差异，再修复检索并保留原始题目复测，而不是直接增加模型调用次数。

## 证据位置与复现

- 完整解析报告：`backend/tmp/basic-repairs/parse-2/report.json`
- 原生来源预览与强化金标：`backend/tmp/basic-repairs/sources-2/`
- Codex 分隔补测：`backend/tmp/basic-repairs/codex-web-1/report.json`
- BGE 实际运行与逐条证据：`backend/tmp/basic-repairs/bge-3/report.json`
- 零新请求重审：`backend/tmp/basic-repairs/bge-3/reviewed-1.json`
- 对外摘要：`docs/evidence/official-basic-repairs-bge-2026-10-10.json`

上述 `backend/tmp` 输入与运行产物不进入仓库。正式脚本和金标保存在 `backend/scripts`、`backend/evals`；新增测试随代码提交。运行真实检索前应确认两个本地模型服务健康，测试脚本仅允许本机地址并禁用云端生成。

```powershell
python backend/scripts/prepare_official_remaining.py --corpus 科研小助手/pdf --inventory backend/tmp/stage87-official/inventory.json --metadata backend/evals/official_remaining_metadata.json --output-dir backend/tmp/your-run/sources
python backend/scripts/evaluate_official_batch.py --corpus 科研小助手/pdf --manifest backend/tmp/your-run/sources/manifest.json --report-dir backend/tmp/your-run/parse
python backend/scripts/evaluate_official_batch.py --corpus 科研小助手/pdf --manifest backend/evals/official_codex_web_references.json --report-dir backend/tmp/your-run/codex-web
python backend/scripts/evaluate_official_retrieval.py --corpus 科研小助手/pdf --manifest backend/tmp/your-run/sources/manifest.json --probes backend/evals/official_remaining_retrieval.json --parse-report backend/tmp/your-run/parse/report.json --override-report backend/tmp/your-run/codex-web/report.json --output-dir backend/tmp/your-run/bge
```

所有输出目录必须是新的。数据集原始 PDF 不随代码公开分发；环境与依赖按项目现有安装说明配置。全文和跨文生成验收、OCR 与长书的当前版本回归，以及公网与录屏交付仍属于后续工作。
