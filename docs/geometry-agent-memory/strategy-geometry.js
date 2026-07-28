const SVG_NS = "http://www.w3.org/2000/svg";
const WIDTH = 720;
const HEIGHT = 560;
const PADDING = 34;

function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function paddedBounds(points, padding = 0.12) {
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const xRange = Math.max(xMax - xMin, 1);
  const yRange = Math.max(yMax - yMin, 1);
  return {
    xMin: xMin - xRange * padding,
    xMax: xMax + xRange * padding,
    yMin: yMin - yRange * padding,
    yMax: yMax + yRange * padding,
  };
}

function projector(bounds, width = WIDTH, height = HEIGHT, padding = PADDING) {
  const xRange = bounds.xMax - bounds.xMin || 1;
  const yRange = bounds.yMax - bounds.yMin || 1;
  return (point) => ({
    x: padding + ((point.x - bounds.xMin) / xRange) * (width - padding * 2),
    y: height - padding - ((point.y - bounds.yMin) / yRange) * (height - padding * 2),
  });
}

function starPoints(center, outer = 10, inner = 4.4) {
  const points = [];
  for (let index = 0; index < 10; index += 1) {
    const angle = -Math.PI / 2 + (index * Math.PI) / 5;
    const radius = index % 2 === 0 ? outer : inner;
    points.push(`${center.x + Math.cos(angle) * radius},${center.y + Math.sin(angle) * radius}`);
  }
  return points.join(" ");
}

function groupTurns(turns) {
  const sessions = new Map();
  turns.forEach((turn) => {
    if (!sessions.has(turn.session)) sessions.set(turn.session, []);
    sessions.get(turn.session).push(turn);
  });
  sessions.forEach((session) => session.sort((a, b) => a.turn - b.turn));
  return sessions;
}

function shortText(text, length = 360) {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > length ? `${clean.slice(0, length)}…` : clean;
}

