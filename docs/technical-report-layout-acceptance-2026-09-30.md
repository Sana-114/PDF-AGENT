# 第 71 阶段：技术文档排版草稿验收

日期：2026-09-30

## 产物

- 内容源：`docs/competition-technical-report-draft.md`；
- 构建脚本：`scripts/build_technical_report.py`；
- 文档依赖：`requirements-docs.txt`；
- 排版草稿：`output/pdf/PaperPilot-technical-report-draft.pdf`。

## 当前排版

- A4，17 页，未超过比赛规定的 30 页；
- 封面、目录、页眉、页脚和连续页码已建立；
- 系统架构与核心数据流使用 PDF 矢量图，不依赖位图截图；
- 表格根据内容动态分配列宽，长文件名和中英文混排可换行；
- 五处真实界面截图位置已经规划，并明确标注提交前替换；
- 当前 17 页是无正式截图的排版骨架，插入截图后预计为 22–26 页。

## 复现

```powershell
python -m pip install -r requirements-docs.txt
python scripts/build_technical_report.py
```

Windows 优先使用微软雅黑字体；缺失时回退到 ReportLab 的 `STSong-Light`。构建后脚本使用 pypdf 检查页数，若草稿超出 30 页会直接失败。

## 验收结果

- `pdfinfo`：A4、17 页、无加密、无 JavaScript；
- `pdfplumber`：17 页均有有效文本，可提取标题与参考资料；
- Poppler 以 110 DPI 渲染全部页面成功；
- 人工检查封面、目录、两张矢量图、全部表格、截图规划框和章节边界；
- 未发现文字裁切、重叠、乱码、黑块或单行孤立空页。

## 最终版待办

1. 填写团队、最终 Tag、Commit SHA、在线地址和服务器配置；
2. 用公网正式版本截图替换五处截图规划；
3. 官方 PDF 复验后更新测试结果；
4. 再次执行全页渲染检查，并确认最终页数不超过 30 页。
