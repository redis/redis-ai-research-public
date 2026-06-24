import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import boto3
import numpy as np
import redis
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

from config import cfg

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MEMORYDB_HOST = cfg.memorydb.host
MEMORYDB_PORT = cfg.memorydb.port
MEMORYDB_PASSWORD = cfg.memorydb.password
BUCKET = cfg.s3.bucket
POOL_SIZE = cfg.server.pool_size

_tls = threading.local()
r = None
model = None
lock = None
pool = None


def _s3():
    if not hasattr(_tls, "s3"):
        _tls.s3 = boto3.client(
            "s3",
            region_name=cfg.aws.region,
            endpoint_url=f"https://s3.{cfg.aws.region}.amazonaws.com",
        )
    return _tls.s3


class SearchRequest(BaseModel):
    query: str
    k: int = 10


class SearchResult(BaseModel):
    rank: int
    score: float
    s3_key: str
    text: str


class SearchResponse(BaseModel):
    results: list[SearchResult]
    timing: dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    global r, model, lock, pool
    print("Initializing...", flush=True)
    t0 = time.time()
    kwargs = dict(host=MEMORYDB_HOST, port=MEMORYDB_PORT, ssl=True, ssl_cert_reqs=None, decode_responses=False)
    if MEMORYDB_PASSWORD:
        kwargs["password"] = MEMORYDB_PASSWORD
    r = redis.Redis(**kwargs)
    r.ping()
    print(f"  Connected to MemoryDB", flush=True)
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    model.max_seq_length = 512
    print(f"  Model loaded in {time.time()-t0:.1f}s", flush=True)
    lock = threading.Lock()
    pool = ThreadPoolExecutor(max_workers=POOL_SIZE)
    print(f"  Ready in {time.time()-t0:.1f}s", flush=True)
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
def search(req: SearchRequest):
    return _run_search("idx_hybrid", req, text_source="s3")


@app.post("/search_pure", response_model=SearchResponse)
def search_pure(req: SearchRequest):
    return _run_search("idx", req, text_source="redis")


@app.post("/search_disk", response_model=SearchResponse)
def search_disk(req: SearchRequest):
    return _run_search("idx_hybrid", req, text_source="disk")


def _run_search(index_name: str, req: SearchRequest, text_source: str):
    t_start = time.time()
    with lock:
        q_emb = model.encode(req.query, normalize_embeddings=True).astype(np.float32)
    encode_time = time.time() - t_start

    t1 = time.time()
    raw = r.execute_command(
        "FT.SEARCH", index_name,
        f"*=>[KNN {req.k} @embedding $vec EF_RUNTIME 200 AS score]",
        "PARAMS", 2, "vec", q_emb.tobytes(),
        "DIALECT", 2,
    )
    search_time = time.time() - t1

    num = raw[0] if raw else 0
    s3_keys = []
    for i in range(num):
        fields = raw[1 + i * 2 + 1]
        doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
        s3_keys.append(doc.get("s3_key", b"").decode())

    fetch_time = 0
    texts = []

    if text_source == "redis":
        for i in range(num):
            fields = raw[1 + i * 2 + 1]
            doc = {fields[j].decode(): fields[j + 1] for j in range(0, len(fields), 2)}
            texts.append(doc.get("text", b"").decode(errors="replace")[:500])

    elif text_source == "s3":
        t2 = time.time()
        texts = list(pool.map(_fetch_s3, s3_keys))
        fetch_time = time.time() - t2

    elif text_source == "disk":
        t2 = time.time()
        texts = list(pool.map(_read_disk, s3_keys))
        fetch_time = time.time() - t2

    results = []
    for i, sk in enumerate(s3_keys):
        raw_fields = raw[1 + i * 2 + 1]
        doc = {raw_fields[j].decode(): raw_fields[j + 1] for j in range(0, len(raw_fields), 2)}
        score = float(doc.get("score", b"0"))
        results.append(SearchResult(rank=i + 1, score=score, s3_key=sk, text=texts[i] if i < len(texts) else ""))

    total = time.time() - t_start
    return SearchResponse(
        results=results,
        timing={
            "encode_ms": round(encode_time * 1000, 1),
            "search_ms": round(search_time * 1000, 1),
            "fetch_ms": round(fetch_time * 1000, 1),
            "total_ms": round(total * 1000, 1),
        },
    )


def _fetch_s3(s3_key):
    try:
        obj = _s3().get_object(Bucket=BUCKET, Key=s3_key)
        return obj["Body"].read().decode(errors="replace")
    except Exception:
        return ""


def _read_disk(s3_key):
    path = f"/data/{s3_key}"
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


if __name__ == "__main__":
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port)