async function initStrategyGeometry() {
  const explorer = document.querySelector("#strategy-geometry-explorer");
  if (!explorer) return;

  const response = await fetch(explorer.dataset.source);
  if (!response.ok) throw new Error(`Could not load geometry data (${response.status})`);
  const data = await response.json();
  const panels = explorer.querySelector(".geometry-panels");
  const focusList = explorer.querySelector(".geometry-focus-list");
  const tooltip = explorer.querySelector(".geometry-tooltip");
  const zoomStatus = explorer.querySelector("[data-zoom-status]");
  const viewportGroups = [];
  const camera = { scale: 1, x: 0, y: 0 };
  const center = { x: WIDTH / 2, y: HEIGHT / 2 };

  const showTooltip = (event, title, text, detail = "") => {
    tooltip.replaceChildren();
    const heading = document.createElement("strong");
    heading.textContent = title;
    const body = document.createElement("span");
    body.textContent = shortText(text);
    tooltip.append(heading, body);
    if (detail) {
      const meta = document.createElement("small");
      meta.textContent = detail;
      tooltip.append(meta);
    }
    tooltip.hidden = false;
    moveTooltip(event);
  };

  const moveTooltip = (event) => {
    const gap = 16;
    const width = tooltip.offsetWidth || 320;
    const height = tooltip.offsetHeight || 120;
    tooltip.style.left = `${Math.min(event.clientX + gap, window.innerWidth - width - gap)}px`;
    tooltip.style.top = `${Math.min(event.clientY + gap, window.innerHeight - height - gap)}px`;
  };

  const hideTooltip = () => {
    tooltip.hidden = true;
  };

  const bindTooltip = (node, title, text, detail) => {
    node.addEventListener("pointerenter", (event) => showTooltip(event, title, text, detail));
    node.addEventListener("pointermove", moveTooltip);
    node.addEventListener("pointerleave", hideTooltip);
  };

  function addPlotContent(svg, strategyKey, bounds, options = {}) {
    const strategy = data.strategies[strategyKey];
    const selectedSessions = options.sessions ? new Set(options.sessions) : null;
    const turns = selectedSessions
      ? data.turns.filter((turn) => selectedSessions.has(turn.session))
      : data.turns;
    const memories = selectedSessions
      ? strategy.memories.filter((memory) =>
          selectedSessions.has(data.turns[memory.nearestTurn].session),
        )
      : strategy.memories;
    const project = projector(
      bounds,
      options.width || WIDTH,
      options.height || HEIGHT,
      options.padding || PADDING,
    );
    const clipId = `geometry-clip-${strategyKey}-${Math.random().toString(36).slice(2)}`;
    const defs = svgElement("defs");
    const clipPath = svgElement("clipPath", { id: clipId });
    clipPath.append(
      svgElement("rect", {
        x: 0,
        y: 0,
        width: options.width || WIDTH,
        height: options.height || HEIGHT,
      }),
    );
    defs.append(clipPath);
    svg.append(defs);

    const viewport = svgElement("g", {
      class: "geometry-viewport",
      "clip-path": `url(#${clipId})`,
    });
    const pathLayer = svgElement("g", { class: "geometry-path-layer" });
    const turnLayer = svgElement("g", { class: "geometry-turn-layer" });
    const memoryLayer = svgElement("g", { class: "geometry-memory-layer" });
    const queryLayer = svgElement("g", { class: "geometry-query-layer" });

    groupTurns(turns).forEach((session) => {
      if (session.length < 2) return;
      const path = session
        .map((turn, index) => {
          const point = project(turn);
          return `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
        })
        .join(" ");
      pathLayer.append(svgElement("path", { d: path, class: "conversation-path" }));
    });

    turns.forEach((turn) => {
      const point = project(turn);
      const circle = svgElement("circle", {
        cx: point.x,
        cy: point.y,
        r: options.closeup ? 3.7 : 2.25,
        class: "conversation-turn",
      });
      bindTooltip(
        circle,
        `Conversation turn · session ${turn.session + 1} · ${turn.speaker}`,
        turn.text,
        turn.sessionLabel,
      );
      turnLayer.append(circle);
    });

    memories.forEach((memory) => {
      const point = project(memory);
      const circle = svgElement("circle", {
        cx: point.x,
        cy: point.y,
        r: options.closeup ? 6 : 3.45,
        class: "stored-memory",
      });
      bindTooltip(
        circle,
        `${strategy.name} memory`,
        memory.text,
        `Nearest-turn cosine distance ${memory.nearestTurnDistance.toFixed(2)}`,
      );
      memoryLayer.append(circle);
    });

    if (options.includeQuery !== false) {
      const queryPoint = project(data.question);
      const star = svgElement("polygon", {
        points: starPoints(queryPoint, options.closeup ? 13 : 10),
        class: "test-question",
      });
      bindTooltip(star, "Test question", data.question.text, "Shown in both strategy panels");
      queryLayer.append(star);
    }

    viewport.append(pathLayer, turnLayer, memoryLayer, queryLayer);
    svg.append(viewport);
    return { viewport, turns, memories };
  }

  function updateCamera() {
    const transform = [
      `translate(${camera.x} ${camera.y})`,
      `translate(${center.x} ${center.y})`,
      `scale(${camera.scale})`,
      `translate(${-center.x} ${-center.y})`,
    ].join(" ");
    viewportGroups.forEach((group) => group.setAttribute("transform", transform));
    zoomStatus.textContent = `${camera.scale.toFixed(1)}×`;
  }

  function zoomTo(nextScale, origin = center) {
    const scale = Math.max(1, Math.min(12, nextScale));
    const ratio = scale / camera.scale;
    camera.x = origin.x - center.x - ratio * (origin.x - center.x - camera.x);
    camera.y = origin.y - center.y - ratio * (origin.y - center.y - camera.y);
    camera.scale = scale;
    if (scale === 1) {
      camera.x = 0;
      camera.y = 0;
    }
    updateCamera();
  }

  function attachNavigation(svg) {
    let dragging = false;
    let last = null;

    svg.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        const rect = svg.getBoundingClientRect();
        const origin = {
          x: ((event.clientX - rect.left) / rect.width) * WIDTH,
          y: ((event.clientY - rect.top) / rect.height) * HEIGHT,
        };
        zoomTo(camera.scale * Math.exp(-event.deltaY * 0.0015), origin);
      },
      { passive: false },
    );

    svg.addEventListener("pointerdown", (event) => {
      dragging = true;
      last = { x: event.clientX, y: event.clientY };
      svg.setPointerCapture(event.pointerId);
      svg.classList.add("is-dragging");
    });
    svg.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const rect = svg.getBoundingClientRect();
      camera.x += ((event.clientX - last.x) / rect.width) * WIDTH;
      camera.y += ((event.clientY - last.y) / rect.height) * HEIGHT;
      last = { x: event.clientX, y: event.clientY };
      updateCamera();
    });
    const stopDragging = (event) => {
      if (!dragging) return;
      dragging = false;
      svg.releasePointerCapture(event.pointerId);
      svg.classList.remove("is-dragging");
    };
    svg.addEventListener("pointerup", stopDragging);
    svg.addEventListener("pointercancel", stopDragging);
    svg.addEventListener("dblclick", () => zoomTo(1));
  }

  Object.entries(data.strategies).forEach(([strategyKey, strategy]) => {
    const panel = document.createElement("article");
    panel.className = "geometry-panel";
    const header = document.createElement("header");
    header.innerHTML = `
      <div>
        <span>${strategy.subtitle}</span>
        <h4>${strategy.name}</h4>
      </div>
      <small>${strategy.memories.length} memories</small>
    `;
    const svg = svgElement("svg", {
      viewBox: `0 0 ${WIDTH} ${HEIGHT}`,
      role: "img",
      "aria-label": `${strategy.name} memory geometry; scroll to zoom and drag to pan`,
    });
    const { viewport } = addPlotContent(svg, strategyKey, data.bounds);
    viewportGroups.push(viewport);
    attachNavigation(svg);
    panel.append(header, svg);
    panels.append(panel);
  });

  explorer.querySelector("[data-zoom-in]").addEventListener("click", () => {
    zoomTo(camera.scale * 1.5);
  });
  explorer.querySelector("[data-zoom-out]").addEventListener("click", () => {
    zoomTo(camera.scale / 1.5);
  });
  explorer.querySelector("[data-zoom-reset]").addEventListener("click", () => {
    zoomTo(1);
  });

  data.focuses.forEach((focus) => {
    const focusTurns = data.turns.filter((turn) => focus.sessions.includes(turn.session));
    const focusMemories = Object.values(data.strategies).flatMap((strategy) =>
      strategy.memories.filter((memory) =>
        focus.sessions.includes(data.turns[memory.nearestTurn].session),
      ),
    );
    const boundsPoints = [...focusTurns, ...focusMemories];
    if (focus.includeQuery) boundsPoints.push(data.question);
    const bounds = paddedBounds(boundsPoints, 0.17);

    const article = document.createElement("article");
    article.className = "geometry-focus";
    const copy = document.createElement("div");
    copy.className = "geometry-focus-copy";
    copy.innerHTML = `
      <span>${focus.label}</span>
      <h4>${focus.title}</h4>
      <p><strong>Conversation.</strong> ${focus.conversation}</p>
      <p><strong>What changes.</strong> ${focus.explanation}</p>
    `;
    const diagrams = document.createElement("div");
    diagrams.className = "geometry-focus-diagrams";

    Object.entries(data.strategies).forEach(([strategyKey, strategy]) => {
      const wrapper = document.createElement("div");
      wrapper.className = "geometry-focus-panel";
      const relevantMemories = strategy.memories.filter((memory) =>
        focus.sessions.includes(data.turns[memory.nearestTurn].session),
      );
      const title = document.createElement("div");
      title.className = "geometry-focus-title";
      title.innerHTML = `<strong>${strategy.name}</strong><span>${focusTurns.length} turns · ${relevantMemories.length} memories</span>`;
      const svg = svgElement("svg", {
        viewBox: "0 0 480 300",
        role: "img",
        "aria-label": `${strategy.name} close-up for ${focus.title}`,
      });
      addPlotContent(svg, strategyKey, bounds, {
        sessions: focus.sessions,
        includeQuery: focus.includeQuery,
        closeup: true,
        width: 480,
        height: 300,
        padding: 24,
      });
      wrapper.append(title, svg);
      diagrams.append(wrapper);
    });

    article.append(copy, diagrams);
    focusList.append(article);
  });

  explorer.classList.add("is-ready");
  updateCamera();
}

initStrategyGeometry().catch((error) => {
  const explorer = document.querySelector("#strategy-geometry-explorer");
  if (!explorer) return;
  explorer.classList.add("has-error");
  const message = explorer.querySelector(".geometry-error");
  message.hidden = false;
  message.textContent = `The interactive figure could not load: ${error.message}`;
});
