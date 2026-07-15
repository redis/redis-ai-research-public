# LongMemEval agent-memory blog

A self-contained, dependency-free technical blog based on the June 2026
LongMemEval evaluation write-up and the corresponding raw experiment outputs.

## Preview locally

The charts load `data/results.json`, so serve the directory over HTTP:

```bash
cd docs/longmemeval-agent-memory
python3 -m http.server 8080
```

Open <http://localhost:8080>.

## GitHub Pages location

This repository publishes `main:/docs` through GitHub Pages. The folder is
already static output—there is no build step. After these changes are reviewed,
committed, and pushed, the article will be available at:

<https://redis.github.io/redis-ai-research-public/longmemeval-agent-memory/>

## Source provenance

Primary narrative source:

- `/Users/iliya.zhechev/Downloads/longmemeval_writeup (1).pdf`

Evaluation repository:

- `/Users/iliya.zhechev/workspace/ai-research-semantic-cache/experiments/ams-eval`

Key evidence used:

- `RESULTS.md` — May 18 leaderboard, cost assumptions, and caveats
- `scripts/holistic_session_cost.py` — the `ingest + 5.13 × query` cost model
- `presentations/2026-05-26-remis-instruct/slides.md` — strategy descriptions and best configuration
- `experiment_results/20260714-remis-instruct-full-gpt4o-small-complete/official_metrics.json` — complete 500-question aggregation: 86.14% task-averaged, 85.0% overall
- `experiment_results/20260409-0718-remis-instruct-full-modelgpt-4o-extractionmodelgpt-4o-existinglimit20-searchlimit40-hybridtrue-topk15-contextwindow0-reranktopk0/official_metrics.json` — official per-task aggregation for the 85.7% configuration

The publishable chart dataset is in `data/results.json`.

## Editorial and data choices

- **86.1% is identified as task-averaged accuracy.** The complete aggregate
  records 86.14% task-averaged and 85.0% sample-weighted accuracy (425/500).
- **Published-only and reproduced results are visually distinct.** Published
  values are context, not independently verified evidence.
- **Oracle is explicitly qualified.** Its reproduced 86.0% run used
  `gpt-5.5 xhigh`, not the common `gpt-4o` answer backbone.
- **Cost is described as an estimate of visible LLM spend**, not total cost of
  ownership. Lower bounds are marked when server-side ingest spend is hidden.
- **The per-question-type figure from the PDF is intentionally omitted.** Its
  bars and 61.5% legend reconcile to the fixed-chunk RAG-mem baseline, while
  the figure labels that series “Remis”; elsewhere the same document reports
  Remis at 83.4%. Publishing that chart without the underlying corrected
  aggregation would be misleading.
- **Raw-result mismatches are disclosed in the article.** The retained
  AgentCore backing run uses the oracle split rather than small; the matching
  RAG-mem run is partial (412/500); and no retained raw run was found that
  reproduces the write-up's 83.4% Remis figure.
- **Exact source revision is not claimed.** Flagship metadata records the git
  hash and branch as `unknown`.
- **LongMemEval-V2 results are intentionally excluded.** The July trajectory
  runs use a different dataset, adapter, judge, and metric schema; mixing them
  into the original chat-session LongMemEval story would be misleading.

## Files

- `index.html` — article structure and copy
- `styles.css` — responsive editorial design
- `script.js` — dependency-free accessible SVG charts
- `data/results.json` — chart data and annotations
- `assets/social-card.svg` — 1200×630 Open Graph image
