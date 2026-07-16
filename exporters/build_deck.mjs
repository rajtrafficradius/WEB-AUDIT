import fs from "node:fs/promises";
import path from "node:path";
const artifactModule = process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { Presentation, PresentationFile } = await import(artifactModule);

const [inputPath, pptxPath, previewRoot] = process.argv.slice(2);
if (!inputPath || !pptxPath || !previewRoot) {
  throw new Error("Usage: node build_deck.mjs <input.json> <deck.pptx> <preview-root>");
}

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
await fs.mkdir(path.dirname(pptxPath), { recursive: true });
await fs.mkdir(previewRoot, { recursive: true });

const C = {
  paper: "#F6F2E9",
  surface: "#FFFEFA",
  ink: "#17201E",
  muted: "#66716D",
  indigo: "#3E4C83",
  copper: "#A15C38",
  green: "#2F6B57",
  red: "#9C3F3B",
  rule: "#D8D4C9",
};
const W = 1280;
const H = 720;
const presentation = Presentation.create({ slideSize: { width: W, height: H } });

function box(slide, { left, top, width, height, fill = "none", line = "none", radius = "none" }) {
  return slide.shapes.add({
    geometry: radius === "none" ? "rect" : "roundRect",
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: line, width: line === "none" ? 0 : 1 },
    ...(radius === "none" ? {} : { borderRadius: radius }),
  });
}

