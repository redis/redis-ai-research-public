# Vector Search on AWS MemoryDB: Pure Redis vs Hybrid S3 vs Hybrid EBS

## A Performance and Cost Comparison for Semantic Search at Scale

---

## 1. Executive Summary

We evaluated three architectures for serving semantic search over ~238,000 chat sessions from the LongMemEval Medium dataset, using **AWS MemoryDB** (Redis-compatible, Valkey 7.2) with vector search (HNSW, 384-dim embeddings). The three approaches differ in where the session text is stored:

| Approach | Text Location | MemoryDB Usage | Latency p50 | Monthly Cost |
|---|---|---|---|---|
| **Pure Redis** | Inline in MemoryDB key | 7.78 GB | 76.1 ms | $245 |
| **Hybrid + S3** | S3 (VPC Gateway Endpoint) | 0.62 GB | 141.0 ms | $191 |
| **Hybrid + EBS** | Local EBS disk on bastion | 0.62 GB | 77.7 ms | $191 |

**Key finding**: Hybrid+EBS delivers 12.5× memory savings with only ~2 ms (2%) added latency over Pure Redis, making it the best cost-performance tradeoff. Hybrid+S3 adds 65 ms (85%) with significant tail variance.

---

## 2. Introduction

Semantic search requires storing both vector embeddings and the original text documents. The naive approach stores everything in the vector database, but this is expensive for large text corpora. We explore two hybrid alternatives — moving text to S3 or local disk — and measure the real-world latency, throughput, and cost tradeoffs on AWS.

### 2.1 Dataset

**LongMemEval Medium** (Hugging Face: `experilabs/LongMemEval`)

| Property | Value |
|---|---|
| Items | 500 |
| Sessions per item (avg) | ~475 |
| Total sessions | 237,655 |
| JSON file size | ~2.5 GB |
| Text per session (avg) | ~30 KB |
| Total text on disk | ~2.8 GB |

Each session is a multi-turn chat conversation (user/assistant messages). Sessions are flattened, chunked at 512-word sliding windows (64-word overlap), mean-pooled, and L2-normalized.

### 2.2 Model

**BAAI/bge-small-en-v1.5** (sentence-transformers)

| Property | Value |
|---|---|
| Embedding dimension | 384 |
| Max tokens | 512 |
| Distance metric | Cosine (via L2 normalization) |
| Encode time (t4g.small CPU) | ~70 ms |
| Encode time (MPS Mac) | ~12 ms |
| Encode time (T4 GPU) | ~2 ms |

---

## 3. Experiment Setup

### 3.1 Infrastructure (AWS, us-east-1)

| Resource | Type | Purpose |
|---|---|---|
| **MemoryDB** | `db.r7g.large`, Valkey 7.2, search-enabled | Vector storage + search |
| **Bastion** | `t4g.small`, Amazon Linux 2023 | API server + EBS mount |
| **S3** | configured in `config.yaml` (`s3.bucket`) | Text storage (hybrid) |
| **EBS** | 10 GB gp3 at `/data` | Local text cache (hybrid) |
| **VPC Endpoint** | Gateway, `com.amazonaws.us-east-1.s3` | Same-region S3 access |

All infrastructure provisioned via Terraform (`terraform/main.tf`).

### 3.2 Vector Indexes

**Pure Redis index (`idx`):**

```
FT.CREATE idx ON HASH PREFIX 1 {session}: \
  SCHEMA text TEXT embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE
```

Keys: `{session}:{question_id}:{session_index}` — 7.78 GB total.

**Hybrid index (`idx_hybrid`):**

```
FT.CREATE idx_hybrid ON HASH PREFIX 1 {hybrid}: \
  SCHEMA embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE s3_key TEXT
```

Keys: `{hybrid}:{question_id}:{session_index}` — 0.62 GB total.

### 3.3 Search Flow

All three approaches follow the same high-level flow:

