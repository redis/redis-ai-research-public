const COLORS = {
  ink: "#171716",
  muted: "#68645d",
  line: "#cfc6b8",
  paper: "#f5f1e8",
  red: "#d52b1e",
  orange: "#d77b2d",
  published: "#918b82",
  blue: "#42657a",
  white: "#fffdf8",
};

const tooltip = document.querySelector("#chart-tooltip");

function svgEl(name, attrs = {}, text = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (text) node.textContent = text;
  return node;
}

function showTooltip(event, html) {
  tooltip.innerHTML = html;
  tooltip.classList.add("visible");
  const point = event.touches?.[0] ?? event;
  tooltip.style.left = `${Math.max(145, Math.min(window.innerWidth - 145, point.clientX))}px`;
  tooltip.style.top = `${Math.max(120, point.clientY)}px`;
}

function hideTooltip() {
  tooltip.classList.remove("visible");
}

function makeInteractive(node, label, html) {
  node.setAttribute("tabindex", "0");
  node.setAttribute("role", "button");
  node.setAttribute("aria-label", label);
  node.addEventListener("pointerenter", (event) => showTooltip(event, html));
  node.addEventListener("pointermove", (event) => showTooltip(event, html));
  node.addEventListener("pointerleave", hideTooltip);
  node.addEventListener("focus", () => {
    const rect = node.getBoundingClientRect();
    showTooltip(
      { clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 },
      html,
    );
  });
  node.addEventListener("blur", hideTooltip);
}

function addGrid(svg, { x, y, width, height, values, scale, format = String }) {
  values.forEach((value) => {
    const gy = scale(value);
    svg.append(
      svgEl("line", {
        x1: x,
        x2: x + width,
        y1: gy,
        y2: gy,
        stroke: COLORS.line,
        "stroke-width": 1,
      }),
      svgEl(
        "text",
        {
          x: x - 10,
          y: gy + 4,
          fill: COLORS.muted,
          "font-size": 11,
          "text-anchor": "end",
        },
        format(value),
      ),
    );
  });

  svg.append(
    svgEl("line", {
      x1: x,
      x2: x,
      y1: y,
      y2: y + height,
      stroke: COLORS.ink,
      "stroke-width": 1,
    }),
  );
}

function drawStrategyChart(data) {
  const root = document.querySelector("#strategy-chart");
  const width = 900;
  const height = 350;
  const margin = { top: 22, right: 35, bottom: 66, left: 55 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    "aria-hidden": "true",
  });
  const y = (value) => margin.top + innerHeight - (value / 100) * innerHeight;

  addGrid(svg, {
    x: margin.left,
    y: margin.top,
    width: innerWidth,
    height: innerHeight,
    values: [0, 20, 40, 60, 80, 100],
    scale: y,
    format: (value) => `${value}`,
  });

  const slot = innerWidth / data.length;
  const barWidth = Math.min(145, slot * 0.54);

  data.forEach((item, index) => {
    const x = margin.left + index * slot + (slot - barWidth) / 2;
    const barY = y(item.task_averaged_accuracy);
    const group = svgEl("g");
    const fill = index === data.length - 1 ? COLORS.red : index === 1 ? COLORS.orange : COLORS.blue;
    const bar = svgEl("rect", {
      x,
      y: barY,
      width: barWidth,
      height: margin.top + innerHeight - barY,
      fill,
    });

    group.append(
      bar,
      svgEl(
        "text",
        {
          x: x + barWidth / 2,
          y: barY - 12,
          fill: COLORS.ink,
          "font-family": "Georgia, serif",
          "font-size": 22,
          "font-weight": 700,
          "text-anchor": "middle",
        },
        `${item.task_averaged_accuracy.toFixed(1)}%`,
      ),
      svgEl(
        "text",
        {
          x: x + barWidth / 2,
          y: margin.top + innerHeight + 27,
          fill: COLORS.ink,
          "font-size": 12,
          "font-weight": 700,
          "text-anchor": "middle",
        },
        item.name,
      ),
    );

    makeInteractive(
      group,
      `${item.name}: ${item.task_averaged_accuracy}% task-averaged accuracy`,
      `<strong>${item.name}</strong><br>Task-averaged: <strong>${item.task_averaged_accuracy.toFixed(1)}%</strong><br>${item.note}`,
    );
    svg.append(group);
  });

  root.replaceChildren(svg);
}