function text(slide, value, position, style = {}) {
  const shape = slide.shapes.add({
    geometry: "textbox",
    position,
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  shape.text = String(value);
  shape.text.style = {
    fontFamily: style.fontFamily ?? "Aptos",
    fontSize: style.fontSize ?? 20,
    bold: style.bold ?? false,
    color: style.color ?? C.ink,
    alignment: style.alignment ?? "left",
    verticalAlignment: style.verticalAlignment ?? "top",
  };
  return shape;
}

function chrome(slide, index, total, label = "KAKAWA CHOCOLATES") {
  text(slide, "TRAFFIC RADIUS", { left: 64, top: 34, width: 230, height: 28 }, { fontSize: 12, bold: true, color: C.ink });
  text(slide, label, { left: 760, top: 34, width: 360, height: 28 }, { fontSize: 12, bold: true, color: C.muted, alignment: "right" });
  box(slide, { left: 64, top: 676, width: 1152, height: 1, fill: C.rule });
  text(slide, `EVIDENCE AS OF ${data.run.evidence_as_of}`, { left: 64, top: 685, width: 420, height: 18 }, { fontSize: 10, color: C.muted });
  text(slide, `${String(index).padStart(2, "0")} / ${String(total).padStart(2, "0")}`, { left: 1080, top: 685, width: 136, height: 18 }, { fontSize: 10, color: C.muted, alignment: "right" });
}

function pointStack(slide, points, left = 825, top = 176, width = 350) {
  const limited = points.slice(0, 4);
  limited.forEach((point, index) => {
    const y = top + index * 104;
    box(slide, { left, top: y, width, height: 1, fill: index === 0 ? C.copper : C.rule });
    text(slide, point.label.toUpperCase(), { left, top: y + 14, width, height: 24 }, { fontSize: 11, bold: true, color: index === 0 ? C.copper : C.indigo });
    text(slide, point.text, { left, top: y + 39, width, height: 54 }, { fontSize: 17, color: C.ink });
  });
}

function coverSlide(slide, item, index, total) {
  slide.background.fill = C.paper;
  box(slide, { left: 846, top: 0, width: 434, height: H, fill: C.ink });
  text(slide, item.eyebrow ?? "ENTERPRISE SEO REVIEW", { left: 70, top: 102, width: 450, height: 26 }, { fontSize: 13, bold: true, color: C.copper });
  text(slide, item.title, { left: 70, top: 164, width: 700, height: 270 }, { fontFamily: "Georgia", fontSize: 58, bold: true, color: C.ink });
  text(slide, item.body, { left: 72, top: 466, width: 650, height: 108 }, { fontSize: 22, color: C.muted });
  text(slide, "KAKAWA\nCHOCOLATES", { left: 890, top: 96, width: 300, height: 120 }, { fontFamily: "Georgia", fontSize: 32, bold: true, color: C.surface });
  text(slide, `RUN\n${data.run.id}`, { left: 890, top: 304, width: 300, height: 68 }, { fontSize: 14, color: C.rule });
  text(slide, `STATUS\n${data.qa.release_status}`, { left: 890, top: 424, width: 300, height: 68 }, { fontSize: 14, color: C.surface });
  text(slide, `${String(index).padStart(2, "0")} / ${String(total).padStart(2, "0")}`, { left: 1040, top: 675, width: 150, height: 20 }, { fontSize: 10, color: C.rule, alignment: "right" });
}

function scoreSlide(slide, item, index, total) {
  slide.background.fill = C.surface;
  chrome(slide, index, total, item.eyebrow ?? "EVIDENCE POSTURE");
  text(slide, item.title, { left: 64, top: 104, width: 560, height: 142 }, { fontFamily: "Georgia", fontSize: 43, bold: true });
  text(slide, item.body, { left: 64, top: 258, width: 500, height: 102 }, { fontSize: 19, color: C.muted });
  const scored = data.categories.filter((category) => category.score !== null && category.score !== undefined).slice(0, 8);
  slide.charts.add("bar", {
    position: { left: 596, top: 112, width: 600, height: 450 },
    categories: scored.map((item) => item.category),
    series: [{ name: "Score", values: scored.map((item) => item.score), fill: C.indigo }],
    hasLegend: false,
    dataLabels: { showValue: true, position: "outEnd" },
    xAxis: { min: 0, max: 100, majorGridlines: { style: "solid", fill: C.rule, width: 1 } },
    yAxis: { majorGridlines: { style: "solid", fill: "none", width: 0 } },
  });
  box(slide, { left: 64, top: 420, width: 476, height: 150, fill: C.paper, line: C.rule, radius: "rounded-lg" });
  text(slide, "PUBLICATION RULE", { left: 90, top: 446, width: 220, height: 22 }, { fontSize: 11, bold: true, color: C.copper });
  text(slide, data.run.overall_score_reason, { left: 90, top: 480, width: 420, height: 70 }, { fontSize: 17, color: C.ink });
}

function timelineSlide(slide, item, index, total) {
  slide.background.fill = C.paper;
  chrome(slide, index, total, item.eyebrow ?? "16-WEEK ROADMAP");
  text(slide, item.title, { left: 64, top: 96, width: 880, height: 90 }, { fontFamily: "Georgia", fontSize: 43, bold: true });
  text(slide, item.body, { left: 64, top: 190, width: 760, height: 66 }, { fontSize: 19, color: C.muted });
  const phases = [
    ["01", "Weeks 1–4", "Stabilise", "Evidence closure, indexation controls, measurement contract"],
    ["02", "Weeks 5–8", "Clarify", "Information architecture, templates, internal-link design"],
    ["03", "Weeks 9–12", "Expand", "Approved content opportunities and merchandising depth"],
    ["04", "Weeks 13–16", "Prove", "QA, measurement review, iteration and release evidence"],
  ];
  phases.forEach((phase, position) => {
    const left = 64 + position * 288;
    box(slide, { left, top: 310, width: 250, height: 246, fill: position === 0 ? C.ink : C.surface, line: position === 0 ? C.ink : C.rule, radius: "rounded-lg" });
    text(slide, phase[0], { left: left + 22, top: 330, width: 60, height: 28 }, { fontSize: 13, bold: true, color: position === 0 ? C.copper : C.indigo });
    text(slide, phase[1], { left: left + 22, top: 374, width: 190, height: 24 }, { fontSize: 12, bold: true, color: position === 0 ? C.rule : C.muted });
    text(slide, phase[2], { left: left + 22, top: 414, width: 190, height: 40 }, { fontFamily: "Georgia", fontSize: 24, bold: true, color: position === 0 ? C.surface : C.ink });
    text(slide, phase[3], { left: left + 22, top: 468, width: 198, height: 70 }, { fontSize: 15, color: position === 0 ? C.rule : C.muted });
  });
}

function comparisonSlide(slide, item, index, total) {
  slide.background.fill = C.surface;
  chrome(slide, index, total, item.eyebrow ?? "NEGATIVE REGRESSION");
  text(slide, item.title, { left: 64, top: 98, width: 740, height: 96 }, { fontFamily: "Georgia", fontSize: 43, bold: true });
  text(slide, item.body, { left: 64, top: 194, width: 920, height: 58 }, { fontSize: 18, color: C.muted });
  const rows = data.comparison.slice(0, 5);
  const headers = ["Failure mode", "v19 control", "Result"];
  const widths = [270, 520, 260];
  let x = 64;
  headers.forEach((header, i) => {
    box(slide, { left: x, top: 286, width: widths[i], height: 44, fill: C.indigo });
    text(slide, header.toUpperCase(), { left: x + 12, top: 300, width: widths[i] - 24, height: 18 }, { fontSize: 11, bold: true, color: C.surface });
    x += widths[i];
  });
  rows.forEach((row, rowIndex) => {
    const y = 330 + rowIndex * 58;
    const fill = rowIndex % 2 ? C.paper : C.surface;
    const values = [row.failure_mode, row.v19_control, row.v19_result];
    let left = 64;
    values.forEach((value, columnIndex) => {
      box(slide, { left, top: y, width: widths[columnIndex], height: 58, fill, line: C.rule });
      text(slide, value, { left: left + 12, top: y + 10, width: widths[columnIndex] - 24, height: 42 }, { fontSize: columnIndex === 2 ? 13 : 14, bold: columnIndex === 2, color: columnIndex === 2 ? C.green : C.ink });
      left += widths[columnIndex];
    });
  });
}

function genericSlide(slide, item, index, total) {
  slide.background.fill = index % 2 === 0 ? C.paper : C.surface;
  chrome(slide, index, total, item.eyebrow ?? "EXECUTIVE REVIEW");
  text(slide, item.title, { left: 64, top: 110, width: 690, height: 200 }, { fontFamily: "Georgia", fontSize: 46, bold: true });
  text(slide, item.body, { left: 64, top: 328, width: 650, height: 120 }, { fontSize: 20, color: C.muted });
  if (item.callout) {
    box(slide, { left: 64, top: 500, width: 650, height: 90, fill: C.ink, line: C.ink, radius: "rounded-lg" });
    text(slide, item.callout, { left: 90, top: 522, width: 598, height: 54 }, { fontSize: 18, bold: true, color: C.surface });
  }
  pointStack(slide, item.points ?? []);
}

const slides = data.deck;
slides.forEach((item, offset) => {
  const index = offset + 1;
  const slide = presentation.slides.add();
  if (offset === 0 || item.kind === "cover") coverSlide(slide, item, index, slides.length);
  else if (item.kind === "score") scoreSlide(slide, item, index, slides.length);
  else if (item.kind === "timeline") timelineSlide(slide, item, index, slides.length);
  else if (item.kind === "comparison") comparisonSlide(slide, item, index, slides.length);
  else genericSlide(slide, item, index, slides.length);
});

const layouts = [];
for (const [index, slide] of presentation.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  const png = await presentation.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(previewRoot, `${stem}.png`), new Uint8Array(await png.arrayBuffer()));
  const layout = await slide.export({ format: "layout" });
  const layoutText = await layout.text();
  await fs.writeFile(path.join(previewRoot, `${stem}.layout.json`), layoutText);
  layouts.push(JSON.parse(layoutText));
}
const montage = await presentation.export({ format: "webp", montage: true, scale: 1 });
await fs.writeFile(path.join(previewRoot, "deck-montage.webp"), new Uint8Array(await montage.arrayBuffer()));
const pptx = await PresentationFile.exportPptx(presentation);
await pptx.save(pptxPath);
try {
  await fs.rename(`${pptxPath}.inspect.ndjson`, path.join(previewRoot, "deck-inspect.ndjson"));
} catch (error) {
  if (error?.code !== "ENOENT") throw error;
}
await fs.writeFile(
  path.join(previewRoot, "deck-diagnostics.json"),
  JSON.stringify({ generated_at: new Date().toISOString(), slides: slides.length, slide_size: { width: W, height: H }, layout_exports: layouts.length, source_run: data.run.id }, null, 2),
);
process.exit(0);
