# BGE 标准模式本地验收（2026-09-08）

## 验收环境

- GPU：NVIDIA GeForce RTX 4070 Laptop GPU，8188 MiB 显存
- 推理镜像：`ghcr.io/huggingface/text-embeddings-inference:89-1.9`
- 向量模型：`BAAI/bge-m3`，输出维度 1024
- 重排模型：`BAAI/bge-reranker-v2-m3`
- 检索链路：BGE-M3 dense embedding + Qdrant dense/sparse RRF + BGE cross-encoder rerank
- 模型运行时显存：约 4504 MiB，剩余约 3445 MiB

模型权重只保存在本机 Docker 命名卷中，不进入 Git 仓库或比赛提交包。

## 验收结果

数据集为 `backend/evals/local_chinese_thesis.json`，共 5 个带页码和原文锚点的中文论文问答用例。

| 指标 | BM25 基线 | BGE 标准模式 |
| --- | ---: | ---: |
| 用例通过率 | 100% | 100% |
| 证据召回率 | 100% | 100% |
| MRR | 0.7667 | 1.0000 |
| 锚点有效率 | 100% | 100% |
| P50 延迟 | 24 ms | 230 ms |
| P95 延迟 | 49 ms | 3002 ms |

BGE 评测的 5 个预期证据均位于第 1 名。P95 包含首次为文档生成并写入向量索引的冷启动开销；P50 更接近索引已存在时的查询延迟。本轮运行未出现向量服务降级警告，返回证据均完成 cross-encoder 重排。

这是一组工程回归用例，不等同于官方赛事数据集或大规模检索基准；官方 PDF 到位后仍需追加版本去重、扫描 OCR、541 页长文档和跨文献问答验收。

## 执行命令

```powershell
docker compose -f docker-compose.yml -f docker-compose.bge-gpu.yml --env-file .env.bge --profile local-bge up -d
docker compose --env-file .env.bge exec backend python scripts/check_bge_services.py
docker compose --env-file .env.bge exec backend python scripts/evaluate_retrieval.py evals/local_chinese_thesis.json --retriever configured --summary-only --output tmp/eval-bge-standard.json
```

评测明细写入被 Git 忽略的 `backend/tmp/eval-bge-standard.json`，避免将临时数据库标识和大段证据文本提交到仓库。
