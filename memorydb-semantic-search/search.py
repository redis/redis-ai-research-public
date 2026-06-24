import argparse
import sys
import time

import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
INDEX_NAME = "idx"

r = None
model = None


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


def load_model():
    global model
    dev = pick_device()
    print(f"  Loading {MODEL_NAME} on {dev}...", end=" ")
    sys.stdout.flush()
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device=dev)
    model.max_seq_length = 512
    print(f"done ({time.time()-t0:.1f}s)")


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
    total_time = time.time() - t0

    num = raw[0] if raw else 0
    output = []
    for i in range(num):
        key = raw[1 + i * 2].decode()
        fields = raw[1 + i * 2 + 1]
        doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
        text = doc.get("text", b"?").decode(errors="replace")[:500]
        score = float(doc.get("score", b"0"))
        output.append({"rank": i + 1, "score": score, "key": key, "text": text})

    return output, encode_time, search_time, total_time


def display_results(results, encode_time, search_time, total_time):
    print()
    print(f"  Query encoding: {encode_time*1000:.1f}ms")
    print(f"  Search (FT.SEARCH): {search_time*1000:.1f}ms")
    print(f"  Total latency: {total_time*1000:.1f}ms")
    print()
    print(f"  {'Rank':<5} {'Score':<8} {'Preview'}")
    print(f"  {'-'*5} {'-'*8} {'-'*80}")
    for r in results:
        preview = r["text"].replace("\n", " ")[:80]
        print(f"  {r['rank']:<5} {r['score']:<8.4f} {preview}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Semantic search over LongMemEval chat sessions")
    parser.add_argument("--host", default="localhost", help="Redis host (default: localhost via SSH tunnel)")
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
        results, enc_t, search_t, total_t = search(query, k=args.k)
        print(f"Query: {query}")
        print(f"K: {args.k}")
        display_results(results, enc_t, search_t, total_t)
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
            results, enc_t, search_t, total_t = search(raw, k=args.k)
            print(f"  Query: {raw}")
            print(f"  K: {args.k}")
            display_results(results, enc_t, search_t, total_t)
    except KeyboardInterrupt:
        print()
        print("Bye")


if __name__ == "__main__":
    main()
