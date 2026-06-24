# Redis AI Research

Public-facing projects, experiments, and reference implementations from the **Redis AI Research team**.

This monorepo collects open-source work exploring how Redis can be applied to modern AI workloads — semantic caching, vector search, retrieval-augmented generation (RAG), agentic systems, and beyond.

## What you'll find here

Each top-level directory is a self-contained project with its own README, dependencies, and setup instructions. Projects generally fall into one of these areas:

- **Semantic & vector search** — using Redis as a vector store for embeddings, similarity search, and hybrid retrieval.
- **Caching for LLM applications** — semantic caching, result reuse, and latency optimization patterns.
- **Agentic systems** — multi-agent orchestration, learning agents, and memory architectures backed by Redis.
- **RAG pipelines** — reference implementations and benchmarks for retrieval-augmented generation.
- **Benchmarks & evaluations** — performance studies and methodology for AI workloads on Redis.

## Repository layout

```
.
├── learning-agents/            # multi-agent NL → pandas analysis with Redis-backed
│                               # semantic cache and learned-guidance memory
├── memorydb-semantic-search/   # vector search over ~238k LongMemEval sessions on
│                               # AWS MemoryDB (pure-Redis vs. hybrid+S3 vs. hybrid+EBS)
└── ...
```

Projects are added incrementally. Browse the top-level directories to see what's currently available.

### Current projects

- **[learning-agents](learning-agents/)** — A multi-agent system that turns natural-language questions into executable pandas code over a CSV/JSON dataset. Uses Redis vector indices for a semantic result cache and a persistent "guidance" memory that learns from past errors to improve future retries.
- **[memorydb-semantic-search](memorydb-semantic-search/)** — Semantic search over ~238k LongMemEval chat sessions on AWS MemoryDB (Valkey 7.2 with search). Benchmarks three backend approaches — pure Redis, hybrid+S3, and hybrid+EBS — with Terraform for the full AWS stack (MemoryDB cluster, bastion, S3, VPC endpoint) and a FastAPI server exposing the search APIs.

## Getting started

1. Clone the repository:
   ```bash
   git clone https://github.com/redis/redis-ai-research-public.git
   cd redis-ai-research-public
   ```
2. Pick a project directory and follow its own README for setup and usage. Most projects assume a running Redis instance (Redis Stack or Redis 8+ with the search/vector modules).

## Prerequisites (common)

- Redis Stack or Redis 8+ (for vector search and JSON support)
- Python 3.11+ (most projects)
- An API key for the LLM provider used by the project (typically OpenAI, Anthropic, or Google)

Check each project's README for exact requirements.

## Contributing

Contributions, issues, and discussion are welcome. Open an issue to propose a new experiment, report a bug, or ask a question. Pull requests should target the relevant project directory and include updates to that project's README where appropriate.

## License

This repository is released under the [MIT License](LICENSE). See the `LICENSE` file for details.

## Contact

For questions about the AI Research team's work, open an issue in this repository.
