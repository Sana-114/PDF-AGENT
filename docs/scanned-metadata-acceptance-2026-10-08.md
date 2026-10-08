# 第 73 阶段：扫描件作者与机构抽取修复

日期：2026-10-08

## 问题与修改

第 65 阶段扫描替代样本的机构列表为空。原逻辑找到大 OCR 块中带星号的姓名后直接跳过整个块，导致同块中的机构行丢失；单独最后一位作者还可能被邮箱行对齐规则过滤。编号机构预扫描也可能误将 `1lion@google. com` 当成机构。

本阶段按原始 OCR 行分别保留姓名和机构：

- 多栏合并块内逐行提取机构，重复机构去重，保留原始拼写；
- 单个带星号作者及其邮箱位于同一块时也走网格解析；
- 混合署名保留与带星号姓名处于同一源行高度的无星号姓名，不把下方邮政地址当作姓名；
- 邮箱、版权和编辑信息不进入机构列表；
- 在块内 `Abstract`／`Introduction` 边界停止，防止正文机构或姓名混入；
- 没有独立源行的合并文本仅提取明确姓名，不猜测机构的字符串边界；
- 解析器版本升级为 `layout73`，旧版本检查点失效并重新计算；已经入库的文献需要手动重新解析才能更新元数据。

扫描金标从“至少 6 个作者”升级为“至少 8 个、4 个指定姓名、3 家机构精确匹配”。**这不是全部姓名的精确金标。**

## 实际扫描样本结果

样本是公开 Transformer v1 自行栅格化的 `1706.03762v1_img.pdf`，不是组委会原始扫描文件。SHA-256：`4792e7260495afbe0ef84d70f7a1e563df4e7e31ef457672c861d6c10a1bd2aa`。

| 指标 | 本次结果 |
| --- | --- |
| 页数／原生文本 | 15 页／0 字符 |
| OCR | 15/15 页，36,624 字符 |
| 姓名条目 | 8；指定姓名检查通过，包含此前可能遗漏的 Illia Polosukhin |
| 机构精确列表 | Google Brain；Google Research；University of Toronto |
| 结构 | 25 个目录节点、1 个表格、5 个图区域、18 个公式候选、36 条参考文献 |
| 耗时 | 87.308 秒 |
| Python 分配内存峰值 | 49.09 MB；不包含所有原生 OCR 内存 |
| 严格门禁 | 1/1 文档通过 |

机器报告：`backend/tmp/stage73-scan-ocr.json`。本次首页渲染与源行复核确认三家机构确实位于作者区，邮箱不进入机构列表。

### 已发现但未修正的姓名错误

本次本地 OCR 将源姓名 `Lukasz Kaiser` 转录为 `Fukasz Kaiser`。单独英文 OCR 和局部裁剪重识别也未可靠消除该字符误识别。因此本阶段不能宣称八位作者全名均精确识别；全名审校仍须对照 PDF 原页。生产算法未引入预置作者名单替换。

本次使用 Python 3.12.14、PyMuPDF 1.26.4，并复用第 60 阶段的本地依赖和 Tesseract 数据；不同语言数据与运行环境会影响 OCR 输出，不能将本次耗时直接与旧 Linux 容器成绩作性能对比。

语言数据哈希：

- `eng.traineddata`：`8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba`；
- `chi_sim.traineddata`：`a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730`。

## 回归结果

- 后端全部 222 项单元测试通过；本阶段新增 6 项扫描元数据边界测试。
- 后端全量 Ruff 检查通过。
- 六篇、144 页固定 SHA-256 原生论文严格验收 6/6 通过：GPT-3、ViT、DDPM、Nucleotide Transformer、ResNet、Scikit-learn。验收包括既有作者／机构金标、参考文献和结构检查。
- 原生论文报告：`backend/tmp/stage73-native-corpus.json`。
- 本阶段所有验证在本地完成，没有调用 LLM。

运行时 PDF、语言数据、图片和 JSON 均位于忽略目录，不上传 Git。

## 复现

准备仅含扫描替代样本的目录后，在安装了 `backend/requirements-dev.txt` 及中英文 Tesseract 语言数据的环境运行：

```powershell
Push-Location backend
python -m pytest
ruff check app tests scripts
python scripts/evaluate_pdf_corpus.py tmp/stage73-scan-corpus --manifest ../docs/pdf-regression-corpus.json --output tmp/stage73-scan-ocr.json --strict
Pop-Location
```

Windows 如无系统 Tesseract 数据路径，应将 `TESSDATA_PREFIX` 和 `OCR_TESSDATA` 指向本地语言数据目录。生产容器已包含语言数据。新代码需重新构建后端／Worker，旧文献需点击重新解析；上述本地验收不代表运行中的容器已自动更新。
