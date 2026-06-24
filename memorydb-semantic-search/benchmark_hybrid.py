import argparse
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
INDEX_NAME = "idx_hybrid"
BUCKET = cfg.s3.bucket
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]
QUERIES_PER_LEVEL = 50

_tls = threading.local()


def _s3():
    if not hasattr(_tls, "s3"):
        _tls.s3 = boto3.client("s3")
    return _tls.s3


def pick_device():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def connect(host, port, password):
    kwargs = dict(host=host, port=port, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    if password:
        kwargs["password"] = password
    return redis.Redis(**kwargs)


def load_model():
    dev = pick_device()
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=dev)
    model.max_seq_length = 512
    return model, time.time() - t0


def fetch_queries(r, n=100):
    raw = r.execute_command("FT.SEARCH", INDEX_NAME, "*", "RETURN", 1, "s3_key", "LIMIT", 0, n)
    queries = []
    s3 = boto3.client("s3")
    for i in range(min(n, (len(raw) - 1) // 2)):
        fields = raw[1 + i * 2 + 1]
        doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
        s3k = doc.get("s3_key", b"").decode()
        if s3k:
            try:
                obj = s3.get_object(Bucket=BUCKET, Key=s3k)
                text = obj["Body"].read().decode(errors="replace")
                words = text.split()
                if len(words) >= 4:
                    queries.append(" ".join(words[:40]))
            except Exception:
                pass
    random.shuffle(queries)
    return queries[:n]


def search(r, model, lock, query, k=10, fetch_s3=False):
    t0 = time.time()
    with lock:
        q_emb = model.encode(query, normalize_embeddings=True).astype("float32")
    encode_time = time.time() - t0

    t1 = time.time()
    raw = r.execute_command(
        "FT.SEARCH", INDEX_NAME,
        f"*=>[KNN {k} @embedding $vec EF_RUNTIME 200 AS score]",
        "PARAMS", 2, "vec", q_emb.tobytes(),
        "DIALECT", 2,
    )
    search_time = time.time() - t1

    s3_time = 0.0
    if fetch_s3 and raw and raw[0] > 0:
        s3_keys = []
        for i in range(raw[0]):
            fields = raw[1 + i * 2 + 1]
            doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
            s3_keys.append(doc.get("s3_key", b"").decode())

        t2 = time.time()
        s3 = _s3()
        for sk in s3_keys:
            try:
                s3.get_object(Bucket=BUCKET, Key=sk)
            except Exception:
                pass
        s3_time = time.time() - t2

    total_time = time.time() - t0
    return encode_time, search_time, s3_time, total_time


def run_concurrent(r, model, lock, queries, queries_per_level, concurrency, fetch_s3):
    latencies = []
    wall_start = time.time()

    def task(_):
        q = random.choice(queries)
        *_, tot_t = search(r, model, lock, q, fetch_s3=fetch_s3)
        return tot_t

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(task, i) for i in range(queries_per_level)]
        for f in as_completed(futures):
            latencies.append(f.result() * 1000)

    wall_time = time.time() - wall_start
    return latencies, wall_time


def main():
    parser = argparse.ArgumentParser(description="Hybrid concurrent load test (Redis+S3)")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--password", default=None)
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--levels", type=int, nargs="*", default=CONCURRENCY_LEVELS)
    parser.add_argument("--no-s3", action="store_true", help="Skip S3 fetch (search only)")
    args = parser.parse_args()

    label = "search + S3" if not args.no_s3 else "search only"
    print(f"=== Hybrid concurrent load test ({label}) ===")
    print()
    print("Initializing...")
    t_global = time.time()
    r = connect(args.host, args.port, args.password)
    model, model_load_time = load_model()
    lock = threading.Lock()
    queries = fetch_queries(r, 50)
    init_time = time.time() - t_global
    print(f"  Ready in {init_time:.1f}s (model: {model_load_time:.1f}s, queries: {len(queries)})")
    print()
    print(f"Queries per level: {QUERIES_PER_LEVEL}")
    print()

    header = f"{'Concurrency':>12s}  {'p50':>8s}  {'p95':>8s}  {'mean':>8s}  {'max':>8s}  {'wall':>8s}  {'qps':>8s}"
    print(header)
    print("-" * len(header))

    for c in args.levels:
        latencies, wall_time = run_concurrent(r, model, lock, queries, QUERIES_PER_LEVEL, c, fetch_s3=not args.no_s3)
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        mean = sum(latencies) / len(latencies)
        mx = latencies[-1]
        qps = QUERIES_PER_LEVEL / wall_time
        wall_ms = wall_time * 1000
        print(f"{c:>12d}  {p50:>8.1f}  {p95:>8.1f}  {mean:>8.1f}  {mx:>8.1f}  {wall_ms:>8.1f}  {qps:>8.1f}")


if __name__ == "__main__":
    main()
