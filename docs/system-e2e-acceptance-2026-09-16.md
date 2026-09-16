# 系统级 E2E 验收（2026-09-16）

## 目标

本轮不再只调用解析器函数，而是从公开 HTTP 接口进入真实 Compose 部署，覆盖 Frontend、Backend、PostgreSQL、Redis、Celery Worker 与 Qdrant。测试顺序与比赛用例一致：先上传 Transformer v7，再上传 v1，并确认系统建议保留更新版本。

## 复现

前置语料：

- `output/pdf/regression-corpus/1706.03762v7.pdf`
- `output/pdf/regression-corpus/1706.03762v1.pdf`

运行：

```powershell
.\scripts\run_system_e2e.ps1 -TimeoutSeconds 360
```

脚本从 4300 起自动寻找 200 个候选端口中的首个可绑定端口，并同步设置本轮 Compose 的 `FRONTEND_HOST_PORT` 与 `FRONTEND_ORIGIN`。默认删除本轮创建的数据；传入 `-KeepDocuments` 可供人工检查。动态报告和截图写入 `backend/tmp/e2e/`，由 `.gitignore` 排除。

## 验收断言

评测器的 15 个布尔检查全部通过：

1. Backend 健康检查返回 `ok`；
2. Frontend 返回 HTTP 200；
3. Frontend HTML 包含 PaperPilot 应用壳；
4. v7 上传未被历史精确哈希短路；
5. v7 最终进入 `ready`；
6. 从文件名正确识别随机 arXiv ID 与 v7；
7. 解析进度达到 100%，完成页数等于文献页数；
8. Document AST 达到页、表格、公式与参考文献最低阈值；
9. 阅读器目录端点返回条目；
10. References 端点同时返回条目与正文提及；
11. PDF 文件端点正确处理 `Range: bytes=0-63`，返回 206、64 字节及 `%PDF-`；
12. v1 上传同样未被精确哈希短路；
13. v1 产生指向本轮 v7 的语义重复预警；
14. 建议文本明确包含 v7 并建议保留库中较新版本；
15. 版本差异端点返回正确文献对，正文重合度大于 0.5。

Windows 主脚本另外执行两项浏览器检查：Edge DOM 必须包含本轮随机生成的 arXiv ID，以排除误命中搜索框示例文本；独立 Edge Profile 必须成功写出 1440×1200 PNG 截图。没有安装 Edge 时，API 工作流仍运行，但脚本会明确警告浏览器检查已跳过。

## 实测结果

最终严格运行状态为 `passed`，API 工作流耗时 10.943 秒。v7 结构化结果为：

| 指标 | 数值 |
| --- | ---: |
| 页数 | 15 |
| 表格 | 4 |
| 图区域 | 3 |
| 公式 | 11 |
| 参考文献 | 40 |
| 正文引用提及 | 38 |
| v1/v7 正文重合度 | 0.6203 |

Edge DOM 找到了该轮唯一测试 ID `9900.00590`，截图成功写出 122,676 字节。两条测试文献在脚本 `finally` 阶段删除；浏览器开发 Profile 与动态结果不进入版本控制。

## 边界

- 该脚本验证真实数据流与浏览器渲染结果，但不模拟鼠标拖拽、文件选择器或每个工作台按钮；这些交互仍需演示视频和人工验收。
- 测试上传会在 PDF 末尾追加合法注释以获得唯一 SHA-256，因此能够重复运行，但页面内容和解析结构保持不变。
- 公开仓库不保存两份论文 PDF；须先运行 `scripts/fetch_pdf_corpus.ps1 -PaperIds transformer-v1,transformer-v7` 或放入组委会文件。
- 结构阈值针对当前官方 Transformer 矢量样本。扫描版和 541 页书籍分别由已有 OCR 与长文档评测器验收。
