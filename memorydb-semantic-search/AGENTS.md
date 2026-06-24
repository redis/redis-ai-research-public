# AGENTS.md — LongMemEval Semantic Search on AWS MemoryDB

## Project Goal
Search ~238k chat sessions from LongMemEval Medium dataset using vector similarity, comparing three backends: **Pure Redis** (text inline), **Hybrid+S3** (text in S3), **Hybrid+EBS** (text on local disk).

## Key Findings
- Pure Redis: 76.1ms p50, 10.5 QPS, 7.78 GB MemoryDB ($245/mo)
- Hybrid+EBS: 77.7ms p50, 10.4 QPS, 0.62 GB MemoryDB + 10GB gp3 ($191/mo) — **best tradeoff**
- Hybrid+S3: 141.0ms p50, 7.5 QPS, 0.62 GB MemoryDB + S3 ($191/mo) — high tail latency
- Encode bottleneck (~70ms on CPU, GIL-locked) dominates all three.
- Full distribution and QPS tables in `BENCHMARK_RESULTS.md`.

## Infrastructure (Terraform)
- **Directory:** `terraform/` — `main.tf` + `outputs.tf`
- **MemoryDB:** `db.r7g.large`, Valkey 7.2, search-enabled (`default.memorydb-valkey7.search`), single shard, TLS, `open-access` ACL
- **Bastion:** `t4g.small` in public subnet, EC2 key pair from `var.key_pair_name`, IAM role for S3, port 8080 open
- **S3 bucket:** from `var.s3_bucket_name`, VPC Gateway Endpoint
- **EBS:** 10 GB gp3 attached at `/dev/sdf`, mounted at `/data`
- **Inputs:** VPC ID, subnet IDs, route table ID, SSH ingress CIDR — all in `terraform/terraform.tfvars` (see `terraform.tfvars.example`)

### Deploy
```bash
cd terraform && terraform apply
```

### Outputs
- `cluster_endpoint` — MemoryDB address+port
- `bastion_public_ip` — bastion IP
- `bastion_ssh_command` — SSH command
- `bastion_port_forward_command` — port forward command
- `bastion_api_url` — API server URL (`http://<IP>:8080`)
- `memorydb_endpoint` — `<host>:6379`
- `sessions_bucket` — bucket name

## Bastion Setup (post `terraform apply`)
```bash
# SSH (values from config.yaml / terraform output)
ssh -i "$BASTION_SSH_KEY" "$BASTION_USER@$BASTION_IP"

# Mount EBS
sudo mkfs -t ext4 /dev/sdf
sudo mkdir -p /data
sudo mount /dev/sdf /data
sudo chown ec2-user:ec2-user /data

# Install Python deps
sudo yum install -y python3-pip git
python3 -m pip install --user boto3 redis sentence-transformers fastapi uvicorn

# Start API server
nohup python3 server.py >> server.log 2>&1 &

# Check health
curl http://localhost:8080/health

# Sync session texts from S3 to EBS
aws s3 sync "s3://$S3_BUCKET/sessions/" /data/sessions/ --no-progress
```

## Indexes
### Pure Redis index (`idx`)
```bash
FT.CREATE idx ON HASH PREFIX 1 {session}: \
  SCHEMA text TEXT embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE
```
- Keys: `{session}:<question_id>:<session_index>`
- Fields: `text`, `embedding` (384×float32 bytes), `question_id`, `session_index`, `msg_count`

### Hybrid index (`idx_hybrid`)
```bash
FT.CREATE idx_hybrid ON HASH PREFIX 1 {hybrid}: \
  SCHEMA embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE s3_key TEXT
```
- Keys: `{hybrid}:<question_id>:<session_index>`
- Fields: `embedding`, `s3_key` (e.g. `sessions/<question_id>/<session_index>.txt`)

## Python Scripts

### Data Loading
| Script | Purpose | Key Output |
|---|---|---|
| `embed_and_load.py` | Generate embeddings from LongMemEval JSON, load into MemoryDB `idx` | `{session}:*` keys |
| `gpu_embed.py` | GPU-accelerated version of embed_and_load | Same, but faster |
| `load_hybrid.py` | Migrate text from `idx` to S3, create `idx_hybrid` keys | `sessions/` in S3, `{hybrid}:*` keys |

