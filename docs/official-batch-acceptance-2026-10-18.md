# 联网、调度、界面与生成问答批量验收

日期：2026-10-18。本轮按用户要求集中推进多类验收，不以单个小功能为终点。原失败全部保留；没有把不同构建拼成同一轮满分，也没有重新宣称官方39份所有功能通过。

## 1. 本批结果

| 验收组 | 实际结果 | 范围与限制 |
| --- | --- | --- |
| 真实联网检索／导入／引用下钻／推荐 | 4/4通过 | 正确ViT题名、官方LoRA v2下载解析与重复去重、Transformer引用下钻到Adam、真实arXiv刷新及反馈；不是公网验收 |
| 独立Redis＋Worker＋Beat | 7/7通过 | 真正外部进程、当前app只读挂载、非eager、独立DB14及UUID队列；Beat加速到15秒，仅一次定时派发，不是默认6小时或长时间稳定性 |
| 推荐浏览器 | 9/9通过 | 真实Edge和HTTP API；点赞→中立→踩、重载持久化、真实刷新、创建／删除追踪、错误恢复及390px布局；该UI副本使用eager任务，外部调度由上一组独立证明 |
| GitHub仓库检索 | 1/1通过 | 一次真实搜索找到原文中的microsoft/LoRA；不是代码可运行或全体仓库官方性证明 |
| BLOOM贡献问答 | 最后8/8核心与原页确认 | 真实BGE查询、TEI及DeepSeek，含团体署名、跨页分组、姓名补全、缺机构拒答；不是全量姓名或39文件问答准确率 |
| 既有固定跨论文／反例四题 | 首轮2/4，修复轮3/4 | DPO正向、LoRA/QLoRA分项、随机种子拒答核心确认；DPO边界反例仍有最终发布漏项，不放行该组完整性或稳定性 |
| 最终离线回归 | 后端1,413／前端42 | 类型与相关Ruff通过；较上一轮增加27项回归，不是科研准确率 |

最后后端app SHA：`563912114119b8c670289c3ac12643c65f2588c83af690d1c4fea1699eb9d2f7`（133个Python文件）。联网／外部调度发生在本批较早的订阅修复构建，贡献问答发生在中间构建，最后跨论文发生在上面这一构建；逐轮指纹见机器摘要，不冒充所有场景的最终同版完整复验。

官方基础39份／1,986页覆盖沿用历史。本批问答使用6份官方公开论文的已核验AST；下载LoRA v2后实际重新解析，其他不是新全库解析。未重新测试扫描OCR或541页教材资源门禁。

## 2. 发现并修复的实际问题

### 订阅写入与版本

新增边界测试首轮9项中8项失败，揭示同批重复条目触发唯一约束、旧版本覆盖新版本、失败事务未rollback、空feed仍调用Embedding，以及空白名称被接受。新增批量任务反例后共10项韧性检查。

修复：同一feed按arXiv工作ID去重并选择最高明确版本，不混拼版本元数据；较旧条目不覆盖已有较新推荐或反馈。写入失败先整体回滚，再记录不含SQL参数的错误；后续显式刷新可恢复，旧成功时间／数据不伪更新。空feed成功但不调用向量模型、不伪造排序来源。空白名称拒绝；一个后台任务异常不能使后续活跃订阅饥饿。失败不自动重试。

这是推荐元数据版本保护，不等同于重新完成PDF正文版本差异或语义去重验收。

### 页码与姓名关系

首轮8道贡献问答机器5/8；独立原页复核发现姓名补全误判，核心实际4/8：答案提到目标姓名，却把完整姓名字段赋给了后面另一人。另3题找到了正确来源，但“第2页／第3页”被数字防护当作未经证明的科研数值误拦。

修复：只有与**该声明所引证据的物理页**一致的显式页码定位符可在数值审计视图中豁免；同值GPU数量、层数、错误页码仍需原文支持。英文页码的句末句号也经过反例检查。姓名补全仅从真实贡献原文的逗号边界提取候选，不嵌入评测答案，且拒绝把下一人的姓名赋给目标；歧义时不强行提示。

修复后同8题机器8/8，但额外排序说明出现“唯一例外”范围过宽，原答继续保留。随后缩小贡献名单回答范围，最后同8题核心与原页8/8。仍会出现有原文支持但未被问到的排序／致谢补充，短答范围服从性未宣称完美；姓名题本身带有Rush提示，是关系回归，不是盲测姓名发现。

### 跨论文边界与机制动作

第一组固定四题原机检2/4。补充窄范围保护：不把明确否定“模型参数有闭式解”视为肯定；有直接微调边界时允许同源受限短语的引述复述，仍拒绝扩展到整个流程；识别“基于分位数量化”这种通用动作表达，不把“精度更好”单独算机制解释。61项定向回归通过。

最后固定四题3/4。剩余DPO反例：草稿确实写出微调／语言模型采样边界，但“原文并未称……”以及免责引述经拆句后仍被误读，最终正确限定被删，且幸存部分没有充分标记为不完整。**这是发布检查与完整性问题，不能归咎为BGE找不到原文，也不通过反复生成追绿。** 本批至此停止追加付费复测，下一步应修否定命题／指代边界与漏项标记，再做固定反例及有效前提对照。

### 界面错误恢复

