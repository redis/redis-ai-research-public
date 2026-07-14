#!/usr/bin/env python3
"""Rebuild talk-slides.html as a native PDF (dark theme, 16:9)."""
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import simpleSplit

for name, path in [
    ("DVS", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("DVM", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
]:
    pdfmetrics.registerFont(TTFont(name, path))

W, H = 960, 540
BG = HexColor("#0d1117"); PANEL = HexColor("#161b22"); PANEL2 = HexColor("#1c2129")
BORDER = HexColor("#2d333b"); TEXT = HexColor("#e6edf3"); MUTED = HexColor("#8b949e")
ACCENT = HexColor("#ff4438"); BLUE = HexColor("#58a6ff"); GOOD = HexColor("#3fb950")
WARN = HexColor("#d29922"); GRAY = HexColor("#4a5568"); DARK = HexColor("#0d1117")

OUT = "talk-slides.pdf"
c = canvas.Canvas(OUT, pagesize=(W, H))
c.setTitle("Spec-Driven Agents for RQE: Specs, Tooling, and Trajectory-Based Evaluation")


def T(y):
    return H - y


def new_slide():
    c.setFillColor(BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)


def text(x, y, s, font="DVS", size=12, color=TEXT, align="l"):
    c.setFont(font, size)
    c.setFillColor(color)
    if align == "c":
        c.drawCentredString(x, T(y), s)
    elif align == "r":
        c.drawRightString(x, T(y), s)
    else:
        c.drawString(x, T(y), s)


def para(x, y, s, font="DVS", size=12, color=TEXT, maxw=840, leading=None):
    lines = simpleSplit(s, font, size, maxw)
    lead = leading or size * 1.45
    for i, ln in enumerate(lines):
        text(x, y + i * lead, ln, font, size, color)
    return y + len(lines) * lead


def kicker_title(kick, title, y=52):
    text(60, y, kick.upper(), "DVB", 10.5, ACCENT)
    c.setFont("DVB", 25)
    c.setFillColor(TEXT)
    c.drawString(60, T(y + 30), title)
    return y + 62


def rrect(x, y_top, w, h, fill=PANEL, stroke=BORDER, r=9, dash=None, sw=1):
    c.setLineWidth(sw)
    if dash:
        c.setDash(dash[0], dash[1])
    else:
        c.setDash()
    if fill:
        c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.roundRect(x, T(y_top + h), w, h, r, stroke=1, fill=1 if fill else 0)
    c.setDash()


def card(x, y_top, w, h, title, body, tcolor=BLUE, muted_line=None, bsize=11.5):
    rrect(x, y_top, w, h)
    text(x + 16, y_top + 26, title, "DVB", 13, tcolor)
    yy = para(x + 16, y_top + 47, body, "DVS", bsize, TEXT, w - 32)
    if muted_line:
        para(x + 16, yy + 4, muted_line, "DVS", 9.5, MUTED, w - 32)


def callout(y_top, s, w=840, size=12.5):
    lines = simpleSplit(s, "DVS", size, w - 44)
    h = len(lines) * size * 1.45 + 26
    c.setFillColor(PANEL)
    c.rect(60, T(y_top + h), w, h, stroke=0, fill=1)
    c.setFillColor(ACCENT)
    c.rect(60, T(y_top + h), 4, h, stroke=0, fill=1)
    for i, ln in enumerate(lines):
        text(82, y_top + 22 + i * size * 1.45, ln, "DVS", size, TEXT)
    return y_top + h


def arrow(x1, y1, x2, y2, color=ACCENT):
    c.setStrokeColor(color)
    c.setLineWidth(1.4)
    c.line(x1, T(y1), x2, T(y2))
    import math
    ang = math.atan2(T(y2) - T(y1), x2 - x1)
    for da in (2.6, -2.6):
        c.line(x2, T(y2), x2 - 8 * math.cos(ang + da), T(y2) - 8 * math.sin(ang + da))


def bullet(x, y, s, size=13, maxw=800, color=TEXT, gap=1.5):
    c.setFillColor(ACCENT)
    c.circle(x + 3, T(y) + size * 0.32, 2.2, stroke=0, fill=1)
    return para(x + 16, y, s, "DVS", size, color, maxw - 16, size * gap)


# ---------------------------------------------------------------- 1 title
new_slide()
c.setFont("DVB", 40)
c.setFillColor(ACCENT); c.drawString(60, T(170), "Spec-Driven Agents")
c.setFillColor(TEXT); c.drawString(60 + c.stringWidth("Spec-Driven Agents ", "DVB", 40), T(170), "for RQE:")
c.drawString(60, T(222), "Specs, Tooling, and")
c.drawString(60, T(274), "Trajectory-Based Evaluation")
text(60, 330, "Evaluation-led agent design on the Redis Query Engine", "DVS", 16, MUTED)
text(60, 420, "Srijith Rajamohan, Ph.D. · Yash Mandilwar · Chris Coleman · Itamar Haber · Adriano Amaral", "DVS", 13.5, TEXT)
text(60, 442, "Redis", "DVS", 12, MUTED)
c.showPage()

# ---------------------------------------------------------------- 2 problem
new_slide()
y = kicker_title("The Problem", "Reliability is the bottleneck between demo and production")
y = para(60, y + 8, "Production means, every time:", "DVS", 15, TEXT) + 10
c.setFont("DVS", 15)
y = bullet(60, y, "The right tools, in the correct order", 15) + 10
y = bullet(60, y, "All the time", 15) + 30
callout(y, "Answers must be correct, complete, and useful — ideally also efficient — and all of it measurable.")
c.showPage()

# ---------------------------------------------------------------- 3 case study
new_slide()
y = kicker_title("Case Study", "Troubleshooting the Redis Query Engine")
ly = para(60, y + 6, "An agent that diagnoses RQE (RediSearch) issues: slow queries, indexing failures, memory "
          "pressure, stale results, vector search problems.", "DVS", 12.5, TEXT, 380) + 14
for b in ["Interprets raw diagnostics: FT.PROFILE, FT.INFO, SLOWLOG, shard metrics",
          "Staged workflow: Clarification → Diagnosis → Confirmation → Solution",
          "Must ground every conclusion in collected evidence"]:
    ly = bullet(60, ly, b, 12.5, 380) + 10
card(490, y, 410, 280, "Why this domain is hard for agents", "", BLUE)
cy = y + 50
for b in ["Unlike SQL, parametric knowledge is often incorrect",
          "Very context dependent — needs to know what information is missing from a question",
          "Analysis may not catch all issues (completeness)",
          "Answers can be non-useful"]:
    cy = bullet(508, cy, b, 12, 372) + 12
c.showPage()

# ---------------------------------------------------------------- 4 failure modes
new_slide()
y = kicker_title("Observed Failure Modes", "How agents actually go wrong")
text(60, y + 8, "CORRECTNESS", "DVB", 11, ACCENT)
text(640, y + 8, "EFFICIENCY", "DVB", 11, ACCENT)
cy = y + 22
card(60, cy, 270, 150, "Tool & file-read omission",
     "Skips a required tool call, misses a file entirely, or reads it partially — the answer looks "
     "complete but is missing evidence.")
card(345, cy, 270, 150, "Hallucination & misuse of information",
     "Guesses paths or arguments that don't exist, forgets evidence already collected, or reasons "
     "illogically from what it has.")
card(640, cy, 260, 150, "Inefficient paths",
     "Exploratory flailing: repeated reads of the same document, backtracking, dead-end searches.")
callout(cy + 175, 'None of these show up in an end-to-end "did it answer?" eval. They only show up in the trajectory.')
c.showPage()

# ---------------------------------------------------------------- 5 approach 1
new_slide()
y = kicker_title("Approach 1/2", "Spec-driven design & tool granularity")
y = para(60, y + 6, "Hierarchical specs + deliberately shaped tools — not a monolithic prompt:", "DVS", 14, TEXT) + 14
card(60, y, 270, 165, "Specs encode context & tool use",
     "What context matters · which tools, how, in what order · required inputs declared.")
card(345, y, 270, 165, "Right-sized tools",
     "Transforms belong in tools, not the context window: analyse_ft_profile → compact summary + outliers.")
card(630, y, 270, 165, "Setup vs. discretionary tools",
     "Setup: always run — ft_info before any query. Discretionary: invoked by model judgment as context requires.")
c.showPage()

# ---------------------------------------------------------------- 6 baseline architecture
new_slide()
y = kicker_title("Architecture — Baseline", "A standard spec-driven agent")
s = 0.84; ox, oy = 60, y + 4


def box(bx, by, bw, bh, stroke, lines, tcolor=TEXT, dash=None):
    x, yt = ox + bx * s, oy + by * s
    rrect(x, yt, bw * s, bh * s, PANEL, stroke, 9, dash, 1.3)
    ty = yt + 24
    for i, (ln, f, sz, col) in enumerate(lines):
        text(x + bw * s / 2, ty, ln, f, sz, col, "c")
        ty += sz * 1.55


box(20, 160, 150, 70, BORDER, [("User", "DVB", 13, TEXT), ("symptom description", "DVS", 9, MUTED)])
box(240, 130, 190, 130, ACCENT, [("Agent", "DVB", 13, TEXT), ("spec: role, workflow,", "DVS", 9.5, MUTED),
                                 ("hard rules", "DVS", 9.5, MUTED)])
box(500, 130, 180, 130, WARN, [("Subagents", "DVB", 12.5, WARN), ("delegated search,", "DVS", 9.5, MUTED),
                               ("analysis subtasks", "DVS", 9.5, MUTED)])
box(760, 40, 220, 140, BLUE, [("Raw doc files", "DVB", 12.5, BLUE),
                              ("RQE-Troubleshooting-Guide.md", "DVS", 9, TEXT),
                              ("best-practices/*.md", "DVS", 9, MUTED),
                              ("found via glob / grep / read", "DVS", 9, MUTED)])
box(760, 220, 220, 140, GOOD, [("MCP Tools", "DVB", 12.5, GOOD),
                               ("analyse_ft_profile", "DVM", 9.5, TEXT),
                               ("ft_info · slowlog_get", "DVM", 9, MUTED),
                               ("redis_execute", "DVM", 9, MUTED)])
arrow(ox + 170 * s, oy + 195 * s, ox + 232 * s, oy + 195 * s)
arrow(ox + 430 * s, oy + 195 * s, ox + 492 * s, oy + 195 * s)
arrow(ox + 680 * s, oy + 160 * s, ox + 752 * s, oy + 115 * s)
arrow(ox + 680 * s, oy + 230 * s, ox + 752 * s, oy + 285 * s)
text(ox + 716 * s, oy + 118 * s, "search + read", "DVS", 9, MUTED, "c")
text(ox + 716 * s, oy + 280 * s, "invoke", "DVS", 9, MUTED, "c")
text(60, 500, "Knowledge is unstructured — the agent must find what it needs. This is the baseline we measure against.",
     "DVS", 11, MUTED)
c.showPage()

# ---------------------------------------------------------------- 7 playbook architecture
new_slide()
y = kicker_title("Architecture — With Playbook", "Structured knowledge replaces raw docs")
s = 0.68; ox, oy = 60, y + 2
box(20, 185, 150, 70, BORDER, [("User", "DVB", 12, TEXT), ("symptom description", "DVS", 8.5, MUTED)])
box(240, 155, 190, 130, ACCENT, [("Agent", "DVB", 12, TEXT), ("spec: role, workflow,", "DVS", 8.5, MUTED),
                                 ("hard rules", "DVS", 8.5, MUTED)])
box(500, 155, 180, 130, WARN, [("Subagents", "DVB", 11.5, WARN), ("delegated search,", "DVS", 8.5, MUTED),
                               ("analysis subtasks", "DVS", 8.5, MUTED)])
box(760, 15, 220, 130, BLUE, [("Playbook + organized docs", "DVB", 10.5, BLUE),
                              ("symptoms-router.md", "DVS", 8.5, TEXT),
                              ("slow-queries/ SQ-01…05", "DVS", 8.5, MUTED),
                              ("indexing-failures/ IF-01…06", "DVS", 8.5, MUTED),
                              ("memory · vector · connection", "DVS", 8.5, MUTED)])
box(760, 165, 220, 115, GOOD, [("Data processing tools", "DVB", 10.5, GOOD),
                               ("analyse_ft_profile", "DVM", 8.5, TEXT),
                               ("parse · summarize · outliers", "DVS", 8.5, MUTED),
                               ("(MCP)", "DVS", 8.5, MUTED)])
box(760, 300, 220, 115, GOOD, [("Data connector tools", "DVB", 10.5, GOOD),
                               ("ft_info · slowlog_get", "DVM", 8.5, MUTED),
                               ("redis_execute · fetch_redis_metrics", "DVM", 8, MUTED),
                               ("(MCP → live Redis)", "DVS", 8.5, MUTED)])
text(ox + 1125 * s, oy + 138 * s, "Same tools, second axis:", "DVB", 9, GOOD, "c")
text(ox + 1125 * s, oy + 156 * s, "when they run", "DVB", 9, GOOD, "c")
box(1015, 165, 220, 115, GOOD, [("Setup tools", "DVB", 10.5, GOOD),
                                ("always run first", "DVS", 8.5, MUTED),
                                ("ft_info · get_ftprofile_stats", "DVM", 8, MUTED)], dash=([5, 4], 0))
box(1015, 300, 220, 115, GOOD, [("Discretionary tools", "DVB", 10.5, GOOD),
                                ("invoked by model judgment", "DVS", 8.5, MUTED),
                                ("drilldowns · redis_execute · …", "DVM", 8, MUTED)], dash=([5, 4], 0))
arrow(ox + 170 * s, oy + 220 * s, ox + 232 * s, oy + 220 * s)
arrow(ox + 430 * s, oy + 220 * s, ox + 492 * s, oy + 220 * s)
arrow(ox + 680 * s, oy + 180 * s, ox + 752 * s, oy + 90 * s)
arrow(ox + 680 * s, oy + 220 * s, ox + 752 * s, oy + 222 * s)
arrow(ox + 680 * s, oy + 260 * s, ox + 752 * s, oy + 350 * s)
text(ox + 712 * s, oy + 120 * s, "route + read", "DVS", 8.5, MUTED, "c")
text(ox + 716 * s, oy + 212 * s, "invoke", "DVS", 8.5, MUTED, "c")
para(60, 480, "Two orthogonal tool abstractions: what they do — processing vs. connector — and when they run — "
     "setup (always, e.g. ft_info) vs. discretionary (model judgment).", "DVS", 11, MUTED, 840)
c.showPage()

# ---------------------------------------------------------------- 8 KB -> playbook
new_slide()
y = kicker_title("Approach 2/2", "Knowledge base → Agent Diagnostic Playbook")
steps = [("Troubleshooting guide", "prose"), ("Symptom extraction", "user language"),
         ("Router", "symptom → handbook"), ("Handbooks", "where to look · what to")]
fx = 60
for i, (t1, t2) in enumerate(steps):
    fw = 190
    rrect(fx, y, fw, 58, PANEL, BORDER)
    text(fx + fw / 2, y + 24, t1, "DVB", 11, TEXT, "c")
    text(fx + fw / 2, y + 42, t2, "DVS", 9, MUTED, "c")
    if i == 3:
        text(fx + fw / 2, y + 54, "interpret · how to act", "DVS", 9, MUTED, "c")
    if i < 3:
        arrow(fx + fw + 4, y + 29, fx + fw + 22, y + 29)
    fx += fw + 26
cy = y + 84
rrect(60, cy, 400, 190, PANEL, BORDER)
code = ['- symptom: "Any query with *word*', '    or big OR chains gets', '    dramatically slower."',
        '  likely_problem_type:', '    "Wildcards and broad', '     text expansion"', '  route_to_handbook:',
        '    - slow-queries/SQ-02-expensive-', '      wildcards-and-broad-text.md']
for i, ln in enumerate(code):
    text(80, cy + 26 + i * 17, ln, "DVM", 10, TEXT)
by = cy + 30
for b in ["Symptoms phrased the way users complain", "Routing is a lookup, not a search",
          "Handbooks are self-contained: diagnostics, patterns, fixes"]:
    by = bullet(500, by, b, 13, 400) + 18
c.showPage()

# ---------------------------------------------------------------- 9 trajectory eval
new_slide()
y = kicker_title("Measuring It", "Trajectory-based evaluation")
para(60, y, "We instrument every session and score the full trajectory, not just the final answer:", "DVS", 13, TEXT)
cy = y + 26
card(60, cy, 410, 118, "Correctness", "The answer is correct.", BLUE,
     "grounding · specificity · hallucinated paths · ordering (LCS similarity)", 12.5)
card(490, cy, 410, 118, "Completeness", "The answer has identified all the issues.", BLUE,
     "tool/doc coverage · % sessions missing a required read · first-pass success", 12.5)
card(60, cy + 132, 410, 118, "Efficiency", "It has taken the most optimal path to the answer.", BLUE,
     "redundant/repeated reads · backtracking · dead-ends · tokens · duration · tool errors", 12.5)
card(490, cy + 132, 410, 118, "Usefulness", "The fix preserves the user's semantic intent.", BLUE,
     'e.g. "use fewer search terms" speeds the query but breaks what the user meant', 12.5)
callout(cy + 268, "Coverage makes omission visible; usefulness is the hardest to automate — it requires judging "
        "recommendations against intent, not just evidence.", 840, 11.5)
c.showPage()

# ---------------------------------------------------------------- 10 exp1 table
new_slide()
y = kicker_title("Experiment 1 — Tool Granularity", "Which MCP setup? Four designs, head-to-head")
para(60, y, "4 FT.PROFILE datasets × 5 sessions per setup (20 runs each):", "DVS", 13, TEXT)
rows = [("Setup", "Avg duration", "Std dev", "Tool calls", "Quality", MUTED, "DVB"),
        ("Baseline", "50.8s", "26.8s", "217*", "mixed", TEXT, "DVS"),
        ("With-setup-tools", "31.9s", "5.6s", "224", "20/20", GOOD, "DVB"),
        ("Minimal", "31.6s", "7.0s", "251", "20/20", TEXT, "DVS"),
        ("Full", "40.6s", "13.3s", "390", "19/20", TEXT, "DVS")]
ty = y + 30
cols = [60, 420, 560, 700, 840]
for r in rows:
    col, fnt = r[5], r[6]
    text(cols[0], ty, r[0], fnt, 13, col)
    for j in range(1, 5):
        text(cols[j] + 60, ty, r[j], "DVM" if fnt == "DVS" or col == GOOD else "DVB", 12.5, col, "r")
    c.setStrokeColor(BORDER); c.setLineWidth(0.8)
    c.line(60, T(ty + 12), 900, T(ty + 12))
    ty += 34
callout(ty + 16, "With-setup-tools wins 3/4 datasets: layered drilldowns — broad stats → diagnosis → iterator "
        "tree → details. Full is over-instrumented; the Baseline* leans on 49 bash + 5 grep fallbacks.")
c.showPage()

# ---------------------------------------------------------------- 11 exp1 trajectories
new_slide()
y = kicker_title("Experiment 1 — Tool Granularity", "Granularity shapes trajectory consistency")
rows = [("", "With-setup-tools", "Minimal", MUTED), ("Unique sequences / 20 runs", "10", "19", TEXT),
        ("Avg LCS similarity", "0.849", "0.679", TEXT), ("Avg tool-path length", "11.2", "12.6", TEXT)]
ty = y + 14
for i, r in enumerate(rows):
    text(60, ty, r[0], "DVB" if i == 0 else "DVS", 11.5, r[3])
    text(330, ty, r[1], "DVB" if i == 0 else "DVM", 11.5, GOOD if i else MUTED, "r")
    text(410, ty, r[2], "DVB" if i == 0 else "DVM", 11.5, r[3] if i else MUTED, "r")
    c.setStrokeColor(BORDER); c.setLineWidth(0.8); c.line(60, T(ty + 10), 415, T(ty + 10))
    ty += 30
para(60, ty + 8, "LCS = longest common subsequence of tool calls, normalized — higher = more consistent "
     "ordering. With-setup-tools more consistent on every dataset.", "DVS", 10, MUTED, 360)
# timeline panel
px, py, pw, ph = 450, y, 450, 330
rrect(px, py, pw, ph, PANEL, BORDER, 10)
ts = pw / 760.0
tox, toy = px + 6, py + 10


def tt(sx, sy):
    return tox + sx * ts, toy + sy * ts


def tbox(sx, sy, fill, label=None, lcolor=DARK):
    x, yy = tt(sx, sy)
    rrect2 = 22 * ts * 1.25
    c.setFillColor(fill)
    c.roundRect(x, T(yy + 17), 17, 17, 4, stroke=0, fill=1)
    text(x + 8.5, yy + 12.5, "t" if label is None else label, "DVB", 9, lcolor, "c")


lx1, _ = tt(70, 60); lx2, ly = tt(700, 60)
c.setStrokeColor(BORDER); c.setLineWidth(1.5); c.line(lx1, T(ly), lx2, T(ly))
ux, uy = tt(14, 38)
rrect(ux, uy, 28, 28, PANEL2, BORDER, 6)
text(ux + 14, uy + 18, "U1", "DVB", 9, TEXT, "c")
text(ux + 14, uy + 42, "turn 1", "DVS", 7.5, MUTED, "c")
ax, ay = tt(722, 60)
c.setFillColor(PANEL2); c.setStrokeColor(GOOD); c.setLineWidth(1.2)
c.circle(ax, T(ay), 10, stroke=1, fill=1)
text(ax, ay + 3.5, "✓", "DVB", 9, GOOD, "c")
text(ax, ay + 24, "answer", "DVS", 7.5, MUTED, "c")
ticks = [(95, "+0.0s"), (150, "+2.9s"), (255, "+8.5s"), (430, "+18.1s"), (565, "+25.7s"), (672, "+33.8s")]
for sx, lab in ticks:
    x, yy = tt(sx, 60)
    c.setStrokeColor(HexColor("#4a5058")); c.setLineWidth(1); c.line(x, T(yy) - 5, x, T(yy) + 5)
    text(x, yy - 10, lab, "DVS", 7.5, MUTED, "c")
events = [(95, [("r", BLUE, "diagnose_issues.json")]),
          (150, [("t", GOOD, "ft_info"), ("t", GOOD, "slowlog_get")]),
          (255, [("t", GRAY, "ft_profile")]),
          (430, [("t", GRAY, "redis_execute"), ("t", GRAY, "redis_execute"), ("t", GRAY, "ft_profile"),
                 ("t", GRAY, "ft_profile")]),
          (565, [("t", GRAY, "ft_profile")]),
          (672, [("t", GRAY, "redis_execute"), ("t", GRAY, "redis_execute")])]
c.saveState()
for sx, evs in events:
    for i, (lab, fill, name) in enumerate(evs):
        sy = 78 + i * 34
        x, yy = tt(sx, sy)
        c.setFillColor(fill)
        c.roundRect(x - 8.5, T(yy + 17), 17, 17, 4, stroke=0, fill=1)
        text(x, yy + 12.5, lab, "DVB", 9, DARK if fill != GRAY else TEXT, "c")
        c.saveState()
        lx, lyy = tt(sx + 14, sy + 14)
        c.translate(lx, T(lyy))
        c.rotate(-30)
        c.setFont("DVS", 7.5); c.setFillColor(MUTED)
        c.drawString(0, 0, name)
        c.restoreState()
c.restoreState()
sx, sy = tt(150, 24)
text(sx, sy, "setup", "DVB", 8, GOOD, "c")
mx, my = tt(430, 250)
text(mx, my, "stacked = parallel / overlapping calls", "DVS", 8, MUTED, "c")
leg = [(BLUE, "read"), (GOOD, "setup tool"), (GRAY, "discretionary tool")]
lx0, ly0 = tt(70, 292)
for fill, lab in leg:
    c.setFillColor(fill)
    c.roundRect(lx0, T(ly0 + 12), 12, 12, 3, stroke=0, fill=1)
    text(lx0 + 18, ly0 + 10, lab, "DVS", 8, MUTED)
    lx0 += 30 + c.stringWidth(lab, "DVS", 8) + 24
para(px, py + ph + 16, "One session, one turn — context read + setup tools first, then discretionary "
     "drilldowns → answer in ~34s.", "DVS", 10, MUTED, pw)
c.showPage()

# ---------------------------------------------------------------- 12 exp1 why
new_slide()
y = kicker_title("Experiment 1 — Tool Granularity", "Why each setup behaves the way it does")
card(60, y, 410, 122, "With-setup-tools — the only one with setup tools",
     "Broad summary stats (timing, counts, iterators) establish runtime context before discretionary "
     "drilldowns (diagnosis, iterator tree, details). This layering induces a consistent investigation order.",
     GOOD, None, 11)
card(490, y, 410, 122, "Minimal — strong but unguided",
     "Few tools, all necessary — so quality holds and it's marginally fastest. But no setup layer means each "
     "run improvises its own path: more calls, 19 unique trajectories in 20 runs.", BLUE, None, 11)
card(60, y + 136, 410, 122, "Full — over-instrumented",
     "Richest toolset, so the agent explores tools instead of the problem: 390 calls, slowest of the good "
     "setups, no quality gain to show for it.", BLUE, None, 11)
card(490, y + 136, 410, 122, "Baseline — under-powered",
     "Lacks targeted drilldowns, so the agent falls back to raw reads, grep, and bash (49 bash calls) — "
     "workable on simple cases, slow and inconsistent otherwise.", BLUE, None, 11)
callout(y + 276, "Granularity is a design dial: too few tools → fallback improvisation; too many → exploration "
        "overhead. Setup tools + discretionary drilldowns = just right — this is the demonstrated need for "
        "dynamic, runtime exploration.", 840, 11.5)
c.showPage()

# ---------------------------------------------------------------- 13 exp2 setup
new_slide()
y = kicker_title("Experiment 2 — Knowledge Structure", "Raw guide vs. diagnostic playbook")
card(60, y, 410, 120, "Baseline: raw guide",
     "Same agent spec, knowledge provided as a monolithic RQE-Troubleshooting-Guide.md plus best-practices "
     "docs. Agent must find what it needs.", WARN, None, 11.5)
card(490, y, 410, 120, "Treatment: playbook",
     "Same knowledge, restructured as symptoms-router + per-issue handbooks (SQ-04, SQ-07, SQ-11 relevant "
     "to the tasks).", GOOD, None, 11.5)
by = y + 150
for b in ["5 sessions per condition, identical troubleshooting tasks over 5 FT.PROFILE inputs",
          "Both conditions call analyse_ft_profile 25× (5 per session) — tool access held constant",
          "Only the knowledge structure changes"]:
    by = bullet(60, by, b, 13.5, 840) + 14
c.showPage()

# ---------------------------------------------------------------- 14 exp2 quality bars
new_slide()
y = kicker_title("Experiment 2 — Results 1/2", "Answer quality")
c.setFillColor(WARN); c.rect(60, T(y + 12), 12, 12, stroke=0, fill=1)
text(80, y + 11, "Raw guide", "DVS", 11, MUTED)
c.setFillColor(GOOD); c.rect(180, T(y + 12), 12, 12, stroke=0, fill=1)
text(200, y + 11, "Playbook", "DVS", 11, MUTED)
bars = [("First-pass success", 86, 95, "0.86", "0.95"), ("Answer grounding", 27.3, 39.7, "0.27", "0.40"),
        ("Answer specificity", 77.4, 88.8, "0.77", "0.89"), ("Dead-end rate (lower = better)", 21.2, 10.2, "0.21", "0.10")]
byy = y + 40
tw = 480
for lab, v1, v2, s1, s2 in bars:
    text(280, byy + 14, lab, "DVS", 11.5, TEXT, "r")
    for k, (v, sv, col) in enumerate([(v1, s1, WARN), (v2, s2, GOOD)]):
        yy = byy + k * 24
        c.setFillColor(PANEL2); c.roundRect(300, T(yy + 18), tw, 16, 4, stroke=0, fill=1)
        c.setFillColor(col); c.roundRect(300, T(yy + 18), tw * v / 100.0, 16, 4, stroke=0, fill=1)
        text(300 + tw + 14, yy + 15, sv, "DVM", 11, col if k else TEXT)
    byy += 62
text(60, byy + 10, "Mean over 5 sessions per condition · grounding +45% rel., specificity +15% rel., dead-ends −52%",
     "DVS", 10.5, MUTED)
c.showPage()

# ---------------------------------------------------------------- 15 exp2 cost
new_slide()
y = kicker_title("Experiment 2 — Results 2/2", "Cost & reliability")
stats = [("−43%", "total tokens", "326.5k → 185.0k"), ("−48%", "tool error rate", "0.113 → 0.059"),
         ("−15%", "wall-clock duration", "79.8s → 67.6s")]
for i, (big, lab, det) in enumerate(stats):
    x = 60 + i * 290
    rrect(x, y, 270, 110)
    text(x + 135, y + 52, big, "DVB", 30, GOOD, "c")
    text(x + 135, y + 76, lab, "DVS", 11, MUTED, "c")
    text(x + 135, y + 94, det, "DVM", 10, MUTED, "c")
ty = y + 140
trows = [("Metric", "Raw guide", "Playbook", True),
         ("Cache-read tokens", "218,573", "108,442 (−50%)", False),
         ("Backtracking rate", "0.015", "0.000", False),
         ("Repeated doc reads / session", "0.80", "0.00", False),
         ("Unique documents covered", "8.6", "9.8", False),
         ("Required docs read in all 5 sessions", "no (3/5 typical)", "yes (5/5)", False)]
for lab, a, b, hdr in trows:
    text(60, ty, lab, "DVB" if hdr else "DVS", 11.5, MUTED if hdr else TEXT)
    text(640, ty, a, "DVB" if hdr else "DVM", 11.5, MUTED if hdr else TEXT, "r")
    text(880, ty, b, "DVB" if hdr else "DVM", 11.5, MUTED if hdr else GOOD, "r")
    c.setStrokeColor(BORDER); c.setLineWidth(0.8); c.line(60, T(ty + 10), 900, T(ty + 10))
    ty += 29
c.showPage()

# ---------------------------------------------------------------- 16 eval-led design
new_slide()
y = kicker_title("Evaluation-Led Design", "The metrics also tell us what's still broken")
by = y + 8
for b in ["Hallucinated-path rate stays high in both (0.94 → 0.86): agents still guess near-miss paths "
          "(ft.profile vs ftProfile1.txt) before consulting the router — the next spec iteration targets this directly",
          "Grounding variance went up (σ 0.05 → 0.14) even as the mean improved: one or two playbook sessions "
          "skip an optional handbook — coverage tables point at exactly which reads were missed",
          "Exploration breadth/depth barely moved — the playbook doesn't make the agent less curious, it makes "
          "exploration land"]:
    by = bullet(60, by, b, 13, 840) + 16
callout(by + 14, "This is the loop: trajectory metrics → identify failure mode → revise spec/playbook → "
        "re-measure. Design choices become testable hypotheses.")
c.showPage()

# ---------------------------------------------------------------- 17 takeaways
new_slide()
y = kicker_title("Takeaways", "Three things to steal")
items = [("1", "Evaluation-led spec design",
          "Let trajectory metrics — not intuition — drive how you structure agent specs, tool granularity, "
          "and ordering constraints."),
         ("2", "KB → playbook pipelines",
          "Restructure human knowledge bases into routed diagnostic playbooks: where to look, what to "
          "interpret, how to act — for efficient, reliable trajectories."),
         ("3", "Measure the trajectory",
          "Correctness, completeness, and usefulness of the answer are downstream of tool-call coverage, "
          "ordering, and duplication. Instrument those.")]
for i, (num, t1, body) in enumerate(items):
    x = 60 + i * 290
    rrect(x, y, 270, 190)
    text(x + 16, y + 40, num, "DVB", 28, ACCENT)
    text(x + 16, y + 68, t1, "DVB", 12.5, BLUE)
    para(x + 16, y + 90, body, "DVS", 10.5, TEXT, 238)
callout(y + 214, "Same model, same tools, same knowledge — structure alone bought ~half the cost and a large "
        "step toward deterministic, debuggable trajectories.")
text(60, 505, "Srijith Rajamohan · srijith.rajamohan@redis.com", "DVS", 10.5, MUTED)
c.showPage()

c.save()
print("wrote", OUT)