function drawLeaderboard(data) {
  const root = document.querySelector("#leaderboard-chart");
  const width = 1040;
  const rowHeight = 28;
  const margin = { top: 27, right: 45, bottom: 30, left: 205 };
  const innerWidth = width - margin.left - margin.right;
  const height = margin.top + margin.bottom + data.length * rowHeight;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
  const x = (value) => margin.left + (value / 100) * innerWidth;
  const kindColor = {
    redis: COLORS.red,
    reproduced: COLORS.orange,
    published: COLORS.published,
  };

  [0, 20, 40, 60, 80, 100].forEach((tick) => {
    const gx = x(tick);
    svg.append(
      svgEl("line", {
        x1: gx,
        x2: gx,
        y1: margin.top - 10,
        y2: height - margin.bottom,
        stroke: COLORS.line,
      }),
      svgEl(
        "text",
        {
          x: gx,
          y: 13,
          fill: COLORS.muted,
          "font-size": 10,
          "text-anchor": "middle",
        },
        `${tick}%`,
      ),
    );
  });

  data.forEach((item, index) => {
    const y = margin.top + index * rowHeight;
    const barHeight = 16;
    const group = svgEl("g");
    group.append(
      svgEl(
        "text",
        {
          x: margin.left - 12,
          y: y + 12,
          fill: COLORS.ink,
          "font-size": 10.5,
          "font-weight": item.kind === "redis" ? 750 : 500,
          "text-anchor": "end",
        },
        item.name,
      ),
      svgEl("rect", {
        x: margin.left,
        y,
        width: Math.max(2, x(item.accuracy) - margin.left),
        height: barHeight,
        fill: kindColor[item.kind],
        opacity: item.kind === "published" ? 0.72 : 1,
      }),
      svgEl(
        "text",
        {
          x: x(item.accuracy) + 8,
          y: y + 12,
          fill: COLORS.ink,
          "font-size": 10.5,
          "font-weight": 700,
        },
        item.accuracy.toFixed(1),
      ),
    );

    if (item.flag) {
      group.append(
        svgEl(
          "text",
          {
            x: x(item.accuracy) - 7,
            y: y + 11.5,
            fill: COLORS.white,
            "font-size": 8.5,
            "font-weight": 800,
            "letter-spacing": 0.35,
            "text-anchor": "end",
          },
          item.flag.toUpperCase(),
        ),
      );
    }

    makeInteractive(
      group,
      `${item.name}: ${item.accuracy}% task-averaged accuracy, ${item.kind}`,
      `<strong>${item.name}</strong><br>${item.accuracy.toFixed(1)}% task-averaged accuracy<br>${item.model}<br>${item.note}`,
    );
    svg.append(group);
  });

  root.replaceChildren(svg);
}