反馈保存成功后旧错误提示未清除，已在新请求前清理。最终浏览器明确检查503错误可见、失败不伪改状态、手动恢复后旧错误消失。重复跑UI脚本时先通过真实按钮将测试副本状态归为中立，不假设上一轮没有持久反馈；初次前置状态错误保留。

## 3. 原文、调用与环境边界

- PDF流程人工对照完整原页：BLOOM第1–3、43–45页；DPO第1、2、16页；InstructGPT第6页；LoRA第2页；QLoRA第4–6页。结合原文件SHA、AST、块号、BBox和声明的实际引用，不只查关键词。部分Poppler页出现Symbol／ArialUnicode字体回退告警，不宣称所有字形通过。
- 新建BLOOM424块及比较集1,237块的隔离向量。后续两轮BLOOM只读复用前者，先逐点核对全部424个payload与SQL／当前AST，仍做新的BGE查询和TEI重排，不读旧答案进提示。
- 本批DeepSeek **36次实际请求**：贡献题3轮各8次，固定四题2轮各6次（每轮4主生成＋2比较校审）。它们不是36道独立题，原始草稿／校审／错误均保存。模型自动重试0次。
- 联网首轮订阅3次元数据／3次BGE；完整联网9次元数据／2次下载／3次BGE；外部Worker日志确认3次arXiv／3次BGE；GitHub1次搜索。另两轮成功UI流程各有2次真实刷新。模型调用前的工具准备失败为0次付费调用，不记为论文问答失败。
- 本批有界请求未观测到BGE哈希降级；结束时两个BGE健康、重启计数0、OOM=false。历史连接重置根因仍未证实，不将本批成功解释成长时间稳定性通过。
- Redis测试前DB14为空，专用UUID键前缀／队列；从未执行FLUSHDB。仅移除本轮带身份标签的Worker／Beat容器和自身测试键，日志保留。8039／3239临时服务已停止；Next临时类型路径撤回。正式容器、既有3300界面、正式数据库及正式向量集合未替换。

## 4. 证据与复现

原始目录：`backend/tmp/official-batch-acceptance-2026-10-18/`。官方PDF、大AST、响应和截图忽略于Git，只提交脚本及[公共SHA摘要](evidence/official-batch-acceptance-2026-10-18.json)。主要记录：

- `live-baseline/`、`live-full-after-fix/`：真实外部元数据／下载／BGE调用；不是回放。
- `external-worker-first/`：挂载路径导致指纹工具失败，业务尚未执行；`external-worker-second/`：真实Worker／Beat、事件、日志及清理证明。
- `resilience-baseline.xml`：8个基础问题的原失败；`resilience-fixed-2.xml`：修复定向确认。另一次命令包含不存在测试路径，0用例执行，报告单独保留。
- `grouped-qa-first/`：测试SQL未提交，0模型调用；`grouped-qa-after-fix/`：复用工具误把Embedding前缀当载荷原文，0模型调用。二者不是产品召回失败。
- `grouped-qa-second/`、`grouped-qa-after-fix-2/`、`grouped-qa-scoped/`：三轮实际8题，保留原始机检及全部模型请求。
- `comparison-batch/`、`comparison-batch-after-fix/`：固定四题原2/4和最后3/4；不改原失败。
- `radar-browser-first/`、`radar-browser-final/`、`radar-browser-final-2/`：UI初始通过、持久状态前置错误、最后9项确认，注入503单独计数。
- `manual-source-review.json`：独立核心复核及保留项；`backend-release.xml`、`frontend-final.xml`：最终回归。

项目内复现入口：`verify_official_live_discovery.py`、`verify_official_subscription_worker.py`、`verify_official_grouped_qa.py`、`verify_official_comparison_batch.py`（均在`backend/scripts/`），及`frontend/e2e/recommendationFlow.mjs`。需已安装依赖、本机BGE／TEI／Qdrant、合法DeepSeek配置及官方已核验fixture；输出必须使用新的专用`backend/tmp`子目录。

```powershell
python backend/scripts/verify_official_subscription_worker.py --output-dir backend/tmp/new-subscription-run
python backend/scripts/verify_official_grouped_qa.py --fixture backend/tmp/official-grouped-reader-2026-10-18/fixture-release --output-dir backend/tmp/new-grouped-qa --allow-cloud
python backend/scripts/verify_official_comparison_batch.py --fixture backend/tmp/official-review-stability-2026-10-16/runtime-fixture-release --output-dir backend/tmp/new-comparison --allow-cloud
```

Worker入口要求已有`pdfagent-backend:latest`镜像及`pdfagent_default`网络、空测试Redis DB14；不会拉镜像或清空既有数据。只读挂载当前app而非声称镜像已更新。浏览器脚本固定专用3239／8039，并拒绝非推荐API的修改、生成或论文导入。

## 5. 下一批与交付门禁

优先修DPO反例最终发布漏项／否定范围和部分回答状态；保持这四道题及反例不变验证，不回到反复测GPT。随后才做同版有限稳定性和完整综述／写作／Future Work科学质量收口。

还包括旧正式库安全重解析／向量迁移、复杂结构／资源边界、长期调度／真正新论文增量、多用户隔离、引用含金量的独立科学评价，以及最终镜像安装、技术PDF、正式MP4、公网、体验账号、密钥轮换和正式提交。本轮关闭的是限定功能门禁，不是整体验收。按用户要求继续不创建分支、不提交／推送Git。
