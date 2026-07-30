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

function drawPerTaskChart(data) {
  const root = document.querySelector("#per-task-chart");
  const width = 760;
  const rowHeight = 43;
  const margin = { top: 28, right: 50, bottom: 22, left: 145 };
  const innerWidth = width - margin.left - margin.right;
  const height = margin.top + margin.bottom + data.length * rowHeight;
  const x = (value) => margin.left + (value / 100) * innerWidth;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });

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
    const rowY = margin.top + index * rowHeight;
    const group = svgEl("g");
    const bars = [
      { value: item.instruct, y: rowY + 5, color: COLORS.blue, label: "Instruct" },
      {
        value: item.remis_instruct,
        y: rowY + 22,
        color: COLORS.red,
        label: "Remis + Instruct",
      },
    ];

    group.append(
      svgEl(
        "text",
        {
          x: margin.left - 12,
          y: rowY + 18,
          fill: COLORS.ink,
          "font-size": 10.5,
          "font-weight": 700,
          "text-anchor": "end",
        },
        item.task,
      ),
      svgEl(
        "text",
        {
          x: margin.left - 12,
          y: rowY + 32,
          fill: COLORS.muted,
          "font-size": 9,
          "text-anchor": "end",
        },
        `n=${item.count}`,
      ),
    );

    bars.forEach((bar) => {
      group.append(
        svgEl("rect", {
          x: margin.left,
          y: bar.y,
          width: Math.max(2, x(bar.value) - margin.left),
          height: 11,
          fill: bar.color,
        }),
        svgEl(
          "text",
          {
            x: x(bar.value) + 7,
            y: bar.y + 9,
            fill: COLORS.ink,
            "font-size": 10,
            "font-weight": 700,
          },
          `${bar.value.toFixed(1)}%`,
        ),
      );
    });

    makeInteractive(
      group,
      `${item.task}: Instruct ${item.instruct.toFixed(1)}%, Remis + Instruct ${item.remis_instruct.toFixed(1)}%`,
      `<strong>${item.task}</strong> (n=${item.count})<br>Instruct: ${item.instruct.toFixed(1)}%<br>Remis + Instruct: ${item.remis_instruct.toFixed(1)}%`,
    );
    svg.append(group);
  });

  root.replaceChildren(svg);
}

function drawLeaderboard(data) {
  const root = document.querySelector("#leaderboard-chart");
  const rows = [...data].sort((a, b) => b.accuracy - a.accuracy);
  const width = 760;
  const rowHeight = 29;
  const margin = { top: 28, right: 42, bottom: 24, left: 180 };
  const innerWidth = width - margin.left - margin.right;
  const height = margin.top + margin.bottom + rows.length * rowHeight;
  const x = (value) => margin.left + (value / 100) * innerWidth;
  const svg = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
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
          y: 14,
          fill: COLORS.muted,
          "font-size": 10,
          "text-anchor": "middle",
        },
        `${tick}%`,
      ),
    );
  });

  rows.forEach((item, index) => {
    const rowY = margin.top + index * rowHeight;
    const barHeight = 15;
    const fill = kindColor[item.kind];
    const group = svgEl("g");

    group.append(
      svgEl(
        "text",
        {
          x: margin.left - 12,
          y: rowY + 11,
          fill: COLORS.ink,
          "font-size": 10,
          "font-weight": item.kind === "redis" ? 800 : 600,
          "text-anchor": "end",
        },
        item.name,
      ),
      svgEl("rect", {
        x: margin.left,
        y: rowY,
        width: Math.max(2, x(item.accuracy) - margin.left),
        height: barHeight,
        fill,
        opacity: item.kind === "published" ? 0.72 : 1,
      }),
      svgEl(
        "text",
        {
          x: x(item.accuracy) + 8,
          y: rowY + 11,
          fill: COLORS.ink,
          "font-size": 10,
          "font-weight": 750,
        },
        `${item.accuracy.toFixed(1)}%`,
      ),
    );

    if (item.flag) {
      group.append(
        svgEl(
          "text",
          {
            x: x(item.accuracy) - 8,
            y: rowY + 10.8,
            fill: COLORS.white,
            "font-size": 7.5,
            "font-weight": 800,
            "letter-spacing": 0.3,
            "text-anchor": "end",
          },
          item.flag.toUpperCase(),
        ),
      );
    }

    if (item.kind === "published") {
      makeInteractive(
        group,
        `${item.name}: ${item.accuracy}% published accuracy`,
        `<strong>${item.name}</strong><br>${item.accuracy.toFixed(1)}% published accuracy<br>${item.note}`,
      );
    }
    svg.append(group);
  });

  root.replaceChildren(svg);
}

function drawCostChart(data) {
  const root = document.querySelector("#cost-chart");
  const width = 760;
  const height = 400;
  const margin = { top: 22, right: 78, bottom: 58, left: 58 };
  const innerWidth = width - margin.left - margin.right;
  const innerHeight = height - margin.top - margin.bottom;
  const minCost = 4;
  const maxCost = 200;
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

  [5, 10, 20, 50, 100, 200].forEach((tick) => {
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
        `$${tick}`,
      ),
    );
  });

  svg.append(
    svgEl(
      "text",
      {
        x: margin.left + innerWidth / 2,
        y: height - 10,
        fill: COLORS.muted,
        "font-size": 12,
        "text-anchor": "middle",
      },
      "Modeled LLM cost per 1M conversation tokens (USD, log scale)",
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
    "Remis + Instruct": [12, -18],
    "Mastra OM": [-12, -13],
    "emergence-fast": [-12, 19],
    Instruct: [12, -14],
    langmem: [25, 18],
    "Google Vertex Memory Bank": [25, 18],
  };

  data.forEach((item) => {
    const cost = item.cost_usd_per_million_conversation_tokens;
    const px = x(cost);
    const py = y(item.accuracy);
    const [dx, dy] = labelOffsets[item.name] ?? [10, -10];
    const anchor = dx < 0 ? "end" : "start";
    const group = svgEl("g");
    const fill =
      item.kind === "redis"
        ? COLORS.red
        : item.kind === "reproduced"
          ? COLORS.orange
          : COLORS.published;

    if (item.lower_bound) {
      group.append(
        svgEl("line", {
          x1: px + 6,
          x2: px + 18,
          y1: py,
          y2: py,
          stroke: fill,
          "stroke-width": 2,
        }),
        svgEl("path", {
          d: `M ${px + 18} ${py} l -5 -4 v 8 z`,
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
      `${item.name}: ${item.accuracy}% accuracy at ${item.lower_bound ? "at least " : ""}$${cost.toFixed(2)} per 1M conversation tokens`,
      `<strong>${item.name}</strong><br>${item.accuracy.toFixed(1)}% task-averaged accuracy<br>${item.lower_bound ? "At least " : ""}$${cost.toFixed(2)} modeled LLM cost per 1M conversation tokens${item.lower_bound ? "<br>Server-side cost is not fully observed." : ""}${item.note ? `<br>${item.note}` : ""}`,
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

async function initCharts() {
  try {
    const response = await fetch("data/results.json?v=20260730-minimal-tooltips");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    drawPerTaskChart(data.per_task_comparison);
    drawLeaderboard(data.leaderboard);
    drawCostChart(data.cost_accuracy);
  } catch (error) {
    document.querySelectorAll(".chart").forEach((chart) => {
      chart.innerHTML =
        '<p style="padding:2rem;color:#68645d">Charts require the page to be served over HTTP. Run the local preview command in README.md.</p>';
    });
    console.error("Unable to load chart data:", error);
  }
}

initCharts();