function drawCostChart(data) {
  const root = document.querySelector("#cost-chart");
  const width = 980;
  const height = 490;
  const margin = { top: 25, right: 100, bottom: 65, left: 65 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const minCost = 0.008;
  const maxCost = 0.5;
  const minAccuracy = 40;
  const maxAccuracy = 90;
  const logMin = Math.log10(minCost);
  const logMax = Math.log10(maxCost);
  const x = (value) =>
    margin.left + ((Math.log10(value) - logMin) / (logMax - logMin)) * innerWidth;
  const y = (value) =>
    margin.top + innerHeight - ((value - minAccuracy) / (maxAccuracy - minAccuracy)) * innerHeight;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });

  [40, 50, 60, 70, 80, 90].forEach((tick) => {
    const gy = y(tick);
    svg.append(
      svgEl("line", {
        x1: margin.left,
        x2: margin.left + innerWidth,
        y1: gy,
        y2: gy,
        stroke: COLORS.line,
      }),
      svgEl(
        "text",
        {
          x: margin.left - 10,
          y: gy + 4,
          fill: COLORS.muted,
          "font-size": 11,
          "text-anchor": "end",
        },
        `${tick}%`,
      ),
    );
  });

  [0.01, 0.02, 0.05, 0.1, 0.2, 0.5].forEach((tick) => {
    const gx = x(tick);
    svg.append(
      svgEl("line", {
        x1: gx,
        x2: gx,
        y1: margin.top,
        y2: margin.top + innerHeight,
        stroke: COLORS.line,
        "stroke-dasharray": "3 5",
      }),
      svgEl(
        "text",
        {
          x: gx,
          y: margin.top + innerHeight + 28,
          fill: COLORS.muted,
          "font-size": 11,
          "text-anchor": "middle",
        },
        `$${tick.toFixed(2)}`,
      ),
    );
  });

  svg.append(
    svgEl(
      "text",
      {
        x: margin.left + innerWidth / 2,
        y: height - 12,
        fill: COLORS.muted,
        "font-size": 12,
        "text-anchor": "middle",
      },
      "Estimated LLM cost per session (USD, log scale)",
    ),
    svgEl(
      "text",
      {
        x: 16,
        y: margin.top + innerHeight / 2,
        fill: COLORS.muted,
        "font-size": 12,
        "text-anchor": "middle",
        transform: `rotate(-90 16 ${margin.top + innerHeight / 2})`,
      },
      "Task-averaged accuracy (%)",
    ),
  );

  const labelOffsets = {
    "Remis + Instruct": [12, -15],
    Remis: [12, 18],
    Instruct: [12, -8],
    "Mastra OM": [-12, -13],
    "emergence-fast": [-12, 19],
    "Amazon AgentCore": [12, -13],
    langmem: [12, -14],
    "Google Vertex MB": [12, 18],
  };

  data.forEach((item) => {
    const px = x(item.cost_usd);
    const py = y(item.accuracy);
    const [dx, dy] = labelOffsets[item.name] ?? [10, -10];
    const anchor = dx < 0 ? "end" : "start";
    const group = svgEl("g");
    const fill = item.kind === "redis" ? COLORS.red : COLORS.orange;

    if (item.lower_bound) {
      group.append(
        svgEl("line", {
          x1: px - 18,
          x2: px - 6,
          y1: py,
          y2: py,
          stroke: fill,
          "stroke-width": 2,
        }),
        svgEl("path", {
          d: `M ${px - 18} ${py} l 5 -4 v 8 z`,
          fill,
        }),
      );
    }

    group.append(
      svgEl("circle", {
        cx: px,
        cy: py,
        r: item.name === "Remis + Instruct" ? 9 : 6.5,
        fill,
        stroke: item.name === "Remis + Instruct" ? COLORS.ink : "none",
        "stroke-width": 2,
      }),
      svgEl(
        "text",
        {
          x: px + dx,
          y: py + dy,
          fill: COLORS.ink,
          "font-size": item.name === "Remis + Instruct" ? 12 : 10.5,
          "font-weight": item.name === "Remis + Instruct" ? 800 : 600,
          "text-anchor": anchor,
        },
        item.name,
      ),
    );

    makeInteractive(
      group,
      `${item.name}: ${item.accuracy}% accuracy at ${item.lower_bound ? "at least " : ""}$${item.cost_usd} per session`,
      `<strong>${item.name}</strong><br>${item.accuracy.toFixed(1)}% task-averaged accuracy<br>${item.lower_bound ? "At least " : ""}$${item.cost_usd.toFixed(4)} estimated LLM cost/session${item.lower_bound ? "<br>Server-side cost is not fully observed." : ""}${item.note ? `<br>${item.note}` : ""}`,
    );
    svg.append(group);
  });

  root.replaceChildren(svg);
}

