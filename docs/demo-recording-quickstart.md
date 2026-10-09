# 本地录制操作卡

2026-10-09。配合 `docs/demo-video-script.md` 使用；这里提供可执行镜头，不提供冒充模型输出的预写答案。本机前端为 `http://localhost:3300/`，其他机器以环境配置为准。

## 开始前

1. 启动 Docker Desktop，在仓库根目录运行 `pwsh -File scripts/start.ps1 -Mode standard -Gpu`；无 NVIDIA GPU 时去掉 `-Gpu`。等待依赖、API、Worker、前端和两个 BGE 服务健康。
2. 打开本地页面，进入“文献问答”→“多文对比”。选择下方对应论文，保留当前每篇 5 条证据的设置。
3. 确认模型配置为 `deepseek-flash`。配置标签不能替代当次调用成功；Key、终端历史和个人信息不入镜。
4. 问题复制自 v6 金标数据集。英文问题可配普通话解说；若改成中文或另加条件，需核对当次输出，不再称为同一固定测试。
5. 首次打开原页必须等到真实文字绘制完成。遇到检索降级、请求失败或答案缺项暂停该镜头，不隐藏错误或替换响应。

## 镜头 A：LayerNorm 对比和两个原页（优先）

选择 `1706.03762v1.pdf` 和 `gpt2-openai-2019.pdf`，复制：

```text
Compare the exact layer-normalization placement in the original Transformer and GPT-2. Contrast the original Transformer's LayerNorm(x + Sublayer(x)) arrangement with GPT-2 moving layer normalization to the input of each sub-block. Publish the contrast only if one claim cites both original papers.
```

操作：点击“开始证据对比”，找到引用两篇文献的同一声明；分别打开 Transformer 第 2 页和 GPT-2 第 4 页，等页图绘制后展示高亮，再关闭阅读器返回比较。

通过判据：区别有原文支持；同一声明带两个来源；两页显示真实内容而非白色 Canvas。高亮是检索块范围，可能覆盖不止一句话，不称作精准逐词对齐。本镜头已完成新来源门禁和两页绘制检查。

## 镜头 B：GPT 演化（补充）

选择 `gpt1-openai-2018.pdf`、`gpt2-openai-2019.pdf` 和 `2005.14165v4.pdf`，复制：

```text
Compare GPT-1, GPT-2 and GPT-3 using each original paper: decoder architecture, parameter counts, GPT-1's explicit training sequence length, GPT-2/3 context window lengths, and layer-normalization placement. Do not equate a training sequence length with an unstated context-window setting. State explicit values with citations; do not infer values absent from the cited passages.
```

操作：展示已发布事实及来源卡，重点指出 GPT-1 第 5 页是 512-token **训练序列**；GPT-2 第 4 页有 1024 与模型规模；GPT-3 175B 的指定来源页为第 5／8 页，2048 为第 8／43 页，预归一化为第 8 页。分别回到对应来源，不将不同定义合为一项。精确页码与金标以 `backend/evals/real_pdf_comparison_grounded.json` 为准。

本轮七案例事实检查已通过；该镜头的 PDF 内容按新增像素规则尚未专项重跑，正式录制前目视确认，不能凭旧高亮截图认定页图正确。

## 镜头 C：证据不足时拒答

选择 `1810.04805v2.pdf` 和 `gpt1-openai-2018.pdf`，复制：

```text
Compare the exact GPU model and exact GPU count used for the original BERT pre-training and the original GPT-1 pre-training. If both original papers do not provide those exact GPU facts, explicitly decline the comparison without converting TPU information or outside knowledge into GPU claims.
```

通过判据：界面标记“证据不足”，不输出无来源 GPU 型号或数量，也不把 TPU、层数或头数当成 GPU 信息。这表示当前证据不支持请求，不等于已经证明全文绝无相关描述。

## 其他镜头的状态

| 镜头 | 当前可录内容 | 录制前仍需准备 |
| --- | --- | --- |
| 文献库与图谱 | 已保存真实页面与局部图 | 库中不是二十篇图谱专项，不外推影响力 |
| v7→v1 版本提醒 | 历史机器验收记录 | 当前库未预装 v7；准备独立演示数据，不删除现有用户数据 |
| 541 页教材 | 历史恢复验收结果 | 当前库未预装；需要页内镜头时先导入并等待完成 |
| Future Work | 原文整理、待核验持久化 | 科学判断未核对时不录“已解决” |
| CSV／四种架构脚本 | 已核验确定性工具 | 明示 CSV 是合成演示数据 |
| 翻译、生成式综述、写作 | 实现已存在 | 分别审校实际结果；本次七案例比较不覆盖这些质量 |
| 公网与评审门禁 | 配置与部署说明 | 临近提交购置服务器／域名、外网验收后补录 |

## 成绩页口径

- 最新离线回归：后端 284 项、前端 26 项。
- 原 v5 三轮回答按 v6 重新计分：21/21、事实 51/51、拒答 6/6；**没有新增三轮调用**。
- 新真实浏览器一轮：7/7、事实 17/17、拒答 2/2，混合召回与重排通过。
- LayerNorm 一组另行复查：两张原页已绘制；不算第二轮完整七案例。

保存当次真实录制，剪去等待时标“处理完成后”。视频成片仍需 5-8 分钟、1080p、普通话及完整播放检查；操作卡和脚本不替代 MP4。
