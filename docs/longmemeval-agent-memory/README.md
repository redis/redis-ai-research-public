# LongMemEval agent-memory blog

A self-contained, dependency-free technical blog based on the June–July 2026
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
- `experiment_results/20260714-remis-instruct-full-gpt4o-small-complete/official_metrics.json` — complete 500-question aggregation: 86.14% task-averaged
- `experiment_results/20260409-0718-remis-instruct-full-modelgpt-4o-extractionmodelgpt-4o-existinglimit20-searchlimit40-hybridtrue-topk15-contextwindow0-reranktopk0/official_metrics.json` — official per-task aggregation for the 85.7% configuration
- `blog_handoff_extraction_eval.md` — extraction-model study methodology, results, and publishing caveats
- `make_extraction_figure.py` — source data and plotting logic for the extraction price/quality figure
- `https://arxiv.org/abs/2607.16848` — independent scientific-memory evaluation
  cited for its retrieval-budget and sparse–dense retrieval findings
- `https://omegamax.co/blog/number-one-on-longmemeval` and
  `https://github.com/omega-memory/omega-memory` — OMEGA’s published claim,
  implementation, and evaluation code
- `https://arxiv.org/abs/2603.16862` — PwC Chronos architecture, settings, and
  category-level results

The publishable chart dataset is in `data/results.json`.

## Editorial and data choices

- **86.1% is identified as task-averaged accuracy.** The complete aggregate
  records 86.14% task-averaged accuracy, with 425/500 answers judged correct.
- **Published-only and reproduced results are visually distinct.** Published
  values are context, not independently verified evidence.
- **Higher published figures remain visible with direct qualifications.**
  OMEGA’s 95.4% is a self-published, benchmark-tuned GPT-4.1 run without a
  retained public output artifact that we could locate. Chronos uses a broader
  agentic query path whose cost and latency are not reported.
- **Chronos is converted to the chart’s metric.** Its paper reports 92.6%
  sample-weighted overall accuracy. The chart shows 92.07%, calculated as the
  mean of the six published category accuracies, so it is comparable at the
  metric level with the other task-averaged rows.
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
  reproduces the 83.4% Remis figure used in the comparison.
- **Exact source revision is not claimed.** Flagship metadata records the git
  hash and branch as `unknown`.
- **LongMemEval-V2 results are intentionally excluded.** The July trajectory
  runs use a different dataset, adapter, judge, and metric schema; mixing them
  into the original chat-session LongMemEval story would be misleading.
- **The scientific-memory preprint provides converging design evidence.** Its
  findings on sparse–dense retrieval, raw-text preservation, and retrieval-budget
  control support core choices in our approach. The article also states that its
  task is scientific-paper retrieval rather than conversational memory.
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
- `assets/social-card.svg` — 1200×630 Open Graph image
- `assets/extractor-price-quality.svg` — extraction-model accuracy/cost figure