function drawExtractionChart(data) {
  const root = document.querySelector("#extraction-chart");
  const width = 980;
  const height = 520;
  const margin = { top: 30, right: 45, bottom: 80, left: 65 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const minCost = 0.15;
  const maxCost = 30;
  const minAccuracy = 20;
  const maxAccuracy = 90;
  const logMin = Math.log10(minCost);
  const logMax = Math.log10(maxCost);
  const x = (value) =>
    margin.left + ((Math.log10(value) - logMin) / (logMax - logMin)) * innerWidth;
  const y = (value) =>
    margin.top + innerHeight - ((value - minAccuracy) / (maxAccuracy - minAccuracy)) * innerHeight;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });

  [20, 30, 40, 50, 60, 70, 80, 90].forEach((tick) => {
    const gy = y(tick);
    svg.append(
      svgEl("line", {
        x1: margin.left,
        x2: margin.left + innerWidth,
        y1: gy,
        y2: gy,
        stroke: COLORS.line,
      }),
      svgEl(
        "text",
        {
          x: margin.left - 10,
          y: gy + 4,
          fill: COLORS.muted,
          "font-size": 11,
          "text-anchor": "end",
        },
        `${tick}%`,
      ),
    );
  });

  [0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 20, 30].forEach((tick) => {
    const gx = x(tick);
    svg.append(
      svgEl("line", {
        x1: gx,
        x2: gx,
        y1: margin.top,
        y2: margin.top + innerHeight,
        stroke: COLORS.line,
        "stroke-dasharray": "3 5",
        opacity: 0.7,
      }),
      svgEl(
        "text",
        {
          x: gx,
          y: margin.top + innerHeight + 27,
          fill: COLORS.muted,
          "font-size": 10,
          "text-anchor": "middle",
        },
        `$${tick}`,
      ),
    );
  });

  svg.append(
    svgEl(
      "text",
      {
        x: margin.left + innerWidth / 2,
        y: height - 13,
        fill: COLORS.muted,
        "font-size": 12,
        "text-anchor": "middle",
      },
      "Cost per 1M conversation tokens (USD, log scale)",
    ),
    svgEl(
      "text",
      {
        x: 17,
        y: margin.top + innerHeight / 2,
        fill: COLORS.muted,
        "font-size": 12,
        "text-anchor": "middle",
        transform: `rotate(-90 17 ${margin.top + innerHeight / 2})`,
      },
      "Question-level accuracy",
    ),
  );

  const labelBelow = new Set([
    "gpt-5-nano:high",
    "gpt-5-mini:minimal",
    "gpt-5:minimal",
    "gpt-4o:non-reasoning",
  ]);

  data.series.forEach((series) => {
    if (series.points.length > 1) {
      const path = series.points
        .map((point, index) => {
          const command = index === 0 ? "M" : "L";
          return `${command} ${x(point.cost_per_million_conversation_tokens)} ${y(point.accuracy)}`;
        })
        .join(" ");
      svg.append(
        svgEl("path", {
          d: path,
          fill: "none",
          stroke: series.color,
          "stroke-width": 2.5,
          opacity: 0.9,
        }),
      );
    }

    series.points.forEach((point) => {
      const px = x(point.cost_per_million_conversation_tokens);
      const py = y(point.accuracy);
      const group = svgEl("g");
      const key = `${series.name}:${point.effort}`;
      const labelY = labelBelow.has(key) ? py + 23 : py - 13;

      if (series.name === "gpt-4o") {
        group.append(
          svgEl("rect", {
            x: px - 7,
            y: py - 7,
            width: 14,
            height: 14,
            fill: series.color,
            stroke: COLORS.paper,
            "stroke-width": 2,
          }),
        );
      } else {
        group.append(
          svgEl("circle", {
            cx: px,
            cy: py,
            r: point.accuracy === 82 ? 8 : 6.5,
            fill: series.color,
            stroke: COLORS.paper,
            "stroke-width": 2,
          }),
        );
      }

      group.append(
        svgEl(
          "text",
          {
            x: px,
            y: labelY,
            fill: series.color,
            "font-size": point.accuracy === 82 ? 12 : 10.5,
            "font-weight": point.accuracy === 82 ? 800 : 650,
            "text-anchor": "middle",
          },
          `${point.effort} · ${point.accuracy.toFixed(0)}%`,
        ),
      );

      makeInteractive(
        group,
        `${series.name}, ${point.effort}: ${point.accuracy}% question-level accuracy`,
        `<strong>${series.name} · ${point.effort}</strong><br>${point.accuracy.toFixed(1)}% question-level accuracy<br>${point.task_averaged_accuracy.toFixed(1)}% task-averaged accuracy<br>$${point.cost_per_million_conversation_tokens.toFixed(2)} per 1M conversation tokens<br>${point.ingest_seconds_per_session.toFixed(1)}s mean ingest per session<br>${point.median_memories} median memories per example<br>n=${series.sample_size}`,
      );
      svg.append(group);
    });
  });

  root.replaceChildren(svg);
}

