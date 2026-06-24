# LongMemEval Semantic Search on AWS MemoryDB

Search ~238k chat sessions from LongMemEval Medium using vector similarity on AWS MemoryDB. Three backend approaches benchmarked: **Pure Redis**, **Hybrid+S3**, and **Hybrid+EBS** (local disk).

## Configuration

All endpoints, IPs, bucket names, and SSH key paths are loaded from a config file — nothing is hardcoded.

```bash
cp config.example.yaml config.yaml
# edit config.yaml with your MemoryDB endpoint, S3 bucket, bastion IP, SSH key path, etc.
```

`config.yaml` is gitignored. Any setting can also be overridden via environment variable (see [config.example.yaml](config.example.yaml)).

For the AWS infrastructure itself:

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
# edit terraform.tfvars with your VPC / subnet IDs, S3 bucket name, EC2 key pair, etc.
cd terraform && terraform init && terraform apply
```

## Quick Start

```bash
# Start port-forward (uses values from config.yaml)
ssh -i $BASTION_SSH_KEY -f -N \
    -L 6379:$MEMORYDB_HOST:6379 \
    $BASTION_USER@$BASTION_IP

# Search pure Redis index
uv run python search.py -k 5 -q "recommend a good action movie"

# Search hybrid index (reads text from S3)
uv run python search_hybrid.py -k 5 -q "recommend a good action movie"
```

## Infrastructure

| Resource | Type | Notes |
|---|---|---|
| MemoryDB | `db.r7g.large`, Valkey 7.2, search-enabled | Two indexes: `idx` (pure, 7.78 GB), `idx_hybrid` (hybrid, 0.62 GB) |
| Bastion | `t4g.small`, public subnet | Runs FastAPI server + EBS disk storage |
| S3 | `<your-bucket>` (set in `config.yaml` / `terraform.tfvars`) | Text storage for hybrid approach |
| EBS | 10 GB gp3 at `/data` | Local text cache for disk approach |
| VPC Gateway Endpoint | `com.amazonaws.<region>.s3` | Same-region S3 access without NAT/Internet |

## Approaches

| Approach | Storage | MemoryDB | Latency p50 | QPS |
|---|---|---|---|---|
| **Pure Redis** | Text inline in MemoryDB | 7.78 GB (r7g.large, $245/mo) | 76.1ms | 10.5 |
| **Hybrid+S3** | Embeddings in Redis, text in S3 | 0.62 GB (r6g.large, $191/mo) | 141.0ms | 7.5 |
| **Hybrid+EBS** | Embeddings in Redis, text on local disk | 0.62 GB (r6g.large, $191/mo) | 77.7ms | 10.4 |

## API Server

A FastAPI server runs on the bastion (`http://<BASTION_IP>:<SERVER_PORT>` — both from `config.yaml`):

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/search` | POST | Hybrid search (Redis → S3) |
| `/search_pure` | POST | Pure Redis search (text inline) |
| `/search_disk` | POST | Hybrid search (Redis → local EBS disk) |

```bash
curl -X POST http://$BASTION_IP:$SERVER_PORT/search \
  -H "Content-Type: application/json" \
  -d '{"query": "recommend a good action movie", "k": 5}'
```

## Files

| File | Purpose |
|---|---|
| `config.py` / `config.example.yaml` | Central config loader (YAML + env-var overrides) |
| `search.py` | CLI semantic search (pure Redis via `idx`) |
| `search_hybrid.py` | CLI hybrid search (via `idx_hybrid` + S3/disk) |
| `server.py` | FastAPI server on bastion |
| `benchmark.py` | Pure Redis load test |
| `benchmark_hybrid.py` | Hybrid load test |
| `embed_and_load.py` | Embedding generation + Redis loader |
| `load_hybrid.py` | Migrate text from Redis to S3, create `idx_hybrid` |
| `terraform/main.tf` + `variables.tf` | Infrastructure as code |
| `BENCHMARK_RESULTS.md` | Full latency distributions and QPS |
| `ARCHITECTURE.md` | Mermaid sequence diagrams |
| `MEMORYDB_SETUP.md` | Cluster setup details |
| `EMBEDDING_GENERATION.md` | How embeddings were created |
| `CONNECT_TO_BASTION.md` | Connection instructions |