1. Encode query text → 384-dim float32 vector (~70 ms)
2. `FT.SEARCH` with `KNN 10 @embedding $vec EF_RUNTIME 200` (~2.5 ms)
3. Fetch text for 10 results (0 ms inline, ~1 ms disk, or ~68 ms S3)

The encoding step is protected by a `threading.Lock` in the API server, serializing all requests through Python's GIL.

### 3.4 API Server

A FastAPI server (`server.py`) runs on the bastion (`t4g.small`) at port 8080:

| Endpoint | Index | Text Source |
|---|---|---|
| `POST /search_pure` | `idx` | Inline in Redis response |
| `POST /search` | `idx_hybrid` | S3 GetObject (parallel, pool of 8) |
| `POST /search_disk` | `idx_hybrid` | Local file read from `/data/` (parallel, pool of 8) |

### 3.5 Benchmark Methodology

- **Hardware**: Bastion `t4g.small` (2 vCPU ARM, 2 GB RAM) in same VPC as MemoryDB
- **Queries**: 17 unique queries extracted from search results, cycled through 500 requests per approach
- **K**: 10 (top-10 nearest neighbors with text)
- **Metrics**: Request-level wall-clock latency via `curl` timing to API server
- **Concurrency**: `ThreadPoolExecutor` from a separate Python process, 100 queries per level at 1/2/4/8/16/32 concurrency

---

## 4. Results

### 4.1 Single-Query Latency Distribution (500 requests each)

| Percentile | Pure Redis (ms) | Hybrid+S3 (ms) | Hybrid+EBS (ms) |
|---|---|---|---|
| **p1** | 46.0 | 107.0 | 46.4 |
| **p5** | 47.5 | 128.4 | 48.9 |
| **p10** | 71.5 | 131.4 | 73.0 |
| **p25** | 74.1 | 135.8 | 75.4 |
| **p50** | **76.1** | **141.0** | **77.7** |
| **p75** | 81.0 | 154.3 | 82.7 |
| **p90** | 87.3 | 174.8 | 88.7 |
| **p95** | 97.3 | 187.8 | 99.8 |
| **p99** | 100.9 | 218.5 | 102.7 |
| **p100** | 105.1 | 380.5 | 120.0 |

### 4.2 Timing Breakdown (p50)

| Component | Pure Redis | Hybrid+S3 | Hybrid+EBS |
|---|---|---|---|
| **Encode (CPU)** | 70.1 ms | 71.3 ms | 70.8 ms |
| **FT.SEARCH (KNN)** | 2.7 ms | 2.5 ms | 2.5 ms |
| **Text fetch** | 0.0 ms (inline) | 67.2 ms (S3) | 1.2 ms (disk read) |
| **Total** | 76.1 ms | 141.0 ms | 77.7 ms |

Key observations:
- **Encode dominates all three** (~70 ms, 92% of total for pure Redis). This is the GIL-bound SentenceTransformer on CPU.
- **FT.SEARCH is fast** (~2.5 ms) across both indexes. The HNSW index size (237k vectors, 384 dim) is well within MemoryDB's capabilities.
- **EBS disk read adds only 1.2 ms** for 10 parallel file reads (average file size ~12 KB). Linux page cache further reduces this for frequently accessed files.
- **S3 GetObject adds ~68 ms** for 10 parallel requests through boto3 (TLS handshake, request signing, response parsing). Despite a VPC Gateway Endpoint, each object GET takes ~6 ms.

### 4.3 Concurrent Throughput

| Concurrency | Pure Redis p50 | EBS p50 | S3 p50 | Pure QPS | EBS QPS | S3 QPS |
|---|---|---|---|---|---|---|
| 1 | 74.8 ms | 89.8 ms | 151.3 ms | 13.0 | 11.0 | 6.3 |
| 2 | 186.7 ms | 189.9 ms | 261.4 ms | 10.5 | 10.5 | 7.6 |
| 4 | 373.4 ms | 386.3 ms | 528.4 ms | 10.6 | 10.4 | 7.6 |
| 8 | 755.2 ms | 779.9 ms | 1055.2 ms | 10.5 | 10.3 | 7.6 |
| 16 | 1534.4 ms | 1527.0 ms | 2100.0 ms | 10.4 | 10.4 | 7.5 |
| 32 | 3080.3 ms | 3039.9 ms | 4161.2 ms | 10.3 | 10.4 | 7.5 |