function wrapLabel(svg, label, x, y) {
  const words = label.split(" ");
  const lines = [];
  let current = "";
  words.forEach((word) => {
    if (`${current} ${word}`.trim().length > 22) {
      lines.push(current);
      current = word;
    } else {
      current = `${current} ${word}`.trim();
    }
  });
  lines.push(current);

  const text = svgEl("text", {
    x,
    y,
    fill: COLORS.ink,
    "font-size": 11,
    "font-weight": 650,
    "text-anchor": "middle",
  });
  lines.forEach((line, index) => {
    text.append(svgEl("tspan", { x, dy: index === 0 ? 0 : 14 }, line));
  });
  svg.append(text);
}

function drawReproductionChart(data) {
  const root = document.querySelector("#reproduction-chart");
  const width = 900;
  const height = 390;
  const margin = { top: 22, right: 35, bottom: 85, left: 55 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
  const y = (value) => margin.top + innerHeight - (value / 100) * innerHeight;

  addGrid(svg, {
    x: margin.left,
    y: margin.top,
    width: innerWidth,
    height: innerHeight,
    values: [0, 20, 40, 60, 80, 100],
    scale: y,
  });

  const slot = innerWidth / data.length;
  const pairWidth = Math.min(155, slot * 0.68);
  const barWidth = pairWidth / 2 - 4;

  data.forEach((item, index) => {
    const startX = margin.left + index * slot + (slot - pairWidth) / 2;
    const group = svgEl("g");
    [
      { key: "published", value: item.published, color: COLORS.published },
      { key: "measured", value: item.measured, color: COLORS.orange },
    ].forEach((bar, barIndex) => {
      const bx = startX + barIndex * (barWidth + 8);
      const by = y(bar.value);
      group.append(
        svgEl("rect", {
          x: bx,
          y: by,
          width: barWidth,
          height: margin.top + innerHeight - by,
          fill: bar.color,
        }),
        svgEl(
          "text",
          {
            x: bx + barWidth / 2,
            y: by - 8,
            fill: COLORS.ink,
            "font-size": 11,
            "font-weight": 700,
            "text-anchor": "middle",
          },
          bar.value.toFixed(1),
        ),
      );
    });

    const deltaColor = item.delta_pp > 0 ? COLORS.blue : COLORS.red;
    group.append(
      svgEl(
        "text",
        {
          x: startX + pairWidth / 2,
          y: margin.top + 18,
          fill: deltaColor,
          "font-size": 12,
          "font-weight": 800,
          "text-anchor": "middle",
        },
        `${item.delta_pp > 0 ? "+" : ""}${item.delta_pp.toFixed(1)} pp`,
      ),
    );
    wrapLabel(
      group,
      item.name,
      startX + pairWidth / 2,
      margin.top + innerHeight + 31,
    );

    makeInteractive(
      group,
      `${item.name}: published ${item.published}%, measured ${item.measured}%, difference ${item.delta_pp} percentage points`,
      `<strong>${item.name}</strong><br>Published: ${item.published.toFixed(1)}%<br>Measured: ${item.measured.toFixed(1)}%<br>Difference: ${item.delta_pp > 0 ? "+" : ""}${item.delta_pp.toFixed(1)} pp<br>${item.note}`,
    );
    svg.append(group);
  });

  root.replaceChildren(svg);
}

async function initCharts() {
  try {
    const response = await fetch("data/results.json?v=20260728-compact-footnote");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    drawStrategyChart(data.redis_strategies);
    drawLeaderboard(data.leaderboard);
    drawCostChart(data.cost_accuracy);
    drawReproductionChart(data.published_vs_measured);
  } catch (error) {
    document.querySelectorAll(".chart").forEach((chart) => {
      chart.innerHTML =
        '<p style="padding:2rem;color:#68645d">Charts require the page to be served over HTTP. Run the local preview command in README.md.</p>';
    });
    console.error("Unable to load chart data:", error);
  }
}

initCharts();
