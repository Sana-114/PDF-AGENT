# 高级科研数据可视化验收（2026-09-17）

## 目标

在已有 CSV 字段审计、折线图、柱状图和三维雷达图基础上，补齐科研实验中常见的重复运行汇总能力：用户可显式选择 X/Y/分组字段，按类别生成分组均值和误差棒，并同时获得可核验统计摘要、Matplotlib 复现脚本与独立 SVG 文件。

该链路不调用 LLM。所有数值、误差棒和图注事实都由上传数据确定性计算，不生成因果解释或显著性结论。

## 统计口径

设同一 X 类别和同一序列下的有效观测为 `x_1 ... x_n`：

- 均值：`mean = sum(x_i) / n`；
- 样本标准差：分母使用 `n - 1`；
- 标准误：`SEM = sample_SD / sqrt(n)`；
- 95% CI 误差半径：`1.96 × SEM`，明确标注为正态近似。

缺失值和非数值单元格不进入统计分母，也不会自动补零。`n < 2` 时仍可显示均值，但不生成 SD、SEM 或 95% CI 误差棒，并向用户返回样本不足警告。当前 95% CI 没有使用小样本 t 临界值，因此不能把它描述为精确的小样本置信区间。

## 配置与输出

`POST /api/v1/visualizations/analyze` 在原有 `file` 和 `chart_type` 基础上接受：

- `x_column`：类别或连续 X 轴字段；
- `y_columns`：可重复提交，最多四个数值字段；
- `group_column`：可选分组字段；
- `aggregation`：`raw` 或 `mean`；
- `error_mode`：`none`、`std`、`sem` 或 `ci95`。

使用分组列或误差棒时，服务自动切换为均值聚合。响应中的每个序列均包含 `values`、`errors` 和 `sample_sizes`；`statistics` 给出基于全部已分析有效观测的 `count / mean / standard_deviation / minimum / maximum`。超过图表显示预算时只对绘制类别等距抽样，统计摘要仍基于全部已分析行。

Web 工作台提供列选择器、Y 指标多选、聚合和误差模式配置。SVG 预览按 `value ± error` 扩展纵轴边界，避免误差帽被裁切；悬停绘制点可查看值和有效样本量。下载的 SVG 内联必要样式，可脱离网页单独打开。复制的 Matplotlib 脚本使用同一组服务端聚合值和误差值，不在本地重新解释 CSV。

## 可复现用例

核心回归样本包含两个数据集、两个模型、每组两个 seed。以 D1/A 的 accuracy `0.80, 0.84` 为例：

- 均值为 `0.82`；
- 样本标准差为 `sqrt(0.0008) ≈ 0.0282842712`；
- 有效样本量为 `2`。

自动化测试同时覆盖显式 X/Y 选择、分组均值、样本标准差、API 多段表单参数、误差棒缺少 X 轴时的拒绝，以及既有缺失值、雷达图和编码行为。

验收命令：

```powershell
docker compose exec -T backend ruff check .
docker compose exec -T backend python -m pytest -q
cd frontend
npm run typecheck
npm test
$env:NEXT_TELEMETRY_DISABLED='1'; npm run build
```

本轮结果为后端 131 项测试通过且 Ruff 无问题，前端 14 项测试、TypeScript 检查和 Next.js 生产构建通过。

## 当前边界

- 当前只支持算术平均值聚合，不包含中位数、自助法置信区间或加权统计。
- 95% CI 使用正态近似，不进行分布检验，也不替代正式统计推断。
- 尚未提供 t 检验、ANOVA、效应量、多重比较校正或显著性标注。
- 尚未提供散点图、箱线图、热力图及图片格式的服务端导出；当前可下载 SVG，并可用生成脚本输出 300 DPI PNG。
- 三维雷达图用于多指标展示，不接受重复观测误差棒，且归一化数值不应与原始量纲混用。
