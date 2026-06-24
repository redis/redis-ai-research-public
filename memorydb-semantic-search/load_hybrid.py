import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
import redis

from config import cfg

HYBRID_PREFIX = "{hybrid}:"
BUCKET = cfg.s3.bucket
S3_PREFIX = cfg.s3.prefix
NUM_WORKERS = 50

_tls = threading.local()


def _r():
    if not hasattr(_tls, "r"):
        _tls.r = redis.Redis(host="localhost", port=6379, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    return _tls.r


def _s3():
    if not hasattr(_tls, "s3"):
        _tls.s3 = boto3.client("s3")
    return _tls.s3


def connect(host, port, password):
    kwargs = dict(host=host, port=port, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    if password:
        kwargs["password"] = password
    r = redis.Redis(**kwargs)
    r.ping()
    return r


def migrate_key(key_bytes):
    key = key_bytes.decode()
    if key.startswith(HYBRID_PREFIX):
        return None, "skip"

    r = _r()
    s3 = _s3()

    try:
        fields = r.hgetall(key_bytes)
    except redis.ResponseError:
        return None, "skip"
    if not fields:
        return None, "skip"

    embed = fields.get(b"embedding")
    text = fields.get(b"text")
    question_id = fields.get(b"question_id", b"").decode()
    session_index = fields.get(b"session_index", b"0").decode()
    msg_count = fields.get(b"msg_count", b"0")

    if not text or not embed:
        return None, "skip"

    hybrid_key = f"{HYBRID_PREFIX}{question_id}:{session_index}"
    if r.exists(hybrid_key):
        return key, "exists"

    s3_key = f"{S3_PREFIX}/{question_id}/{session_index}.txt"
    s3.put_object(
        Bucket=BUCKET,
        Key=s3_key,
        Body=text,
        ContentType="text/plain",
    )

    r.hset(hybrid_key, mapping={
        "embedding": embed,
        "s3_key": s3_key,
        "question_id": question_id,
        "session_index": session_index,
        "msg_count": msg_count,
    })
    return key, "ok"


def main():
    parser = argparse.ArgumentParser(description="Migrate text from Redis to S3, create hybrid keys")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=cfg.memorydb.port)
    parser.add_argument("--password", default=cfg.memorydb.password)
    parser.add_argument("--batch", type=int, default=500)
    parser.add_argument("--workers", type=int, default=NUM_WORKERS)
    args = parser.parse_args()

    print("=== Hybrid load: Redis text → S3 ===")
    print()
    print("Connecting...")
    r = connect(args.host, args.port, args.password)
    total = r.dbsize()
    print(f"  Total keys: {total}")

    print("Creating hybrid index (idempotent)...")
    try:
        r.execute_command(
            "FT.CREATE", "idx_hybrid",
            "ON", "HASH", "PREFIX", 1, HYBRID_PREFIX,
            "SCHEMA",
            "embedding", "VECTOR", "HNSW", 6, "TYPE", "FLOAT32", "DIM", 384, "DISTANCE_METRIC", "COSINE",
            "s3_key", "TAG",
            "question_id", "TAG",
            "session_index", "NUMERIC",
            "msg_count", "NUMERIC",
        )
        print("  Created idx_hybrid")
    except redis.ResponseError as e:
        if "already exists" in str(e):
            print("  idx_hybrid already exists")
        else:
            raise

    print()
    print(f"Migrating with {args.workers} workers...")
    print()

    cursor = 0
    ok_count = 0
    skip_count = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while True:
            cursor, keys = r.scan(cursor=cursor, count=args.batch)
            if not keys:
                if cursor == 0:
                    break
                continue

            futures = {pool.submit(migrate_key, k): k for k in keys if not k.decode().startswith(HYBRID_PREFIX)}
            for f in as_completed(futures):
                result = f.result()
                if result[1] == "ok":
                    ok_count += 1
                elif result[1] == "exists":
                    skip_count += 1

            if cursor == 0:
                break

            total_done = ok_count + skip_count
            if total_done % 5000 == 0:
                elapsed = time.time() - start
                rate = total_done / elapsed if elapsed > 0 else 0
                eta = (total - total_done) / rate if rate > 0 else 0
                print(f"  {total_done:>7d} / {total}  ({rate:.0f} keys/s, ETA {eta:.0f}s, new={ok_count}, exists={skip_count})")

    elapsed = time.time() - start
    total_done = ok_count + skip_count
    print()
    print(f"Done: {total_done} keys in {elapsed:.1f}s ({total_done/elapsed:.0f} keys/s)")
    print(f"  New: {ok_count}  Already existed: {skip_count}")


if __name__ == "__main__":
    main()
