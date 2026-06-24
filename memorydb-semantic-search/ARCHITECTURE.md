# Architecture

## Pure Redis

```mermaid
sequenceDiagram
    participant U as User
    participant API as API Server
    participant R as MemoryDB
    U->>API: POST /search_pure (query, k=10)
    API->>API: Encode query (~70ms)
    API->>R: FT.SEARCH idx (HNSW)
    R-->>API: embedding + full text (34 KB/key)
    API-->>U: Results + timing (76ms p50)
```

**Redis key:** `{session}:{question_id}:{session_index}` → `{embedding, text, ...}`

---

## Hybrid Redis + S3

```mermaid
sequenceDiagram
    participant U as User
    participant API as API Server
    participant R as MemoryDB
    participant S3 as S3 Bucket
    U->>API: POST /search (query, k=10)
    API->>API: Encode query (~70ms)
    API->>R: FT.SEARCH idx_hybrid (HNSW)
    R-->>API: embedding + s3_key (2.7 KB/key)
    par Fetch 10 S3 objects (pool of 8)
        API->>S3: GET session1.txt
        API->>S3: GET session2.txt
        API->>S3: GET session3.txt
        API->>S3: GET session4.txt
        API->>S3: GET session5.txt
        API->>S3: GET session6.txt
        API->>S3: GET session7.txt
        API->>S3: GET session8.txt
        API->>S3: GET session9.txt
        API->>S3: GET session10.txt
    end
    S3-->>API: text (~30 KB each)
    API-->>U: Results + timing (189ms p50)
```

**Redis key:** `{hybrid}:{question_id}:{session_index}` → `{embedding, s3_key, ...}`
**S3 key:** `sessions/{question_id}/{session_index}.txt`

---

## Hybrid Redis + EBS Local Disk

```mermaid
sequenceDiagram
    participant U as User
    participant API as API Server
    participant R as MemoryDB
    participant Disk as EBS (/data)
    U->>API: POST /search_disk (query, k=10)
    API->>API: Encode query (~70ms)
    API->>R: FT.SEARCH idx_hybrid (HNSW)
    R-->>API: embedding + s3_key (2.7 KB/key)
    par Read 10 files from disk (pool of 8)
        API->>Disk: open /data/sessions/1.txt
        API->>Disk: open /data/sessions/2.txt
        API->>Disk: open /data/sessions/3.txt
        API->>Disk: open /data/sessions/4.txt
        API->>Disk: open /data/sessions/5.txt
        API->>Disk: open /data/sessions/6.txt
        API->>Disk: open /data/sessions/7.txt
        API->>Disk: open /data/sessions/8.txt
        API->>Disk: open /data/sessions/9.txt
        API->>Disk: open /data/sessions/10.txt
    end
    Disk-->>API: text (~30 KB each)
    API-->>U: Results + timing (78ms p50)
```

**Same index as Hybrid+S3** (`idx_hybrid`), same key structure. Only the text fetch path differs.

---

## Comparison

| | Pure Redis | Hybrid S3 | Hybrid EBS |
|---|---|---|---|
| **Redis** | 7.78 GB, r7g.large ($245/mo) | 0.62 GB, r6g.large ($191/mo) | 0.62 GB, r6g.large ($191/mo) |
| **Extra storage** | none | ~7 GB S3 ($0.14/mo) | 10 GB gp3 ($1/mo) |
| **Latency p50** | 76ms | 189ms | 78ms |
| **Std dev** | 7.7ms | 209ms | 7.2ms |
| **Hops** | 1 (Redis) | 2 (Redis → S3) | 2 (Redis → local file) |
