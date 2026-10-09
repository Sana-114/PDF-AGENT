# 第 81 阶段：召回来源与真实浏览器闭环

日期：2026-10-09。后端运行代码 `713fc94`，前端沿用现有生产界面；评测集 `real-pdf-bge-deepseek-comparison-2026-10-v6`。

## 本阶段修复

- EvidenceAnchor 增加 `recall_mode`，保留第一阶段的 `hybrid`／`lexical`／`vector` 来源，不再被 `retrieval_mode=reranked` 覆盖。
- 多文取证记录各个查询的召回模式；标准模式中任一查询只获得词法召回时，显示警告并将检索 Trace 标记为 `degraded`。
- 新真实后端评测和浏览器门禁同时检查混合召回、重排和中间查询降级，不能把 BM25 候选经重排算成完整 BGE-M3 成功。
- 浏览器原页验证增加真实 Canvas 像素和文本层检查。坐标覆盖层出现不代表页图已绘制；今后的录制烟测也等待真实内容。
- 新截图目录按报告文件名隔离，防止定向复查覆盖整轮截图。

原报告没有召回字段，不能事后补填 `hybrid`。第 80 阶段同一批三轮回答的 v6 重新计分仍仅证明内容门禁，不能升级为三轮严格召回来源验收。那次连接中断的确切原因未定；只读检查显示 Qdrant 和 BGE Embedding 未重启、未标记 OOM，不能因此宣称网络故障已根治。

## 真实浏览器结果

实际调用七篇公开论文摘录和固定问题，没有替换 API 响应或注入静态答案。Edge 1920×1080，一轮七案例全部通过。

| 验证项 | 成绩与范围 |
| --- | --- |
| 正向问答与拒答 | 7/7；正向 5 组，拒答 2 组 |
| 必需原文事实 | 17/17，指定来源与页码 |
| 检索 | 返回证据均为 `hybrid` 召回＋`reranked` 重排，中间查询无降级标记 |
| 展示 | 页面答案与实际 API 一致，声明数量和引用按钮一致 |
| 引用跳转 | 五组正向问题打开实际 PDF 页码和坐标覆盖层 |
| 页面异常 | 0 |
| 重复上传 | BERT 原文件返回完全重复；不是 v7→v1 版本测试 |

初次整轮截图有尚未绘制完成的白色 Canvas，故不能用该轮截图证明所有 PDF 页已绘制。只对 LayerNorm 对比重新进行一次定向真实调用，新增像素检查通过：Transformer 第 2 页、GPT-2 第 4 页均已绘制、有文本层及高亮；该次仍通过三项事实与双来源要求。其他四组正向页图没有按新增像素规则重新调用，不冒称全七组严格页图复验。

保存的机器报告：

- `backend/tmp/stage81-browser/browser-2026-10-09.json`：七案例问答与召回来源。
- `backend/tmp/stage81-browser/painted-reader-2026-10-09.json`：仅 LayerNorm 定向页图复查，不能计为另一轮完整七案例。

这两个历史报告的截图在同目录 `screenshots/`，LayerNorm 文件为后一次定向调用；新脚本改为按报告名生成子目录。此次定向原页截图另存于 `docs/assets/recording/07-layernorm-transformer-source.png` 和 `08-layernorm-gpt2-source.png`。公开论文权利归作者，不作为项目原创内容。

## 本地回归与复现

后端 284 项离线测试、Ruff；前端 26 项测试及类型检查通过。检索来源负例覆盖：只有词法召回、缺少来源字段、中间查询降级、重排失败保留混合来源。

复用已保存且通过的后端 v6 报告，可以只执行浏览器：

```powershell
node frontend/e2e/realPdfComparison.mjs --url http://localhost:3300 --backend-report backend/tmp/stage80-regraded/v6-round-3.json --output backend/tmp/new-browser/report.json
```

该命令会真实调用配置的 DeepSeek，请仅在允许公开摘录外发时使用。`--case-id transformer-vs-gpt2-layernorm-conflict` 仅检查一组，不计完整轮次。当前一轮严格浏览器来源验收不能代替上线环境的连续稳定性检查或开放域语义审校；公网、正式视频与官方原始扫描件仍待完成。