### Search
| Script | Purpose | Connection |
|---|---|---|
| `search.py --host HOST --port 6379 -k 10 -q QUERY` | Pure Redis search via `idx` | Direct Redis (port-forward or bastion) |
| `search_hybrid.py --host HOST --port 6379 -k 10 -q QUERY [--no-s3]` | Hybrid search via `idx_hybrid` + S3 (or local disk with `--no-s3`) | Direct Redis |

### API Server
| Script | Purpose |
|---|---|
| `server.py` | FastAPI on bastion (`uvicorn`, port 8080) |

Endpoints (POST, JSON `{"query": str, "k": int}`):
- `/search_pure` — Pure Redis (`idx`)
- `/search` — Hybrid S3 (`idx_hybrid` + S3 GetObject)
- `/search_disk` — Hybrid EBS (`idx_hybrid` + local file read from `/data/`)
- `/health` — GET health check

Config via `config.yaml` (see `config.example.yaml`) — every value also accepts an env-var override: `MEMORYDB_HOST`, `MEMORYDB_PORT`, `MEMORYDB_PASSWORD`, `S3_BUCKET`, `POOL_SIZE`, etc.

### Benchmarking
| Script | Purpose |
|---|---|
| `benchmark.py --host HOST --port 6379` | 100-query single-threaded pure Redis latency distribution |
| `benchmark_concurrent.py --host HOST --port 6379` | Concurrent throughput (1/2/4/8/16 threads), pure Redis |
| `benchmark_hybrid.py --host HOST --port 6379 [--no-s3]` | Hybrid concurrent throughput with optional S3 skip |

Run benchmarks from the bastion (local to MemoryDB) for accurate in-AWS numbers. The `benchmark_qps.py` tests all three endpoints via the API server.

## LongMemEval Dataset
- Source: https://huggingface.co/datasets/experilabs/LongMemEval
- File: `data/longmemeval_m_cleaned.json` (~2.5 GB, 500 items)
- Each item has ~475 sessions on average, each session is a chat conversation
- Sessions flattened to `user:` / `assistant:` prefixed text
- Chunked at 512-token sliding window (64 overlap), mean-pooled, L2 normalized

## Model
- `BAAI/bge-small-en-v1.5` — 384 dim, 512 token limit
- Device: CPU on bastion (`mps` on Mac, `cuda` on GPU instance)
- Encode time: ~70ms on t4g.small, ~12ms on MPS Mac, ~2ms on T4 GPU

## Reproducing the Full Experiment

### Phase 1: Infrastructure
```bash
cd terraform && terraform apply
```
This creates MemoryDB cluster, bastion, S3 bucket, EBS volume, VPC endpoint, IAM role.

### Phase 2: Load Data
1. SSH into bastion, install deps, mount EBS
2. Upload LongMemEval JSON to bastion or GPU instance
3. Run `embed_and_load.py` (CPU, ~8hrs) or `gpu_embed.py` (GPU on g4dn.xlarge, ~3hrs)
4. Create `idx` index via `FT.CREATE` (see above)
5. Run `load_hybrid.py` to migrate text to S3 and create `idx_hybrid`
6. Run `aws s3 sync s3://<bucket>/sessions/ /data/sessions/` to populate EBS disk

### Phase 3: Benchmark
```bash
# From bastion (MEMORYDB_HOST etc. come from config.yaml):
python3 benchmark.py --host "$MEMORYDB_HOST"
python3 benchmark_hybrid.py --host "$MEMORYDB_HOST"
python3 benchmark_hybrid.py --host "$MEMORYDB_HOST" --no-s3
```

### Phase 4: API Server
```bash
nohup python3 server.py >> server.log 2>&1 &
# Then curl endpoints from anywhere
```

## Critical Constants

Account-specific values are not hardcoded — they live in `config.yaml` (Python clients) and `terraform/terraform.tfvars` (infrastructure). Use `terraform output` after `apply` to read the deployed values.

| Setting | Source |
|---|---|
| MemoryDB endpoint | `terraform output memorydb_endpoint` → `config.yaml: memorydb.host` |
| S3 bucket | `terraform output sessions_bucket` → `config.yaml: s3.bucket` |
| Bastion IP | `terraform output bastion_public_ip` → `config.yaml: bastion.ip` |
| SSH key path | `config.yaml: bastion.ssh_key` |
| API URL | `terraform output bastion_api_url` |

Other fixed constants:
- EBS mount: `/data`
- Model: `BAAI/bge-small-en-v1.5`
- Python: 3.11+ (managed by `uv` locally, system python3.9 on bastion AL2023)
