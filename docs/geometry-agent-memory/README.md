# The Geometry of Agent Memory

A static research article based on the unpublished geometric failure-forecasting
study in `experiments/ams-eval/wip.ignore`.

## Publication status

This article is a draft. It is intentionally not linked from the public site
homepage because the source paper is being considered for a NeurIPS 2026
workshop. Confirm publication timing and venue rules before making it public.

## Preview

From the repository root:

```bash
python3 -m http.server 8080 --directory docs
```

Open <http://localhost:8080/geometry-agent-memory/>.

## Primary sources

Read in this order:

1. `wip.ignore/draft.md` — canonical claims and numbers
2. `wip.ignore/README.md` — methodology and figure map
3. `wip.ignore/outline.md` — framing
4. `wip.ignore/notes.md` — decisions and caveats

The source paths are in the separate local evaluation repository:

`/Users/iliya.zhechev/workspace/ai-research-semantic-cache/experiments/ams-eval`

## Editorial choices

- The pooled 0.689 ROC-AUC is always paired with the approximately 0.63
  single-system result.
- Question difficulty at 0.678 is presented as a strong baseline, not dismissed.
- The dense-versus-hybrid retrieval explanation is reported as a negative result.
- Closest-memory selection is described as matching, not beating, the tuned
  native retriever.
- t-SNE is identified as illustrative; metric claims use original-space cosine
  distances.
- The article does not claim to replace end-to-end evaluation or rank systems.
