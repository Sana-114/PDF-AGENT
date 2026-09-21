# 跨出版商 PDF 语料验收（2026-09-21）

## 目标

在比赛官方 PDF 尚未全部提供时，用可公开获取、排版差异明显的真实论文扩充回归语料，验证 `pymupdf-layout` 对长论文、单双栏、密集表格、算法公式、多面板图和不同出版商元数据的泛化能力。本轮不调用 LLM，也不把二进制 PDF 提交到 Git。

## 样本

| ID | 论文 | 来源版式 | 页数 | 文件大小 |
| --- | --- | --- | ---: | ---: |
| `gpt3-v4` | Language Models are Few-Shot Learners | arXiv，长论文与附录 | 75 | 6.45 MiB |
| `vit-v2` | An Image Is Worth 16x16 Words | ICLR/arXiv，小型大写标题、表格与图像 | 22 | 3.57 MiB |
| `resnet-cvpr-2016` | Deep Residual Learning for Image Recognition | CVPR/IEEE 双栏 | 9 | 0.58 MiB |
| `ddpm-neurips-2020` | Denoising Diffusion Probabilistic Models | NeurIPS，算法与密集公式 | 12 | 31.50 MiB |
| `scikit-learn-jmlr-2011` | Scikit-learn: Machine Learning in Python | JMLR，旧式 TeX 元数据 | 6 | 0.04 MiB |
| `nucleotide-transformer-nature-2025` | Nucleotide Transformer | Nature Methods，生物医学多面板图 | 20 | 4.47 MiB |

共 6 个文件、144 页、46.61 MiB。下载清单为每个新增文件保存固定 URL 与 SHA-256；脚本会在复用或下载文件时同时校验 `%PDF-` 文件头和哈希。Nature 出版商地址需要交互式授权 Cookie，因此清单保留官方地址，并使用公开大学课程镜像完成可复现下载。

## 自动化结果

在 PyMuPDF 1.26.4 环境运行：

```powershell
python scripts/evaluate_pdf_corpus.py tmp/stage58-corpus `
  --manifest tmp/stage58-manifest.json `
  --output tmp/stage58-report-final.json `
  --batch-pages 25 `
  --strict
```

结构化门槛覆盖页数、原生及抽取文本量、标题、作者、目录、表格、图像、公式和参考文献。最终结果为 **6/6 通过**：

| 文件 | 抽取字符 | 作者 | 目录节点 | 表格 | 图像 | 公式 | 参考文献 | 耗时 | Python 峰值内存 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `2005.14165v4.pdf` | 236,667 | 10 | 55 | 2 | 83 | 10 | 0 | 19.93 s | 7.88 MiB |
| `2010.11929v2.pdf` | 67,121 | 7 | 17 | 12 | 300 | 12 | 0 | 36.95 s | 16.33 MiB |
| `ddpm-neurips-2020.pdf` | 43,264 | 0 | 19 | 1 | 75 | 51 | 72 | 18.54 s | 102.62 MiB |
| `nucleotide-transformer-nature-2025.pdf` | 109,483 | 0 | 24 | 14 | 27 | 32 | 0 | 24.39 s | 14.13 MiB |
| `resnet-cvpr-2016.pdf` | 43,804 | 0 | 13 | 21 | 0 | 25 | 2 | 15.59 s | 8.64 MiB |
| `scikit-learn-jmlr-2011.pdf` | 15,151 | 0 | 8 | 0 | 0 | 0 | 0 | 1.04 s | 4.66 MiB |

GPT-3 的 75 页文档按 25 页生成 3 个检查点，其余文件各生成 1 个检查点。动态 JSON 报告保存在 `backend/tmp/stage58-report-final.json`，按仓库约定不提交。

## 本轮发现并修复的问题

首轮严格结果为 4/6。ViT 标题由于小型大写字体在多个 span 间切换，被错误拼成 `A N I MAGE IS W ORTH...`；JMLR 文件把构建产物名 `pedregosa11a.dvi` 写入 PDF title 元数据，解析器没有回退到首页真实标题。

修复后，同一行内的 PyMuPDF span 按其自带空格拼接，不再人为插入词间空格；`.dvi/.tex/.ps/.pdf` 等无空格构建文件名会被视为无效标题元数据并回退到首页版式候选。16 项解析器与语料工具定向测试及 149 项完整后端回归全部通过，Ruff 无告警；同一批 PDF 严格复测为 6/6。

## 人工版式复核

每篇均渲染首页和一个代表性中间页。已确认 GPT-3 参数表、ViT 模型表、ResNet 双栏图表、DDPM 算法与公式、JMLR 正文层级以及 Nature 多面板生物图完整可读，没有文件损坏或页面裁切。

## 已知边界

- 通过表示达到清单中的保守回归门槛，不等于所有结构均与人工金标完全一致。
- DDPM、Nature、ResNet 与 JMLR 的作者区尚未被通用启发式恢复；GPT-3、ViT、Nature 与 JMLR 的参考文献计数仍为 0。
- ResNet 页面包含曲线图但嵌入图像计数为 0，说明矢量绘图仍需独立图形区域检测；ViT 的 300 个图像节点也包含页面内小型图形资源，不能直接解释为 300 张语义图表。
- 这些缺口应作为后续作者/机构、References 和矢量图检测改进的真实回归样本；不得通过降低或伪造指标隐藏。
- 本轮全部为原生文本 PDF，不替代既有扫描 OCR 和 541 页长文档专项验收，也不冒充组委会正式测试集。
