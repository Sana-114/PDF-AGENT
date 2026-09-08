# 本地 BGE 推理编排

## 目标与边界

`local-bge` 是可选 Docker Compose Profile，用两个 Hugging Face Text Embeddings Inference（TEI）容器提供：

- `BAAI/bge-m3`：为 Chunk 与问题生成 1024 维 Dense Embedding；
- `BAAI/bge-reranker-v2-m3`：对 BM25/Qdrant 第一阶段召回的有限候选做 Cross-Encoder 重排。

普通 `docker compose up` 不会创建这两个容器，不下载模型，也不会改变默认 Hash Embedding 与关闭 Reranker 的行为。

## CPU 启动

```powershell
Copy-Item .env.bge.example .env.bge
docker compose --env-file .env.bge --profile local-bge up --build -d
docker compose --env-file .env.bge --profile local-bge ps
```

首次启动会从 Hugging Face 下载两个公开模型。缓存分别写入 `bge_embedding_cache` 和 `bge_reranker_cache` Docker 命名卷，容器重建后仍会保留。

TEI 对外端口默认为：

| 服务 | 宿主机地址 | Backend 内部地址 |
| --- | --- | --- |
| Embedding | `http://localhost:8001` | `http://bge-embedding` |
| Reranker | `http://localhost:8002` | `http://bge-reranker` |

宿主机端口冲突时可修改 `.env.bge` 中的 `BGE_EMBEDDING_PORT` 或 `BGE_RERANKER_PORT`；容器内部地址无需改变。

## GPU 启动

先确保 Docker Desktop 可用 NVIDIA GPU，再叠加 Override 文件：

```powershell
docker compose -f docker-compose.yml -f docker-compose.bge-gpu.yml `
  --env-file .env.bge --profile local-bge up --build -d
```

默认 GPU 镜像是官方通用 CUDA 标签。可根据显卡计算能力把 `.env.bge` 的 `TEI_GPU_IMAGE` 换成官方对应架构标签。

## 验证

等待两个服务状态均为 `healthy`：

```powershell
docker compose --env-file .env.bge --profile local-bge ps
```

再执行真实请求烟雾检查：

```powershell
docker compose --env-file .env.bge exec backend python scripts/check_bge_services.py `
  --embedding-url http://bge-embedding --reranker-url http://bge-reranker
```

正常输出示例：

```json
{"status":"ok","embedding_dimensions":1024,"reranker_top_index":0}
```

已有文献在切换模型后不需要重新上传。向量 Collection 按 Provider、模型和维度隔离；首次查询会按需为新模型回填索引。可使用已有离线评测器量化 BGE 相对 Hash/BM25 基线的收益：

```powershell
docker compose --env-file .env.bge exec backend python scripts/evaluate_retrieval.py `
  --dataset evals/local_chinese_thesis.json --retriever configured
```

## 常见问题

- 长时间处于 `starting`：首次下载或 CPU 加载较慢，查看 `docker compose --profile local-bge logs -f bge-embedding bge-reranker`。
- 下载失败：检查代理是否传递给 Docker Desktop；公开模型无需 `HF_TOKEN`。
- 请求超时：增大 `.env.bge` 中的 `EMBEDDING_TIMEOUT_SECONDS` 与 `RERANKER_TIMEOUT_SECONDS`，或减小 `TEI_MAX_CLIENT_BATCH_SIZE`。
- 内存不足：优先只启动其中一个模型服务进行诊断，或使用 GPU 模式；不要删除缓存卷，避免重新下载。
- 停止模型：运行 `docker compose --profile local-bge stop bge-embedding bge-reranker`，不会删除缓存。

## 官方参考

- [TEI Quick Tour](https://huggingface.co/docs/text-embeddings-inference/quick_tour)
- [TEI GitHub](https://github.com/huggingface/text-embeddings-inference)
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)
- [BAAI/bge-reranker-v2-m3](https://huggingface.co/BAAI/bge-reranker-v2-m3)
