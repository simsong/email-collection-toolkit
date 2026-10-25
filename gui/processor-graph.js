/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
/* Draw manifest-derived processor DAGs in a dedicated read-only window.
   Dagre lays out each pipeline; SVG renders selectable labels and directed edges.
   MIME extraction's open-ended outputs use dashed edges and a terminal Other node.
   Bridge generations discard obsolete refresh replies without clearing a good graph.
   Fit/zoom controls resize the diagrams without rerunning processors or reading mail.
   DOM text APIs keep custom plugin labels separate from HTML and SVG markup. */
"use strict";

const byId = id => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const titles = {ingest: "1 · Ingest", message: "2 · Message processing", content: "3 · Content processing"};
let initialized = false;
let generation = 0;
let zoom = null;
let diagrams = [];

function element(name, attributes = {}, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

function labelLines(label) {
  return label.match(/.{1,28}(?:[/-]|$)|.{1,28}/g) || [label];
}

function draw(pipeline) {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({rankdir: "LR", nodesep: 28, ranksep: 55, marginx: 22, marginy: 22});
  graph.setDefaultEdgeLabel(() => ({}));
  const canvas = document.createElement("canvas").getContext("2d");
  canvas.font = "13px -apple-system, BlinkMacSystemFont, sans-serif";
  for (const node of pipeline.nodes) {
    const lines = labelLines(node.label);
    const width = Math.max(110, ...lines.map(line => canvas.measureText(line).width + 28));
    graph.setNode(node.id, {width, height: 36 + lines.length * 17, lines, model: node});
  }
  for (const edge of pipeline.edges) graph.setEdge(edge.source, edge.target, {model: edge});
  if (!dagre.graphlib.alg.isAcyclic(graph)) throw new Error(`Cyclic ${pipeline.pipeline} processor graph`);
  dagre.layout(graph);
  const {width, height} = graph.graph();
  const svg = element("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": titles[pipeline.pipeline]});
  const markerId = `arrow-${pipeline.pipeline}`;
  const definitions = element("defs");
  const marker = element("marker", {id: markerId, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto"});
  marker.append(element("path", {d: "M 0 0 L 10 5 L 0 10 z"}));
  definitions.append(marker); svg.append(definitions);
  for (const edge of graph.edges()) {
    const data = graph.edge(edge);
    const path = data.points.map((point, index) => `${index ? "L" : "M"} ${point.x} ${point.y}`).join(" ");
    svg.append(element("path", {d: path, class: `edge${data.model.dynamic ? " dynamic" : ""}`,
      "marker-end": `url(#${markerId})`, "data-source": edge.v, "data-target": edge.w}));
  }
  for (const identity of graph.nodes()) {
    const node = graph.node(identity), model = node.model;
    const group = element("g", {class: `node ${model.kind}`, "data-node": identity,
      transform: `translate(${node.x},${node.y})`});
    group.append(element("title", {}, `${model.label}${model.detail ? ` — ${model.detail}` : ""}`));
    group.append(element("rect", {x: -node.width / 2, y: -node.height / 2, width: node.width, height: node.height,
      rx: model.kind === "processor" ? 3 : 17}));
    group.append(element("text", {y: -node.height / 2 + 17, class: "node-kind"}, model.kind === "processor" ? "PROCESSOR" : "MIME TYPE"));
    node.lines.forEach((line, index) => group.append(element("text", {y: -node.height / 2 + 35 + 17 * index}, line)));
    svg.append(group);
  }
  const section = document.createElement("section"); section.dataset.pipeline = pipeline.pipeline;
  const heading = document.createElement("h2"); heading.textContent = titles[pipeline.pipeline];
  const container = document.createElement("div"); container.className = "diagram"; container.append(svg);
  const details = document.createElement("details"), summary = document.createElement("summary");
  summary.textContent = "Connections and processor details"; details.append(summary);
  const list = document.createElement("ul");
  for (const edge of pipeline.edges) {
    const source = graph.node(edge.source).model, target = graph.node(edge.target).model;
    const line = document.createElement("li");
    line.textContent = `${source.label} → ${target.label}${edge.dynamic ? " (possible extracted MIME part)" : ""}`;
    list.append(line);
  }
  for (const node of pipeline.nodes.filter(node => node.detail)) {
    const line = document.createElement("li"); line.textContent = `${node.label}: ${node.detail}`; list.append(line);
  }
  details.append(list); section.append(heading, container, details);
  return {section, container, svg, width, height};
}

function resize() {
  for (const diagram of diagrams) {
    const scale = zoom ?? Math.min(1, Math.max(0.1, (diagram.container.clientWidth - 24) / diagram.width));
    diagram.svg.setAttribute("width", diagram.width * scale);
    diagram.svg.setAttribute("height", diagram.height * scale);
  }
}

async function refreshProcessorGraph() {
  const current = ++generation;
  byId("refresh").disabled = true;
  byId("graphs").setAttribute("aria-busy", "true");
  byId("status").textContent = "Reading registered processors…";
  try {
    const result = await window.pywebview.api.status();
    await document.fonts.ready;
    if (current !== generation) return;
    if (result.error) throw new Error(result.error);
    const next = result.pipelines.map(draw);
    byId("graphs").replaceChildren(...next.map(diagram => diagram.section));
    diagrams = next; resize();
    byId("context").textContent = result.context;
    byId("status").textContent = `${result.pipelines.reduce((count, panel) => count + panel.nodes.filter(node => node.kind === "processor").length, 0)} registered processors · potential dataflow`;
    byId("error").hidden = true;
  } catch (error) {
    if (current !== generation) return;
    byId("error").textContent = `Could not load processor graph: ${String(error?.message || error)} Use Refresh to retry.`;
    byId("error").hidden = false;
    byId("status").textContent = diagrams.length ? "Showing the previous graph." : "Graph unavailable.";
  } finally {
    if (current === generation) {
      byId("graphs").setAttribute("aria-busy", "false");
      byId("refresh").disabled = false;
    }
  }
}

function initialize() {
  if (initialized || !window.pywebview?.api?.status) return;
  initialized = true; window.clearInterval(bridgeTimer);
  byId("refresh").addEventListener("click", refreshProcessorGraph);
  byId("fit").addEventListener("click", () => { zoom = null; resize(); });
  byId("actual-size").addEventListener("click", () => { zoom = 1; resize(); });
  byId("zoom-in").addEventListener("click", () => { zoom = Math.min(3, (zoom ?? 1) * 1.2); resize(); });
  byId("zoom-out").addEventListener("click", () => { zoom = Math.max(0.2, (zoom ?? 1) / 1.2); resize(); });
  refreshProcessorGraph();
}

window.refreshProcessorGraph = refreshProcessorGraph;
window.addEventListener("resize", resize);
window.addEventListener("pywebviewready", initialize);
const bridgeTimer = window.setInterval(initialize, 250);
window.setTimeout(() => {
  if (!initialized) byId("status").textContent = "Waiting for the application bridge. Close and reopen this window if it does not connect.";
}, 10000);
