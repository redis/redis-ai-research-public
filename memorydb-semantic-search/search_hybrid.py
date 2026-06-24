import argparse
import sys
import time

import boto3
import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
INDEX_NAME = "idx_hybrid"
BUCKET = cfg.s3.bucket

r = None
model = None
s3 = None


def pick_device():
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def connect(host, port, password):
    global r
    kwargs = dict(host=host, port=port, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    if password:
        kwargs["password"] = password
    r = redis.Redis(**kwargs)
    r.ping()
    info = r.info()
    used = info.get("used_memory_human", "?")
    total = info.get("maxmemory_human", "?")
    print(f"  Connected to Redis ({used} / {total} used)")
    global s3
    s3 = boto3.client("s3")


def load_model():
    global model
    dev = pick_device()
    print(f"  Loading {MODEL_NAME} on {dev}...", end=" ")
    sys.stdout.flush()
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=dev)
    model.max_seq_length = 512
    print(f"done ({time.time()-t0:.1f}s)")


def fetch_text(s3_key):
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=s3_key)
        return obj["Body"].read().decode(errors="replace")
    except Exception as e:
        return f"<S3 error: {e}>"


def search(query, k=10):
    t0 = time.time()
    q_emb = model.encode(query, normalize_embeddings=True).astype(np.float32)
    encode_time = time.time() - t0

    t1 = time.time()
    raw = r.execute_command(
        "FT.SEARCH", INDEX_NAME,
        f"*=>[KNN {k} @embedding $vec EF_RUNTIME 200 AS score]",
        "PARAMS", 2, "vec", q_emb.tobytes(),
        "DIALECT", 2,
    )
    search_time = time.time() - t1

    num = raw[0] if raw else 0
    output = []
    s3_times = []
    for i in range(num):
        key = raw[1 + i * 2].decode()
        fields = raw[1 + i * 2 + 1]
        doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
        s3_key = doc.get("s3_key", b"").decode()
        score = float(doc.get("score", b"0"))

        t2 = time.time()
        text = fetch_text(s3_key)
        s3_times.append(time.time() - t2)

        output.append({
            "rank": i + 1,
            "score": score,
            "key": key,
            "s3_key": s3_key,
            "text": text[:500],
        })

    total_time = time.time() - t0
    avg_s3 = (sum(s3_times) / len(s3_times) * 1000) if s3_times else 0
    return output, encode_time, search_time, total_time, avg_s3


def display_results(results, encode_time, search_time, total_time, avg_s3):
    print()
    print(f"  Query encoding: {encode_time*1000:.1f}ms")
    print(f"  Search (FT.SEARCH): {search_time*1000:.1f}ms")
    print(f"  S3 fetch (avg): {avg_s3:.1f}ms" if avg_s3 else "")
    print(f"  Total latency: {total_time*1000:.1f}ms")
    print()
    print(f"  {'Rank':<5} {'Score':<8} {'Preview'}")
    print(f"  {'-'*5} {'-'*8} {'-'*80}")
    for r in results:
        preview = r["text"].replace("\n", " ")[:80]
        print(f"  {r['rank']:<5} {r['score']:<8.4f} {preview}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Semantic search via hybrid Redis+S3 backend")
    parser.add_argument("--host", default="localhost", help="Redis host (default: localhost; use --host=config to read from config.yaml)")
    parser.add_argument("--port", type=int, default=cfg.memorydb.port, help="Redis port")
    parser.add_argument("--password", default=cfg.memorydb.password, help="Redis password")
    parser.add_argument("-k", type=int, default=10, help="Number of results to return (default: 10)")
    parser.add_argument("--query", "-q", nargs="+", help="Query text (omit for interactive mode)")
    args = parser.parse_args()

    print("Initializing...")
    t_start = time.time()
    connect(args.host, args.port, args.password)
    load_model()
    print(f"  Ready in {time.time()-t_start:.1f}s")
    print()

    if args.query:
        query = " ".join(args.query)
        results, enc_t, search_t, total_t, avg_s3 = search(query, k=args.k)
        print(f"Query: {query}")
        print(f"K: {args.k}")
        display_results(results, enc_t, search_t, total_t, avg_s3)
        return

    print(f"Interactive mode — enter queries (Ctrl+C to quit, k={args.k})")
    print()
    try:
        while True:
            try:
                raw = input("query> ").strip()
            except EOFError:
                break
            if not raw:
                continue
            if raw.startswith("k="):
                try:
                    args.k = int(raw[2:])
                    print(f"  k set to {args.k}")
                except ValueError:
                    print("  invalid k")
                continue
            if raw in ("q", "quit", "exit"):
                break
            results, enc_t, search_t, total_t, avg_s3 = search(raw, k=args.k)
            print(f"  Query: {raw}")
            print(f"  K: {args.k}")
            display_results(results, enc_t, search_t, total_t, avg_s3)
    except KeyboardInterrupt:
        print()
        print("Bye")


if __name__ == "__main__":
    main()
