---
marp: true
theme: rqe-dark
paginate: true
size: 16:9
title: "Spec-Driven Agents for RQE: Specs, Tooling, and Trajectory-Based Evaluation"
---

<!-- _class: title -->
<!-- _paginate: false -->

# <span class="accent">Spec-Driven Agents</span> for RQE:<br>Specs, Tooling, and<br>Trajectory-Based Evaluation

<p class="subtitle">Evaluation-led agent design on the Redis Query Engine</p>

<p class="authors">Srijith Rajamohan, Ph.D. · Yash Mandilwar · Chris Coleman · Itamar Haber · Adriano Amaral<br><span class="muted small">Redis</span></p>

---

###### The Problem
# Reliability is the bottleneck between demo and production

Production means, every time:

- The **right tools**, in the **correct order**
- **All the time**

<div class="callout">Answers must be <b>correct, complete, and useful</b> — ideally also <b>efficient</b> — and all of it <i>measurable</i>.</div>

---

###### Case Study
# Troubleshooting the Redis Query Engine

<div class="cols">
<div>

An agent that diagnoses RQE (RediSearch) issues: slow queries, indexing failures, memory pressure, stale results, vector search problems.

- Interprets raw diagnostics: `FT.PROFILE`, `FT.INFO`, `SLOWLOG`, shard metrics
- Staged workflow: Clarification → Diagnosis → Confirmation → Solution
- Must ground every conclusion in collected evidence

</div>
<div class="card">
<h3>Why this domain is hard for agents</h3>

- Unlike SQL, parametric knowledge is often incorrect
- Very context dependent — needs to know what information is missing from a question
- Analysis may not catch all issues (completeness)
- Answers can be non-useful

</div>
</div>

---

###### Observed Failure Modes
# How agents actually go wrong

<span class="accent"><b>CORRECTNESS</b></span>

<div class="cols">
<div class="card"><h3>Tool &amp; file-read omission</h3><p>Skips a required tool call, misses a file entirely, or reads it partially — the answer looks complete but is missing evidence.</p></div>
<div class="card"><h3>Hallucination &amp; misuse of information</h3><p>Guesses paths or arguments that don't exist, forgets evidence already collected, or reasons illogically from what it has.</p></div>
</div>

<span class="accent"><b>EFFICIENCY</b></span>

<div class="cols">
<div class="card"><h3>Inefficient paths</h3><p>Exploratory flailing: repeated reads of the same document, backtracking, dead-end searches.</p></div>
</div>

<div class="callout">None of these show up in an end-to-end "did it answer?" eval. They only show up in the <b>trajectory</b>.</div>

---

###### Approach 1/2
# Spec-driven design & tool granularity

**Hierarchical specs** + deliberately shaped tools — not a monolithic prompt:

<div class="cols3">
<div class="card"><h3>Specs encode context &amp; tool use</h3><p>What context matters · which tools, how, in what order · required inputs declared.</p></div>
<div class="card"><h3>Right-sized tools</h3><p>Transforms belong in tools, not the context window: <code>analyse_ft_profile</code> → compact summary + outliers.</p></div>
<div class="card"><h3>Setup vs. discretionary tools</h3><p><b>Setup</b>: always run — <code>ft_info</code> before any query. <b>Discretionary</b>: invoked by model judgment as context requires.</p></div>
</div>

---

###### Architecture — Baseline
# A standard spec-driven agent

