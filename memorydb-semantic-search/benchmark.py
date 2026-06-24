import argparse
import random
import sys
import time
import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
INDEX_NAME = "idx"
N = 100


def pick_device():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def connect(host, port, password):
    kwargs = dict(host=host, port=port, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    if password:
        kwargs["password"] = password
    r = redis.Redis(**kwargs)
    r.ping()
    return r


def load_model():
    dev = pick_device()
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=dev)
    model.max_seq_length = 512
    return model, time.time() - t0


def fetch_queries(r, n=100):
    print(f"  Fetching {n} sample queries from Redis...", end=" ")
    sys.stdout.flush()
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
    print(f"got {len(queries)} queries")
    return queries[:n]


def search(r, model, query, k=10):
    t0 = time.time()
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
    total_time = time.time() - t0
    return encode_time, search_time, total_time


def stats(label, values):
    arr = np.array(values)
    print(f"  {label:>15s}:  min={arr.min():8.1f}ms  p50={np.median(arr):8.1f}ms  "
          f"p95={np.percentile(arr, 95):8.1f}ms  p99={np.percentile(arr, 99):8.1f}ms  "
          f"max={arr.max():8.1f}ms  mean={arr.mean():8.1f}ms  std={arr.std():.1f}ms")


def main():
    parser = argparse.ArgumentParser(description="Load test: 100 semantic searches")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=cfg.memorydb.port)
    parser.add_argument("--password", default=cfg.memorydb.password)
    parser.add_argument("-k", type=int, default=10)
    args = parser.parse_args()

    print("=== Load test: 100 queries ===")
    print()
    print("Initializing...")
    t_global = time.time()
    r = connect(args.host, args.port, args.password)
    model, model_load_time = load_model()
    queries = fetch_queries(r, N)
    init_time = time.time() - t_global
    print(f"  Init ready in {init_time:.1f}s (model: {model_load_time:.1f}s)")
    print()
    print(f"Running {len(queries)} searches (k={args.k})...")

    encode_times = []
    search_times = []
    total_times = []

    for i, query in enumerate(queries, 1):
        enc_t, s_t, tot_t = search(r, model, query, k=args.k)
        encode_times.append(enc_t * 1000)
        search_times.append(s_t * 1000)
        total_times.append(tot_t * 1000)
        if i % 10 == 0 or i == 1:
            print(f"  [{i:3d}/{len(queries)}]  encode={enc_t*1000:.0f}ms  "
                  f"search={s_t*1000:.0f}ms  total={tot_t*1000:.0f}ms")

    print()
    print("=== Results ===")
    print(f"  Init time: {init_time:.1f}s  (model load: {model_load_time:.1f}s)")
    print(f"  Queries: {len(queries)}  k={args.k}")
    print()
    stats("Encode", encode_times)
    stats("Search", search_times)
    stats("Total", total_times)


if __name__ == "__main__":
    main()
