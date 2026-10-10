# 第 93 阶段：首页作者区的源图补充 OCR

## 机制

只对扫描首页、包含多个有标记姓名和邮箱的紧凑作者区增加一次 300 DPI 局部 OCR。区域由原 OCR 行坐标确定，排除旋转侧栏；原生 PDF、正文与后续页面不进入补充流程。不从文件名、邮箱、其他版本或测试金标修正姓名。

补充识别使用 `eng+pol`，覆盖英语及波兰语字母。候选必须保留作者数量、顺序和已有机构，逐位姓名与原识别接近，作者标记数量不减；语言包缺失、识别错误或名单不兼容时保留原结果。该规则只能控制替换范围，不能保证任意姓名语义正确。

裁切结果的行／块坐标映射回原页；`processing_metadata.front_matter_refinements` 保存原识别、补充识别、区域、分辨率及语言，也随解析检查点恢复。升级解析版本，并将 OCR 语言及 DPI 配置纳入版本指纹，避免混用旧检查点。语言包文件更换后应显式重新解析，不仅恢复旧检查点。

默认 `OCR_FRONT_MATTER_LANGUAGES=eng+pol`，可设为空禁用。Docker 镜像增加约 4.6 MiB 的 `tesseract-ocr-pol` 语言数据，不提交权重。本地诊断包来自官方 `tessdata_fast/4.1.0`，SHA-256 `c4476cdbc0e33d898d32345122b7be1cbf85ace15f920f06c7714756e1ef79b2`；实测容器同一 Polish 哈希，English 包与主机不同，所以另做了真实容器解析。模型系列说明见 [Tesseract 官方文档](https://tesseract-ocr.github.io/tessdoc/Data-Files.html)。

## 官方原件复核

输入仍为官方 `1706.03762v1_img.pdf`，SHA-256 `64094fe169dd4ff454f579e9f197298df1c118c24b71b1f1ca8bb218c9e4801d`。不修改 PDF，不降低原精确姓名金标。

| 检查 | 本机 | 更新后的真实容器 |
| --- | --- | --- |
| 解析页数 | 15 | 15 |
| 全部有序作者与原金标相同 | 8/8 | 8/8 |
| `Łukasz Kaiser` 原字形 | 通过 | 通过 |
| 表格候选 | 4 | 4 |
| 指定 BLEU 行值 28.4／26.36 | 2/2 | 2/2 |
| 总耗时 | 152.015 秒 | 106.430 秒 |

原始记录 `backend/tmp/stage93-ocr/official-full-crop.json` 与 `official-container.json` 不覆盖；前一阶段失败报告仍保留。两次均为本地 OCR，零 LLM 调用。规则化结构及精确姓名子集通过；本次没有测量 Python 内存峰值，不直接作为第 87 阶段四组全量核心门禁的替代成绩。

**复杂表格的列／小数点／公式错误仍存在**，表 4 个别值如 `933` 仍需核对原页；表格与参考文献数量不是准确率。补充 OCR 没有解决这些问题。

## 回归与运行状态

后端 365 项、前端 29 项、Ruff 与 TypeScript 通过；新增 8 项覆盖名单变更、顺序、机构保留、区域范围、源页坐标、缺包回退、检查点及配置指纹。官方原生 v1/v7 两篇重新解析均通过，仍各有 4 表；首次原生回归遇到 Windows 沙箱临时目录权限失败，失败报告保留，沙箱外同样语料 2/2 通过。

后端、Worker、前端已重新构建及启动，三个容器健康；`http://localhost:3300/`、API、两项 BGE 健康端点返回 200。不改文献库、数据卷、密钥或公网配置；旧已入库扫描 AST 不自动迁移，需要在明确文献范围内重新解析。新增作者块的向量索引也需明确范围更新，不能将词法检索通过当作新向量链路通过。

复现（先安装相应 OCR 语言数据）：

```powershell
python backend/scripts/evaluate_scanned_recovery.py `
  --pdf 科研小助手/pdf/1706.03762v1_img.pdf `
  --manifest docs/official-pdf-manifest.json `
  --output backend/tmp/stage93-ocr/new-full-report.json
```

已有报告拒绝覆盖。`inspect_ocr_front_matter.py` 为 SHA 校验后的源图局部识别对照，不自动修改库中的解析结果。
