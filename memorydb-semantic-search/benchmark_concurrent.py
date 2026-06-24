import argparse
import random
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
INDEX_NAME = "idx"
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]
QUERIES_PER_LEVEL = 50


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
    raw = r.execute_command("FT.SEARCH", INDEX_NAME, "*", "RETURN", 1, "text", "LIMIT", 0, n)
    queries = []
    for i in range(min(n, (len(raw) - 1) // 2)):
        fields = raw[1 + i * 2 + 1]
        doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
        text = doc.get("text", b"").decode(errors="replace")
        words = text.split()
        if len(words) >= 4:
            queries.append(" ".join(words[:40]))
    random.shuffle(queries)
    return queries[:n]


def search(r, model, lock, query, k=10):
    t0 = time.time()
    with lock:
        q_emb = model.encode(query, normalize_embeddings=True).astype(np.float32)
    encode_time = time.time() - t0
    t1 = time.time()
    r.execute_command(
        "FT.SEARCH", INDEX_NAME,
        f"*=>[KNN {k} @embedding $vec EF_RUNTIME 200 AS score]",
        "PARAMS", 2, "vec", q_emb.tobytes(),
        "DIALECT", 2,
    )
    search_time = time.time() - t1
    return encode_time, search_time, time.time() - t0


def run_concurrent(r, model, lock, queries, queries_per_level, concurrency):
    latencies = []
    wall_start = time.time()

    def task(_):
        q = random.choice(queries)
        enc_t, s_t, tot_t = search(r, model, lock, q)
        return enc_t, s_t, tot_t

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(task, i) for i in range(queries_per_level)]
        for f in as_completed(futures):
            enc_t, s_t, tot_t = f.result()
            latencies.append(tot_t * 1000)

    wall_time = time.time() - wall_start
    return latencies, wall_time


def main():
    parser = argparse.ArgumentParser(description="Concurrent load test")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=cfg.memorydb.port)
    parser.add_argument("--password", default=cfg.memorydb.password)
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--levels", type=int, nargs="*", default=CONCURRENCY_LEVELS)
    args = parser.parse_args()

    print("=== Concurrent load test ===")
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

    header = f"{'Concurrency':>12s}  {'p50':>8s}  {'p95':>8s}  {'p99':>8s}  {'mean':>8s}  {'max':>8s}  {'wall':>8s}  {'qps':>8s}"
    print(header)
    print("-" * len(header))

    for c in args.levels:
        latencies, wall_time = run_concurrent(r, model, lock, queries, QUERIES_PER_LEVEL, c)
        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        mean = sum(latencies) / len(latencies)
        mx = latencies[-1]
        qps = QUERIES_PER_LEVEL / wall_time
        wall_ms = wall_time * 1000
        print(f"{c:>12d}  {p50:>8.1f}  {p95:>8.1f}  {p99:>8.1f}  {mean:>8.1f}  {mx:>8.1f}  {wall_ms:>8.1f}  {qps:>8.1f}")


if __name__ == "__main__":
    main()
