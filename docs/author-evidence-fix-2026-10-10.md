# 第 89 阶段：有序作者与机构证据

针对官方第 6 作者问题，新增基于解析 AST 的作者证据块：列出完整排版顺序，并附原始姓名、机构及作者脚注文字。每个姓名必须在前 3 页中的同一页找到真实块；缺失任一姓名时不生成序号列表，不从文件名或邮箱猜机构。序号明确标记为结构化解析结果，不冒充原文编号。

作者问题在 BM25 中优先匹配该块，后续仍可进入混合召回及重排。旧文献只追加缺失的作者块，保留已有 Chunk ID；追加操作幂等，不删除用户论文。不自动重建全库向量，新增块的向量索引需在明确范围内更新。

官方 Transformer v1 的实际解析与词法检索通过：8 位作者完整、有序，第 6 位及机构出现在返回证据，首页块 ID 和 bbox 保留；113 个块，零 LLM 调用、临时数据库。报告为 `backend/tmp/stage89-authors/official-v1.json`。这不等于已经通过新一轮 BGE＋DeepSeek 生成验收。

后端全量 330 项通过；4 项新增测试覆盖顺序、缺失姓名拒绝、机构不乱配和稳定 ID／幂等补充。Ruff 通过。

```powershell
python backend/scripts/evaluate_author_evidence.py `
  --pdf 科研小助手/pdf/1706.03762v1.pdf `
  --expected-sha 0a9bff1be3575cf9fe26a4a3d3a9175bc791ecec90de8b9deba4cff75d2b9476 `
  --ordinal 6 --expected-author 'Aidan N. Gomez' `
  --expected-affiliation 'University of Toronto' `
  --output backend/tmp/stage89-authors/new-run.json
```

金标姓名只存在测试命令中，生产代码没有 Transformer 特例。首轮原题 4/9 的成绩不改写；真实生成仍待新授权复验。
