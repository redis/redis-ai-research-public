# Embedding Generation for LongMemEval

## Overview

Generate 384-dim embeddings for ~238k chat sessions from the LongMemEval Medium dataset and store them in AWS MemoryDB (Redis-compatible). Uses `BAAI/bge-small-en-v1.5` on a GPU instance for batch encoding.

## Pipeline Summary

```
JSON (2.5 GB, 500 items)  →  ijson streaming  →  chunk text (512 token windows, 64 overlap)
→  encode with BAAI/bge-small-en-v1.5  →  mean-pool chunks  →  L2 normalize  →  HSET into MemoryDB
```

## Prerequisites

### Infrastructure

| Resource | Spec | Managed By |
|---|---|---|
| **MemoryDB cluster** | `db.r7g.large` (13.07 GiB), Valkey 7.2, TLS enabled, 1 shard | Terraform (`terraform/main.tf`) |
| **Bastion** | `t4g.nano`, public subnet, SSH port-forward to MemoryDB | Terraform |
| **GPU instance** | `g4dn.xlarge` (Tesla T4, 15 GiB VRAM, 16 GiB RAM), 100 GiB gp3 | Terraform (`aws_instance.gpu`) |

### EC2 GPU Setup (Amazon Linux 2023)

```bash
# SSH
ssh -i "$BASTION_SSH_KEY" ec2-user@<GPU_PUBLIC_IP>

# NVIDIA driver is pre-installed on the AL2023 ECS-optimized AMI
# Verify:
nvidia-smi

# Install Python venv + packages
sudo yum install -y python3-pip
python3 -m venv ~/venv
source ~/venv/bin/activate
pip install --upgrade pip
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
pip install sentence-transformers==5.1.2 redis[hiredis]==7.0.1 numpy==2.0.2 ijson==3.5.0

# Copy dataset (2.5 GB)
# Download from Hugging Face:
# https://huggingface.co/datasets/experilabs/LongMemEval
scp -i "$BASTION_SSH_KEY" data/longmemeval_m_cleaned.json ec2-user@<GPU_PUBLIC_IP>:~/
```

## Script: `embed_and_load.py`

### Key Parameters

| Parameter | Value | Description |
|---|---|---|
| Model | `BAAI/bge-small-en-v1.5` | 384-dim, 512 token limit |
| Batch size | 256 | Sessions per encode call |
| Max tokens | 512 | Word-based sliding window |
| Overlap | 64 | Token overlap between chunks |
| Redis key pattern | `{session}:{question_id}:{index}` | Hash tag `{session}` for cluster-mode pipeline compliance |
| Redis HSET fields | `text`, `embedding`, `question_id`, `session_index`, `msg_count` | |
| Distance metric | Cosine similarity (via L2 normalization) | |

### Embedding Strategy

1. **Session flattening**: Each `haystack_sessions` item contains multiple sessions. Each session is flattened to a string with `user:/assistant:` prefixes, messages joined by `\n\n`.

2. **Text chunking** (word-based):
   - If words ≤ 512 → single chunk, encode directly
   - If words > 512 → sliding window of 512 words, stride 448 (64 overlap)
   - Each chunk is encoded with `normalize_embeddings=True`
   - Chunk embeddings are mean-pooled, then L2-normalized to unit length

   ```python
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
   ```

3. **Encoding**:
   - Single-chunk: `model.encode(text, normalize_embeddings=True)` → unit vector
   - Multi-chunk: `model.encode(chunks)` → `np.mean(axis=0)` → `/ np.linalg.norm()`
   - All embeddings stored as binary `float32` bytes via `emb.astype(np.float32).tobytes()`

4. **Redis pipeline**: All sessions for one item (~475 avg) are encoded and HSET via a single `pipeline.execute()` call.

5. **Checkpointing**: On restart, scans Redis for existing `{session}:*` keys and skips already-loaded items. Only encodes and stores new sessions.

### Running

```bash
source ~/venv/bin/activate
nohup python ~/embed_and_load.py > embed_output.log 2>&1 &
tail -f embed_output.log
```

Expected throughput: ~22 sessions/second (Tesla T4). Full run: ~3 hours.

### Data Format

Each Redis key is a HSET:

```
{session}:<question_id>:<session_index>
  ├── text:        string (full session text)
  ├── embedding:   bytes (1536 bytes = 384 × float32)
  ├── question_id: string
  ├── session_index: integer
  └── msg_count:   integer
```

Total keys: ~237,665 (237,655 sessions + index metadata).

### Verification

```bash
# Via bastion port-forward:
redis-cli -p 6379 --tls --insecure DBSIZE
redis-cli -p 6379 --tls --insecure RANDOMKEY
redis-cli -p 6379 --tls --insecure HGETALL {session}:<random_key>
```

## Search Approach

MemoryDB supports vector search via the `default.memorydb-valkey7.search` parameter group. Semantic search is performed server-side:

1. Create a vector index:
   ```bash
   FT.CREATE idx ON HASH PREFIX 1 {session}: \
     SCHEMA text TEXT \
     embedding VECTOR HNSW 6 TYPE FLOAT32 DIM 384 DISTANCE_METRIC COSINE
   ```
2. Search via `FT.SEARCH`:
   ```bash
   FT.SEARCH idx "*=>[KNN <k> @embedding $vec EF_RUNTIME 200 AS score]" \
     PARAMS 2 vec <binary> DIALECT 2
   ```

See [search.py](search.py) for the Python implementation using `execute_command`.

## Package Versions (verified)

| Package | Version |
|---|---|
| Python | 3.9.25 |
| PyTorch | 2.6.0+cu124 |
| sentence-transformers | 5.1.2 |
| redis | 7.0.1 |
| numpy | 2.0.2 |
| ijson | 3.5.0 |
| CUDA | 12.4 |
| NVIDIA driver | 595.71.05 |

## Cost Notes

- `g4dn.xlarge` (GPU): ~$0.526/hr — terminate when not in use
- `db.r7g.large` (MemoryDB): ~$0.175/hr
- `t4g.nano` (bastion): ~$0.0042/hr