Key observations:
- **QPS is flat after 1 thread** for all approaches. The GIL serializes encoding.
- **Pure Redis and EBS saturate at ~10.5 QPS**. Latency scales linearly with concurrency (the lock queues requests).
- **S3 maxes at ~7.5 QPS** — 30% lower. The S3 I/O wait time compounds under lock contention: one thread holds the encode lock while others wait, then the unlocked thread pays the S3 penalty before the next can encode.
- **To increase QPS**: use multiprocessing (one lock per worker) or deploy multiple API server instances behind a load balancer.

### 4.4 Memory and Cost Comparison

| Approach | MemoryDB | Extra Storage | Min Node | Monthly Cost |
|---|---|---|---|---|
| **Pure Redis** | 7.78 GB | None | `r7g.large` (13.07 GB) | **$245/mo** |
| **Hybrid+S3** | 0.62 GB | ~7 GB S3 ($0.14/mo) | `r6g.large` (6.44 GB) | **$191/mo** |
| **Hybrid+EBS** | 0.62 GB | 10 GB gp3 ($1/mo) | `r6g.large` (6.44 GB) | **$191/mo** |

The pure Redis approach stores ~34 KB per key (text + embedding + metadata). The hybrid approach stores only ~2.7 KB (embedding + s3_key), a 12.5× reduction. This allows dropping from `r7g.large` (13.07 GB, $245/mo) to `r6g.large` (6.44 GB, $191/mo), saving **$54/mo**.

### 4.5 Variance Analysis

| Metric | Pure Redis | Hybrid+S3 | Hybrid+EBS |
|---|---|---|---|
| **Std dev (total)** | 7.7 ms | 209.2 ms | 7.2 ms |
| **p90 - p10 span** | 15.8 ms | 43.4 ms | 15.7 ms |
| **p99 - p50 span** | 24.8 ms | 77.5 ms | 25.0 ms |

- **Pure Redis and EBS have near-identical variance** — sub-8 ms std dev and ~16 ms p10-p90 span.
- **S3 has 27× higher std dev**. The p95 jumps to 187.8 ms and max hits 380.5 ms.

The S3 variance is caused by:
1. **boto3 overhead**: Each GetObject requires TLS negotiation, request signing, and XML response parsing (~6 ms per object)
2. **Network jitter**: Even within the same region, S3 via Gateway Endpoint experiences variable latency
3. **Parallel fetch limits**: The pool of 8 workers on a 2-vCPU instance creates resource contention

---

## 5. Discussion

### 5.1 The Encode Bottleneck

The single largest contributor to latency across all three approaches is query encoding (~70 ms on t4g.small). This is inherent to running SentenceTransformer on CPU. The GIL prevents concurrent encoding even with multiple threads.

**Mitigations:**
- **GPU encoding** (T4): reduces ~70 ms → ~2 ms (35× faster). A GPU instance colocated in the same VPC would shift the bottleneck to network I/O.
- **Multiprocessing**: Each worker process has its own model copy and GIL, allowing true parallel encoding. With 4 workers, theoretical QPS rises from ~10 to ~40.
- **Caching**: Frequently repeated queries could skip encoding entirely.

### 5.2 S3 vs EBS: Why Such a Difference?

Both S3 and EBS are network-attached storage in the same AWS region. The main difference is the access pattern:

| Factor | S3 (boto3) | EBS (local file) |
|---|---|---|
| **Protocol** | HTTPS + TLS | POSIX syscall |
| **Overhead per request** | ~6 ms (TLS + signing + parse) | ~0.1 ms (open + read + close) |
| **Parallelism** | Connection pool (limited) | Kernel I/O scheduler |
| **Page cache** | None | Linux dentry/page cache |

