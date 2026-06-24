# Benchmark Results

**Date:** 2026-06-03
**Setup:**
- MemoryDB: `my-redis-db-search` (`db.r7g.large`, `default.memorydb-valkey7.search`)
- Model: `BAAI/bge-small-en-v1.5` (384 dim, 512 tokens)
- Vector index: HNSW, cosine, EF_RUNTIME 200
- K: 10
- All benchmarks from bastion `t4g.small` in same VPC, 200 queries each via API server

## Architecture Comparison

| Aspect | Pure Redis | Hybrid Redis+S3 | Hybrid Redis+EBS |
|---|---|---|---|
| **Backend** | All data in MemoryDB | Embedding in MemoryDB, text in S3 | Embedding in MemoryDB, text on EBS |
| **Storage per key** | ~34 KB | ~2.7 KB (12x less) | ~2.7 KB (12x less) |
| **Total MemoryDB usage** | 7.78 GB | 0.62 GB | 0.62 GB |
| **Extra storage cost** | None | ~$0.14/mo (S3) | ~$1/mo (10GB gp3) |
| **Min node** | `r7g.large` (13.07 GB, $245/mo) | `r6g.large` (6.44 GB, $191/mo) | `r6g.large` (6.44 GB, $191/mo) |
| **Complexity** | Single hop (Redis) | 2 hops (Redis → S3) | 2 hops (Redis → local file) |

## Single-Query Latency Distribution (500 queries each, in-AWS from bastion t4g.small)

All measurements via API server on t4g.small, sequential requests (no concurrency), cycling through 17 unique queries.

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

Timing breakdown (p50):

| Component | Pure Redis | Hybrid+S3 | Hybrid+EBS |
|---|---|---|---|
| **Encode** | 70.1 ms | 71.3 ms | 70.8 ms |
| **FT.SEARCH** | 2.7 ms | 2.5 ms | 2.5 ms |
| **Text fetch** | 0.0 ms (inline) | ~68 ms (S3) | 1.2 ms (disk) |
| **Total** | 76.1 ms | 141.0 ms | 77.7 ms |

- **Encode is the dominant cost** (~70ms on t4g.small CPU). All three approaches share this.
- **Pure Redis vs EBS are nearly identical** (76.1 vs 77.7 ms). The 1.2ms disk read adds only ~2% overhead.
- **Hybrid+S3 is 1.85x slower at p50** (141ms vs 76ms), with a long tail to 380ms.
- **Distribution shape is tight for all** — pure/EBS p10-p90 span ~16ms, S3 spans ~43ms.

## Concurrent QPS (via API server, 100 queries per level)

All three hit the same API server on t4g.small. The server uses a `threading.Lock` for encoding, serializing all requests through the GIL.

| Concurrency | Pure Redis p50 | EBS p50 | S3 p50 | Pure QPS | EBS QPS | S3 QPS |
|---|---|---|---|---|---|---|
| 1 | 74.8ms | 89.8ms | 151.3ms | 13.0 | 11.0 | 6.3 |
| 2 | 186.7ms | 189.9ms | 261.4ms | 10.5 | 10.5 | 7.6 |
| 4 | 373.4ms | 386.3ms | 528.4ms | 10.6 | 10.4 | 7.6 |
| 8 | 755.2ms | 779.9ms | 1055.2ms | 10.5 | 10.3 | 7.6 |
| 16 | 1534.4ms | 1527.0ms | 2100.0ms | 10.4 | 10.4 | 7.5 |
| 32 | 3080.3ms | 3039.9ms | 4161.2ms | 10.3 | 10.4 | 7.5 |

Key observations:

- **Pure Redis and EBS are identical** — both saturate at ~10.5 QPS. The encode lock is the bottleneck.
- **Hybrid+S3 maxes out at ~7.5 QPS** — 30% lower than Redis/EBS, because the S3 fetch adds I/O wait time that the lock serialization amplifies.
- **Latency scales linearly with concurrency** for all three approaches (the lock queues up requests).
- **QPS is flat regardless of concurrency** — adding threads doesn't help because only one request can encode at a time.
- **To increase QPS**: use multiprocessing (separate workers with their own model copy) or deploy behind a load balancer with multiple API server instances.

## Memory & Cost Comparison

| Approach | MemoryDB Usage | Extra Storage | Monthly Cost (MemoryDB) | Notes |
|---|---|---|---|---|
| Pure Redis | 7.78 GB | None | $245 (r7g.large min) | Full text inline |
| Hybrid+S3 | 0.62 GB | ~7 GB S3 ($0.14) | $191 (r6g.large min) | Text in S3, 113ms fetch |
| Hybrid+EBS | 0.62 GB | 10 GB EBS ($1) | $191 (r6g.large min) | Text on disk, 0.7ms fetch |

## Key Takeaways

1. **Encode is the bottleneck** for all approaches (~70ms on t4g.small CPU).
2. **FT.SEARCH is fast** within the same VPC (~2.5ms).
3. **EBS disk approach is the best hybrid tradeoff** — 12.5x memory savings for only 2ms added latency (0.7ms disk read vs 0ms inline).
4. **S3 adds 113ms p50 with high variance** — VPC Gateway Endpoint helps but boto3/TLS overhead (~6ms/object) adds up over 10 results.
5. **Pure Redis and Hybrid+EBS are nearly identical in latency**. The 0.7ms disk fetch is imperceptible.
6. **Cost savings**: Hybrid approaches can use `r6g.large` ($191/mo) vs `r7g.large` ($245/mo), saving ~$54/mo.