<svg viewBox="0 0 1000 400" style="width:100%;max-height:56vh" xmlns="http://www.w3.org/2000/svg">
  <defs><marker id="ar0" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="none" stroke="#ff4438" stroke-width="1.5"/></marker></defs>
  <rect x="20" y="160" width="150" height="70" rx="10" fill="#161b22" stroke="#2d333b"/>
  <text x="95" y="190" text-anchor="middle" fill="#e6edf3" font-size="17" font-weight="600">User</text>
  <text x="95" y="212" text-anchor="middle" fill="#8b949e" font-size="12">symptom description</text>
  <rect x="240" y="130" width="190" height="130" rx="10" fill="#161b22" stroke="#ff4438" stroke-width="1.5"/>
  <text x="335" y="165" text-anchor="middle" fill="#e6edf3" font-size="17" font-weight="600">Agent</text>
  <text x="335" y="190" text-anchor="middle" fill="#8b949e" font-size="12">spec: role, workflow,</text>
  <text x="335" y="208" text-anchor="middle" fill="#8b949e" font-size="12">hard rules</text>
  <rect x="500" y="130" width="180" height="130" rx="10" fill="#161b22" stroke="#d29922" stroke-width="1.5"/>
  <text x="590" y="165" text-anchor="middle" fill="#d29922" font-size="16" font-weight="600">Subagents</text>
  <text x="590" y="190" text-anchor="middle" fill="#8b949e" font-size="12">delegated search,</text>
  <text x="590" y="208" text-anchor="middle" fill="#8b949e" font-size="12">analysis subtasks</text>
  <rect x="760" y="40" width="220" height="140" rx="10" fill="#161b22" stroke="#58a6ff" stroke-width="1.5"/>
  <text x="870" y="70" text-anchor="middle" fill="#58a6ff" font-size="16" font-weight="600">Raw doc files</text>
  <text x="870" y="96" text-anchor="middle" fill="#e6edf3" font-size="13">RQE-Troubleshooting-Guide.md</text>
  <text x="870" y="118" text-anchor="middle" fill="#8b949e" font-size="12">best-practices/*.md</text>
  <text x="870" y="140" text-anchor="middle" fill="#8b949e" font-size="12">found via glob / grep / read</text>
  <rect x="760" y="220" width="220" height="140" rx="10" fill="#161b22" stroke="#3fb950" stroke-width="1.5"/>
  <text x="870" y="250" text-anchor="middle" fill="#3fb950" font-size="16" font-weight="600">MCP Tools</text>
  <text x="870" y="276" text-anchor="middle" fill="#e6edf3" font-size="13" font-family="monospace">analyse_ft_profile</text>
  <text x="870" y="298" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">ft_info · slowlog_get</text>
  <text x="870" y="318" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">redis_execute</text>
  <line x1="170" y1="195" x2="232" y2="195" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar0)"/>
  <line x1="430" y1="195" x2="492" y2="195" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar0)"/>
  <line x1="680" y1="160" x2="752" y2="115" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar0)"/>
  <line x1="680" y1="230" x2="752" y2="285" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar0)"/>
  <text x="716" y="125" text-anchor="middle" fill="#8b949e" font-size="12">search + read</text>
  <text x="716" y="280" text-anchor="middle" fill="#8b949e" font-size="12">invoke</text>
</svg>

<span class="muted small">Knowledge is unstructured — the agent must <i>find</i> what it needs. This is the baseline we measure against.</span>

---

###### Architecture — With Playbook
# Structured knowledge replaces raw docs

<svg viewBox="0 0 1250 440" style="width:100%;max-height:56vh" xmlns="http://www.w3.org/2000/svg">
  <defs><marker id="ar" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L8,3 L0,6" fill="none" stroke="#ff4438" stroke-width="1.5"/></marker></defs>
  <rect x="20" y="185" width="150" height="70" rx="10" fill="#161b22" stroke="#2d333b"/>
  <text x="95" y="215" text-anchor="middle" fill="#e6edf3" font-size="17" font-weight="600">User</text>
  <text x="95" y="237" text-anchor="middle" fill="#8b949e" font-size="12">symptom description</text>
  <rect x="240" y="155" width="190" height="130" rx="10" fill="#161b22" stroke="#ff4438" stroke-width="1.5"/>
  <text x="335" y="190" text-anchor="middle" fill="#e6edf3" font-size="17" font-weight="600">Agent</text>
  <text x="335" y="215" text-anchor="middle" fill="#8b949e" font-size="12">spec: role, workflow,</text>
  <text x="335" y="233" text-anchor="middle" fill="#8b949e" font-size="12">hard rules</text>
  <rect x="500" y="155" width="180" height="130" rx="10" fill="#161b22" stroke="#d29922" stroke-width="1.5"/>
  <text x="590" y="190" text-anchor="middle" fill="#d29922" font-size="16" font-weight="600">Subagents</text>
  <text x="590" y="215" text-anchor="middle" fill="#8b949e" font-size="12">delegated search,</text>
  <text x="590" y="233" text-anchor="middle" fill="#8b949e" font-size="12">analysis subtasks</text>
  <rect x="760" y="15" width="220" height="130" rx="10" fill="#161b22" stroke="#58a6ff" stroke-width="1.5"/>
  <text x="870" y="43" text-anchor="middle" fill="#58a6ff" font-size="16" font-weight="600">Playbook + organized docs</text>
  <text x="870" y="68" text-anchor="middle" fill="#e6edf3" font-size="13">symptoms-router.md</text>
  <text x="870" y="90" text-anchor="middle" fill="#8b949e" font-size="12">slow-queries/ SQ-01…05</text>
  <text x="870" y="108" text-anchor="middle" fill="#8b949e" font-size="12">indexing-failures/ IF-01…06</text>
  <text x="870" y="126" text-anchor="middle" fill="#8b949e" font-size="12">memory · vector · connection</text>
  <rect x="760" y="165" width="220" height="115" rx="10" fill="#161b22" stroke="#3fb950" stroke-width="1.5"/>
  <text x="870" y="193" text-anchor="middle" fill="#3fb950" font-size="16" font-weight="600">Data processing tools</text>
  <text x="870" y="218" text-anchor="middle" fill="#e6edf3" font-size="13" font-family="monospace">analyse_ft_profile</text>
  <text x="870" y="240" text-anchor="middle" fill="#8b949e" font-size="12">parse · summarize · outliers</text>
  <text x="870" y="258" text-anchor="middle" fill="#8b949e" font-size="12">(MCP)</text>
  <rect x="760" y="300" width="220" height="115" rx="10" fill="#161b22" stroke="#3fb950" stroke-width="1.5"/>
  <text x="870" y="328" text-anchor="middle" fill="#3fb950" font-size="16" font-weight="600">Data connector tools</text>
  <text x="870" y="353" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">ft_info · slowlog_get</text>
  <text x="870" y="373" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">redis_execute · fetch_redis_metrics</text>
  <text x="870" y="393" text-anchor="middle" fill="#8b949e" font-size="12">(MCP → live Redis)</text>
  <text x="1125" y="135" text-anchor="middle" fill="#3fb950" font-size="13" font-weight="600">Same tools, second axis:</text>
  <text x="1125" y="153" text-anchor="middle" fill="#3fb950" font-size="13" font-weight="600">when they run</text>
  <rect x="1015" y="165" width="220" height="115" rx="10" fill="none" stroke="#3fb950" stroke-width="1.5" stroke-dasharray="7 5"/>
  <text x="1125" y="200" text-anchor="middle" fill="#3fb950" font-size="15" font-weight="600">Setup tools</text>
  <text x="1125" y="224" text-anchor="middle" fill="#8b949e" font-size="12">always run first</text>
  <text x="1125" y="246" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">ft_info · get_ftprofile_stats</text>
  <rect x="1015" y="300" width="220" height="115" rx="10" fill="none" stroke="#3fb950" stroke-width="1.5" stroke-dasharray="7 5"/>
  <text x="1125" y="335" text-anchor="middle" fill="#3fb950" font-size="15" font-weight="600">Discretionary tools</text>
  <text x="1125" y="359" text-anchor="middle" fill="#8b949e" font-size="12">invoked by model judgment</text>
  <text x="1125" y="381" text-anchor="middle" fill="#8b949e" font-size="12" font-family="monospace">drilldowns · redis_execute · …</text>
  <line x1="170" y1="220" x2="232" y2="220" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar)"/>
  <line x1="430" y1="220" x2="492" y2="220" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar)"/>
  <line x1="680" y1="180" x2="752" y2="90" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar)"/>
  <line x1="680" y1="220" x2="752" y2="222" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar)"/>
  <line x1="680" y1="260" x2="752" y2="350" stroke="#ff4438" stroke-width="1.5" marker-end="url(#ar)"/>
  <text x="712" y="120" text-anchor="middle" fill="#8b949e" font-size="12">route + read</text>
  <text x="716" y="212" text-anchor="middle" fill="#8b949e" font-size="12">invoke</text>
</svg>

<span class="muted small">Two orthogonal tool abstractions: <b>what they do</b> — processing vs. connector — and <b>when they run</b> — setup (always, e.g. <code>ft_info</code>) vs. discretionary (model judgment).</span>

---

###### Approach 2/2
# Knowledge base → Agent Diagnostic Playbook

<div class="flow">
  <div class="step"><b>Troubleshooting guide</b><br><span class="muted">prose</span></div>
  <div class="arrow">→</div>
  <div class="step"><b>Symptom extraction</b><br><span class="muted">user language</span></div>
  <div class="arrow">→</div>
  <div class="step"><b>Router</b><br><span class="muted">symptom → handbook</span></div>
  <div class="arrow">→</div>
  <div class="step"><b>Handbooks</b><br><span class="muted">where to look · what to interpret · how to act</span></div>
</div>

<div class="cols">
<div>

```yaml
- symptom: "Any query with *word*
    or big OR chains gets
    dramatically slower."
  likely_problem_type:
    "Wildcards and broad
     text expansion"
  route_to_handbook:
    - slow-queries/SQ-02-expensive-
      wildcards-and-broad-text.md
```

</div>
<div>

- Symptoms phrased **the way users complain**
- Routing is a lookup, not a search
- Handbooks are self-contained: diagnostics, patterns, fixes

</div>
</div>

---

###### Measuring It
# Trajectory-based evaluation

We instrument every session and score the **full trajectory**, not just the final answer:

<div class="cols">
<div class="card"><h3>Correctness</h3><p><b>The answer is correct.</b></p><p class="muted small">grounding · specificity · hallucinated paths · ordering (LCS similarity)</p></div>
<div class="card"><h3>Completeness</h3><p><b>The answer has identified all the issues.</b></p><p class="muted small">tool/doc coverage · % sessions missing a required read · first-pass success</p></div>
<div class="card"><h3>Efficiency</h3><p><b>It has taken the most optimal path to the answer.</b></p><p class="muted small">redundant/repeated reads · backtracking · dead-ends · tokens · duration · tool errors</p></div>
<div class="card"><h3>Usefulness</h3><p><b>The fix preserves the user's semantic intent.</b></p><p class="muted small">e.g. "use fewer search terms" speeds the query but breaks what the user meant</p></div>
</div>

<div class="callout">Coverage makes <b>omission</b> visible; usefulness is the hardest to automate — it requires judging recommendations against intent, not just evidence.</div>

---

###### Experiment 1 — Tool Granularity
# Which MCP setup? Four designs, head-to-head

4 FT.PROFILE datasets × 5 sessions per setup (<span class="num">20 runs each</span>):

| Setup | Avg duration | Std dev | Tool calls | Quality |
|---|---:|---:|---:|---:|
| Baseline | 50.8s | 26.8s | 217* | mixed |
| **With-setup-tools** | 31.9s | **5.6s** | **224** | **20/20** |
| Minimal | **31.6s** | 7.0s | 251 | **20/20** |
| Full | 40.6s | 13.3s | 390 | 19/20 |

<div class="callout"><b>With-setup-tools wins 3/4 datasets</b>: layered drilldowns — broad stats → diagnosis → iterator tree → details. Full is over-instrumented; the Baseline* leans on 49 bash + 5 grep fallbacks.</div>

---

###### Experiment 1 — Tool Granularity
# Granularity shapes trajectory consistency

<div class="cols">
<div>

| | With-setup-tools | Minimal |
|---|---:|---:|
| Unique sequences / 20 runs | **10** | 19 |
| Avg LCS similarity | **0.849** | 0.679 |
| Avg tool-path length | **11.2** | 12.6 |

<span class="muted small">LCS = longest common subsequence of tool calls, normalized — higher = more consistent ordering. With-setup-tools more consistent on <b>every</b> dataset.</span>

</div>
<div>

<svg viewBox="0 0 760 330" style="width:100%;background:#161b22;border:1px solid #2d333b;border-radius:12px" xmlns="http://www.w3.org/2000/svg">
  <line x1="70" y1="60" x2="700" y2="60" stroke="#2d333b" stroke-width="2"/>
  <rect x="14" y="38" width="44" height="44" rx="9" fill="#1c2129" stroke="#2d333b"/>
  <text x="36" y="66" text-anchor="middle" fill="#e6edf3" font-size="15" font-weight="700">U1</text>
  <text x="36" y="100" text-anchor="middle" fill="#8b949e" font-size="10">turn 1</text>
  <circle cx="722" cy="60" r="16" fill="#1c2129" stroke="#3fb950" stroke-width="1.5"/>
  <text x="722" y="65" text-anchor="middle" fill="#3fb950" font-size="14">✓</text>
  <text x="722" y="96" text-anchor="middle" fill="#8b949e" font-size="10">answer</text>
  <g fill="#8b949e" font-size="10" text-anchor="middle">
    <text x="95" y="46">+0.0s</text><text x="150" y="46">+2.9s</text><text x="255" y="46">+8.5s</text>
    <text x="430" y="46">+18.1s</text><text x="565" y="46">+25.7s</text><text x="672" y="46">+33.8s</text>
  </g>
  <g stroke="#4a5058" stroke-width="1.5">
    <line x1="95" y1="52" x2="95" y2="68"/><line x1="150" y1="52" x2="150" y2="68"/>
    <line x1="255" y1="52" x2="255" y2="68"/><line x1="430" y1="52" x2="430" y2="68"/>
    <line x1="565" y1="52" x2="565" y2="68"/><line x1="672" y1="52" x2="672" y2="68"/>
  </g>
  <rect x="81" y="78" width="28" height="28" rx="6" fill="#58a6ff"/>
  <text x="95" y="97" text-anchor="middle" fill="#0d1117" font-size="12" font-weight="700">r</text>
  <text x="113" y="120" fill="#8b949e" font-size="10" transform="rotate(32 113 120)">diagnose_issues.json</text>
  <rect x="136" y="78" width="28" height="28" rx="6" fill="#3fb950"/>
  <text x="150" y="97" text-anchor="middle" fill="#0d1117" font-size="12" font-weight="700">t</text>
  <rect x="136" y="114" width="28" height="28" rx="6" fill="#3fb950"/>
  <text x="150" y="133" text-anchor="middle" fill="#0d1117" font-size="12" font-weight="700">t</text>
  <text x="168" y="102" fill="#8b949e" font-size="10" transform="rotate(32 168 102)">ft_info</text>
  <text x="168" y="140" fill="#8b949e" font-size="10" transform="rotate(32 168 140)">slowlog_get</text>
  <rect x="241" y="78" width="28" height="28" rx="6" fill="#4a5568"/>
  <text x="255" y="97" text-anchor="middle" fill="#e6edf3" font-size="12" font-weight="700">t</text>
  <text x="273" y="112" fill="#8b949e" font-size="10" transform="rotate(32 273 112)">ft_profile</text>
  <rect x="416" y="78" width="28" height="28" rx="6" fill="#4a5568"/>
  <rect x="416" y="114" width="28" height="28" rx="6" fill="#4a5568"/>
  <rect x="416" y="150" width="28" height="28" rx="6" fill="#4a5568"/>
  <rect x="416" y="186" width="28" height="28" rx="6" fill="#4a5568"/>
  <g fill="#e6edf3" font-size="12" font-weight="700" text-anchor="middle">
    <text x="430" y="97">t</text><text x="430" y="133">t</text><text x="430" y="169">t</text><text x="430" y="205">t</text>
  </g>
  <text x="448" y="102" fill="#8b949e" font-size="10" transform="rotate(32 448 102)">redis_execute</text>
  <text x="448" y="140" fill="#8b949e" font-size="10" transform="rotate(32 448 140)">redis_execute</text>
  <text x="448" y="176" fill="#8b949e" font-size="10" transform="rotate(32 448 176)">ft_profile</text>
  <text x="448" y="212" fill="#8b949e" font-size="10" transform="rotate(32 448 212)">ft_profile</text>
  <rect x="551" y="78" width="28" height="28" rx="6" fill="#4a5568"/>
  <text x="565" y="97" text-anchor="middle" fill="#e6edf3" font-size="12" font-weight="700">t</text>
  <text x="583" y="112" fill="#8b949e" font-size="10" transform="rotate(32 583 112)">ft_profile</text>
  <rect x="658" y="78" width="28" height="28" rx="6" fill="#4a5568"/>
  <rect x="658" y="114" width="28" height="28" rx="6" fill="#4a5568"/>
  <g fill="#e6edf3" font-size="12" font-weight="700" text-anchor="middle">
    <text x="672" y="97">t</text><text x="672" y="133">t</text>
  </g>
  <text x="690" y="102" fill="#8b949e" font-size="10" transform="rotate(32 690 102)">redis_execute</text>
  <text x="690" y="140" fill="#8b949e" font-size="10" transform="rotate(32 690 140)">redis_execute</text>
  <text x="150" y="30" text-anchor="middle" fill="#3fb950" font-size="10" font-weight="600">setup</text>
  <text x="430" y="245" text-anchor="middle" fill="#8b949e" font-size="10">stacked = parallel / overlapping calls</text>
  <g font-size="10" fill="#8b949e">
    <rect x="70" y="290" width="18" height="18" rx="4" fill="#58a6ff"/><text x="95" y="303">read</text>
    <rect x="140" y="290" width="18" height="18" rx="4" fill="#3fb950"/><text x="165" y="303">setup tool</text>
    <rect x="250" y="290" width="18" height="18" rx="4" fill="#4a5568"/><text x="275" y="303">discretionary tool</text>
  </g>
</svg>

<span class="muted small">One session, one turn — context read + setup tools first, then discretionary drilldowns → answer in ~34s.</span>

</div>
</div>

---

###### Experiment 1 — Tool Granularity
# Why each setup behaves the way it does

<div class="cols">
<div class="card"><h3 class="good">With-setup-tools — the only one with <i>setup tools</i></h3><p>Broad summary stats (timing, counts, iterators) establish runtime context <i>before</i> discretionary drilldowns (diagnosis, iterator tree, details). This layering <i>induces</i> a consistent investigation order.</p></div>
<div class="card"><h3>Minimal — strong but unguided</h3><p>Few tools, all necessary — so quality holds and it's marginally fastest. But no setup layer means each run improvises its own path: more calls, 19 unique trajectories in 20 runs.</p></div>
<div class="card"><h3>Full — over-instrumented</h3><p>Richest toolset, so the agent explores tools instead of the problem: 390 calls, slowest of the good setups, no quality gain to show for it.</p></div>
<div class="card"><h3>Baseline — under-powered</h3><p>Lacks targeted drilldowns, so the agent falls back to raw reads, grep, and bash (49 bash calls) — workable on simple cases, slow and inconsistent otherwise.</p></div>
</div>

<div class="callout">Granularity is a design dial: too few tools → fallback improvisation; too many → exploration overhead. <b>Setup tools + discretionary drilldowns = just right</b> — this is the demonstrated need for dynamic, runtime exploration.</div>

---

###### Experiment 2 — Knowledge Structure
# Raw guide vs. diagnostic playbook

<div class="cols">
<div class="card"><h3 class="warn">Baseline: raw guide</h3><p>Same agent spec, knowledge provided as a monolithic <code>RQE-Troubleshooting-Guide.md</code> plus best-practices docs. Agent must find what it needs.</p></div>
<div class="card"><h3 class="good">Treatment: playbook</h3><p>Same knowledge, restructured as symptoms-router + per-issue handbooks (<span class="num">SQ-04, SQ-07, SQ-11</span> relevant to the tasks).</p></div>
</div>

- **5 sessions per condition**, identical troubleshooting tasks over 5 `FT.PROFILE` inputs
- Both conditions call `analyse_ft_profile` <span class="num">25×</span> (5 per session) — tool access held constant
- Only the **knowledge structure** changes

---

###### Experiment 2 — Results 1/2
# Answer quality

<div class="legend"><span><span class="sw" style="background:#d29922"></span>Raw guide</span><span><span class="sw" style="background:#3fb950"></span>Playbook</span></div>

<div class="bars">
  <div class="bar-row"><span class="label">First-pass success</span><div class="bar-track"><div class="bar-fill" style="width:86%;background:#d29922"></div></div><span class="delta">0.86</span></div>
  <div class="bar-row"><span class="label"></span><div class="bar-track"><div class="bar-fill" style="width:95%;background:#3fb950"></div></div><span class="delta good">0.95</span></div>
  <div class="bar-row"><span class="label">Answer grounding</span><div class="bar-track"><div class="bar-fill" style="width:27.3%;background:#d29922"></div></div><span class="delta">0.27</span></div>
  <div class="bar-row"><span class="label"></span><div class="bar-track"><div class="bar-fill" style="width:39.7%;background:#3fb950"></div></div><span class="delta good">0.40</span></div>
  <div class="bar-row"><span class="label">Answer specificity</span><div class="bar-track"><div class="bar-fill" style="width:77.4%;background:#d29922"></div></div><span class="delta">0.77</span></div>
  <div class="bar-row"><span class="label"></span><div class="bar-track"><div class="bar-fill" style="width:88.8%;background:#3fb950"></div></div><span class="delta good">0.89</span></div>
  <div class="bar-row"><span class="label">Dead-end rate ↓</span><div class="bar-track"><div class="bar-fill" style="width:21.2%;background:#d29922"></div></div><span class="delta">0.21</span></div>
  <div class="bar-row"><span class="label"></span><div class="bar-track"><div class="bar-fill" style="width:10.2%;background:#3fb950"></div></div><span class="delta good">0.10</span></div>
</div>

<span class="muted small">Mean over 5 sessions per condition · grounding +45% rel., specificity +15% rel., dead-ends −52%</span>

---

###### Experiment 2 — Results 2/2
# Cost & reliability

<div class="cols3">
  <div class="card" style="text-align:center"><div class="big-stat">−43%</div><div class="stat-label">total tokens<br><span class="num">326.5k → 185.0k</span></div></div>
  <div class="card" style="text-align:center"><div class="big-stat">−48%</div><div class="stat-label">tool error rate<br><span class="num">0.113 → 0.059</span></div></div>
  <div class="card" style="text-align:center"><div class="big-stat">−15%</div><div class="stat-label">wall-clock duration<br><span class="num">79.8s → 67.6s</span></div></div>
</div>

| Metric | Raw guide | Playbook |
|---|---:|---:|
| Cache-read tokens | 218,573 | **108,442 (−50%)** |
| Backtracking rate | 0.015 | **0.000** |
| Repeated doc reads / session | 0.80 | **0.00** |
| Unique documents covered | 8.6 | **9.8** |
| Required docs read in all 5 sessions | no (3/5 typical) | **yes (5/5)** |

---

###### Evaluation-Led Design
# The metrics also tell us what's still broken

- **Hallucinated-path rate stays high in both** (<span class="num">0.94 → 0.86</span>): agents still guess near-miss paths (<code>ft.profile</code> vs <code>ftProfile1.txt</code>) before consulting the router — the next spec iteration targets this directly
- **Grounding variance went up** (σ <span class="num">0.05 → 0.14</span>) even as the mean improved: one or two playbook sessions skip an optional handbook — coverage tables point at exactly which reads were missed
- Exploration breadth/depth barely moved — the playbook doesn't make the agent *less curious*, it makes exploration *land*

<div class="callout">This is the loop: <b>trajectory metrics → identify failure mode → revise spec/playbook → re-measure.</b> Design choices become testable hypotheses.</div>

---

###### Takeaways
# Three things to steal

<div class="cols3">
<div class="card"><h3><span class="accent" style="font-size:1.6em">1</span><br>Evaluation-led spec design</h3><p>Let trajectory metrics — not intuition — drive how you structure agent specs, tool granularity, and ordering constraints.</p></div>
<div class="card"><h3><span class="accent" style="font-size:1.6em">2</span><br>KB → playbook pipelines</h3><p>Restructure human knowledge bases into routed diagnostic playbooks: where to look, what to interpret, how to act — for efficient, reliable trajectories.</p></div>
<div class="card"><h3><span class="accent" style="font-size:1.6em">3</span><br>Measure the trajectory</h3><p>Correctness, completeness, and usefulness of the answer are downstream of tool-call coverage, ordering, and duplication. Instrument those.</p></div>
</div>

<div class="callout">Same model, same tools, same knowledge — <b>structure alone</b> bought ~half the cost and a large step toward deterministic, debuggable trajectories.</div>

<span class="muted small">Srijith Rajamohan · srijith.rajamohan@redis.com</span>