For 10 parallel reads, S3 pays 10× ~6 ms = ~60 ms overhead before any data is transferred. EBS pays 10× ~0.1 ms ≈ 1 ms. The actual data transfer is comparable (~12 KB per file).

### 5.3 When to Use Each Approach

**Choose Pure Redis when:**
- MemoryDB cost is not a concern
- Minimum latency is critical (every ms matters)
- Dataset fits comfortably in a single node

**Choose Hybrid+EBS when:**
- Cost reduction is important ($54/mo savings)
- Sub-100 ms latency is acceptable
- You can attach an EBS volume to the API server
- Dataset is too large for pure Redis

**Avoid Hybrid+S3 when:**
- Sub-200 ms latency is required
- Tail latency variance is a concern
- Alternative (EBS or pure Redis) is available

---

## 6. Conclusions

1. **Hybrid+EBS is the best tradeoff**: 12.5× memory savings with only 2% latency overhead (76 ms → 78 ms p50). Nearly indistinguishable from Pure Redis in practice.

2. **Hybrid+S3 is viable but expensive in latency**: 85% higher p50 (141 ms vs 76 ms) with severe tail variance (380 ms max). Only suitable when S3 is the only option and latency is not critical.

3. **Encoding dominates all approaches**: ~70 ms of the ~77 ms total is CPU-bound model inference. No storage architecture can work around this bottleneck.

4. **FT.SEARCH is extremely fast**: ~2.5 ms for HNSW KNN on 237k × 384-dim vectors. MemoryDB's vector search capability is production-ready.

5. **Cost savings are real**: Hybrid approaches reduce MemoryDB from $245/mo (r7g.large) to $191/mo (r6g.large), saving $648/year while maintaining comparable performance for the EBS variant.

---

## Appendix A: Infrastructure Deployment

```bash
cd terraform && terraform apply
```

This provisions: MemoryDB cluster (search-enabled), bastion EC2 (t4g.small), S3 bucket with VPC Gateway Endpoint, EBS volume (10 GB gp3), IAM role, and security groups.

## Appendix B: Bastion Setup

```bash
# Mount EBS
sudo mkfs -t ext4 /dev/sdf
sudo mkdir -p /data && sudo mount /dev/sdf /data
sudo chown ec2-user:ec2-user /data

# Install deps
sudo yum install -y python3-pip git
python3 -m pip install --user boto3 redis sentence-transformers fastapi uvicorn

# Start API server
nohup python3 server.py >> server.log 2>&1 &

# Sync EBS from S3
aws s3 sync "s3://$S3_BUCKET/sessions/" /data/sessions/ --no-progress
```

## Appendix C: Creating Indexes

```bash
# Pure Redis
FT.CREATE idx ON HASH PREFIX 1 {session}: \
  SCHEMA text TEXT embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE

# Hybrid
FT.CREATE idx_hybrid ON HASH PREFIX 1 {hybrid}: \
  SCHEMA embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE s3_key TEXT
```

## Appendix D: Running Benchmarks

```bash
# From bastion (in-VPC):
python3 benchmark.py --host "$MEMORYDB_HOST" --port "$MEMORYDB_PORT"
python3 benchmark_hybrid.py --host <memorydb-endpoint> --port 6379
python3 benchmark_hybrid.py --host <memorydb-endpoint> --port 6379 --no-s3
```

## Appendix E: Data Loading

```bash
# GPU-accelerated (g4dn.xlarge, ~3 hrs)
python3 gpu_embed.py

# CPU-only (bastion t4g.small, ~8 hrs)
python3 embed_and_load.py

# Migrate pure Redis text to S3 for hybrid approach
python3 load_hybrid.py
```

---

*Experiment conducted June 2026. All benchmarks run within AWS us-east-1. Full source code and Terraform configuration available in the repository.*
