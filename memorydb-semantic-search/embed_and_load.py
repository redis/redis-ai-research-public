import time
import ijson
import numpy as np
import redis
from sentence_transformers import SentenceTransformer

from config import cfg

DATA_PATH = "longmemeval_m_cleaned.json"
REDIS_HOST = cfg.memorydb.host
REDIS_PORT = cfg.memorydb.port
MODEL_NAME = "BAAI/bge-small-en-v1.5"
BATCH = 256
MAX_TOKENS = 512
OVERLAP = 64


def chunk_text(text):
    words = text.split()
    if len(words) <= MAX_TOKENS:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + MAX_TOKENS, len(words))
        chunks.append(" ".join(words[start:end]))
        start += MAX_TOKENS - OVERLAP
    return chunks


def flatten(item):
    docs = []
    for idx, session in enumerate(item["haystack_sessions"]):
        msgs = [m["role"] + ": " + m["content"] for m in session]
        text = "\n\n".join(msgs)
        docs.append({
            "id": "{session}:" + item["question_id"] + ":" + str(idx),
            "question_id": item["question_id"],
            "session_index": idx,
            "text": text,
            "msg_count": len(session),
            "chunks": chunk_text(text),
        })
    return docs


def main():
    print("Loading model on GPU...", flush=True)
    t0 = time.time()
    model = SentenceTransformer(MODEL_NAME, device="cuda")
    model.max_seq_length = MAX_TOKENS
    print(f"Model loaded in {time.time()-t0:.1f}s (dim={model.get_sentence_embedding_dimension()})", flush=True)

    print("Connecting to Redis...", flush=True)
    r = redis.Redis(
        host=REDIS_HOST, port=REDIS_PORT,
        ssl=True, ssl_cert_reqs=None, decode_responses=False,
    )
    r.ping()
    print("Connected OK", flush=True)

    print("Scanning existing keys...", flush=True)
    existing = set()
    cursor = 0
    while True:
        cursor, keys = r.scan(cursor=cursor, match="{session}:*", count=5000)
        existing.update(k.decode() for k in keys)
        if cursor == 0:
            break
    print(f"Found {len(existing)} existing keys", flush=True)

    pipe = r.pipeline()
    total = len(existing)
    item_no = 0
    skipped_items = 0
    t_start = time.time()

    with open(DATA_PATH, "rb") as f:
        for item in ijson.items(f, "item"):
            item_no += 1
            docs = flatten(item)

            new_docs = [d for d in docs if d["id"] not in existing]
            if not new_docs:
                skipped_items += 1
                continue

            single_chunk = [d for d in new_docs if len(d["chunks"]) == 1]
            multi_chunk = [d for d in new_docs if len(d["chunks"]) > 1]

            if single_chunk:
                texts = [d["chunks"][0] for d in single_chunk]
                embs = model.encode(texts, batch_size=BATCH, normalize_embeddings=True, show_progress_bar=False)
                for d, e in zip(single_chunk, embs):
                    pipe.hset(d["id"], mapping={
                        "text": d["text"],
                        "embedding": e.astype(np.float32).tobytes(),
                        "question_id": d["question_id"],
                        "session_index": d["session_index"],
                        "msg_count": d["msg_count"],
                    })

            for d in multi_chunk:
                chunk_embs = model.encode(d["chunks"], batch_size=BATCH, normalize_embeddings=True, show_progress_bar=False)
                emb = np.mean(chunk_embs, axis=0).astype(np.float32)
                emb = emb / np.linalg.norm(emb)
                pipe.hset(d["id"], mapping={
                    "text": d["text"],
                    "embedding": emb.tobytes(),
                    "question_id": d["question_id"],
                    "session_index": d["session_index"],
                    "msg_count": d["msg_count"],
                })

            pipe.execute()
            total += len(new_docs)
            elapsed = time.time() - t_start
            rate = total / elapsed
            eta = (237655 - total) / rate if rate else 0
            print(f"  Item {item_no}/500 | {total} sessions | {rate:.0f}/s | ETA: {eta//60:.0f}m{eta%60:.0f}s", flush=True)

    print(f"\nDone. {total} sessions in {time.time()-t_start:.0f}s", flush=True)
    print(f"Skipped {skipped_items} fully-loaded items", flush=True)
    print(f"Redis keys: {r.dbsize()}", flush=True)


if __name__ == "__main__":
    main()
