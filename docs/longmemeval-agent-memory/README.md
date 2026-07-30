# LongMemEval agent-memory blog

A self-contained, dependency-free technical blog based on the June 2026
LongMemEval evaluations and the corresponding raw experiment outputs.

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
- `experiment_results/20260407-0658-remis-instruct-full-gpt4o-small/` and
  `experiment_results/20260714-remis-instruct-full-gpt4o-small-complete/` —
  original 489-question run plus the documented 11-question completion:
  86.14% task-averaged across all 500 questions
- `experiment_results/instruct-permsg-ingest-gpt4o-existinglimit20-k40/official_metrics.json` — complete 500-question Small-split Instruct aggregation: 71.24% task-averaged
- `blog_handoff_extraction_eval.md` — extraction-model study methodology, results, and publishing caveats
- `make_extraction_figure.py` — source data and plotting logic for the extraction price/quality figure
- `https://arxiv.org/abs/2607.16848` — independent scientific-memory evaluation
  cited for its retrieval-budget and sparse–dense retrieval findings
The publishable chart dataset is in `data/results.json`.
The evaluation repository and raw run artifacts referenced above are not
currently public. `data/results.json` is a curated publication dataset, not a
substitute for per-question predictions and judgments.

## Editorial and data choices

- **86.1% is identified as task-averaged accuracy.** The completed aggregate
  combines 489 original answers with 11 recovered answers, recording 86.14%
  task-averaged and 85.0% question-level accuracy across all 500 questions.
- **The Instruct comparison uses a complete Small-split run.** It scored
  71.24% task-averaged across the same 500 questions.
- **Standalone Remis accuracy is omitted.** No retained complete Small-split
  run supports the historical 83.4% estimate.
- **The leaderboard separates measured and published results.** Redis Agent
  Memory runs are red, third-party systems measured in our evaluation are
  orange, and published-only figures are gray. Oracle is retained as qualified
  context because its 479-question run used a materially different answer model.
- **Per-task results compare complete runs only.** Instruct and
  Remis + Instruct each cover the same 500 questions and six task types.
- **The cost chart distinguishes measurement quality.** Every point comes from
  our evaluation runs. Redis and Mastra OM use complete Small-split runs; gray
  points use a partial run or incomplete cost capture. Cost
  is normalized per million LongMemEval Small conversation tokens and assumes
  one memory query per user turn. It remains an estimate of visible LLM spend,
  not total cost of ownership.
- **The per-question-type figure from the PDF is intentionally omitted.** Its
  bars and 61.5% legend reconcile to the fixed-chunk RAG-mem baseline, while
  the figure labels that series “Remis”; elsewhere the same document reports
  Remis at 83.4%. Publishing that chart without the underlying corrected
  aggregation would be misleading.
- **Rows without comparable backing are omitted from public comparisons.**
  The retained AgentCore run uses the oracle split; the matching RAG-mem run is
  partial (412/500); and no retained raw run reproduces the 83.4% Remis estimate.
- **Exact source revision is not claimed.** Flagship metadata records the git
  hash and branch as `unknown`.
- **LongMemEval-V2 results are intentionally excluded.** The July trajectory
  runs use a different dataset, adapter, judge, and metric schema; mixing them
  into the original chat-session LongMemEval story would be misleading.
- **The scientific-memory preprint provides converging design evidence.** Adding
  BM25 to dense retrieval was its largest measured intervention, and its three
  sparse–dense hybrids were effectively tied at the top under a matched budget.
  The article maps that result to the dense + BM25 Remis path while stating that
  scientific retrieval does not validate this conversational-memory evaluation.
- **The combined-strategy interpretation is budget-qualified.** The evaluation
  did not equalize retrieved-token budgets across strategies, so it cannot
  separate representational complementarity from the benefit of additional
  retrieved evidence.
- **The extraction study keeps its metric distinct.** Its chart reports
  question-level accuracy, while the article's main result reports task-averaged
  accuracy. The mixed sample sizes (`n=100` for mini and gpt-5; `n=500` for
  nano and gpt-4o) and single-run cells are stated alongside the chart.
- **Extraction costs use verified model rates.** The source prices for gpt-5,
  gpt-5-mini, gpt-5-nano, gpt-4o, and `text-embedding-3-small` were checked
  against the OpenAI model documentation in July 2026.

## Files

- `index.html` — article structure and copy
- `styles.css` — responsive editorial design
- `script.js` — dependency-free accessible SVG charts
- `data/results.json` — chart data and annotations
- `assets/social-card.svg` — editable 1200×630 social-card source
- `assets/social-card.png` — rasterized Open Graph image for social crawlers
- `assets/extractor-price-quality.svg` — extraction-model accuracy/cost figure
