import fs from "node:fs/promises";
import path from "node:path";
const artifactModule = process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { SpreadsheetFile, Workbook } = await import(artifactModule);

const [specPath, packageRoot, previewRoot] = process.argv.slice(2);
if (!specPath || !packageRoot || !previewRoot) {
  throw new Error("Usage: node build_v18_workbooks.mjs <spec.json> <package-root> <preview-root>");
}

const specs = JSON.parse(await fs.readFile(specPath, "utf8"));
await fs.mkdir(previewRoot, { recursive: true });

const C = {
  paper: "#F6F2E9", surface: "#FFFEFA", ink: "#17201E", muted: "#66716D",
  indigo: "#3E4C83", copper: "#A15C38", green: "#2F6B57", rule: "#D8D4C9",
};

function col(index) {
  let value = index + 1;
  let out = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    out = String.fromCharCode(65 + remainder) + out;
    value = Math.floor((value - 1) / 26);
  }
  return out;
}

function safeName(value, index) {
  const cleaned = String(value).replace(/[^A-Za-z0-9]/g, "").slice(0, 24);
  return `${cleaned || "Register"}${index}`;
}

function normalizeRows(spec) {
  if (spec.rows?.length) return spec.rows;
  return [spec.headers.map((header, index) => index === 0 ? "UNAVAILABLE" : `No approved ${header.toLowerCase()} evidence was available.`)];
}

async function build(spec, index) {
  const workbook = Workbook.create();
  const overview = workbook.worksheets.add("Overview");
  overview.showGridLines = false;
  const end = col(Math.max(5, spec.headers.length - 1));
  overview.mergeCells(`A1:${end}1`);
  overview.getRange("A1").values = [[spec.title]];
  overview.getRange(`A1:${end}1`).format = {
    fill: C.ink, font: { name: "Georgia", bold: true, size: 20, color: C.surface },
    rowHeight: 36, verticalAlignment: "center",
  };
  overview.mergeCells(`A2:${end}2`);
  overview.getRange("A2").values = [[spec.subtitle]];
  overview.getRange(`A2:${end}2`).format = {
    fill: C.paper, font: { name: "Aptos", size: 10, color: C.muted },
    wrapText: true, rowHeight: 34,
  };
  overview.getRange("A5:B12").values = [
    ["Control", "Canonical value"],
    ["Evidence as of", spec.as_of],
    ["Approved domain", spec.domain],
    ["Run", spec.run_id],
    ["Evidence status", spec.status],
    ["Decision", spec.decision],
    ["Record count", null],
    ["Release class", null],
  ];
  overview.getRange("A5:B5").format = { fill: C.indigo, font: { bold: true, color: C.surface } };
  overview.getRange("A5:B12").format.borders = { preset: "all", style: "thin", color: C.rule };
  overview.getRange("B11").formulas = [["=MAX(0,COUNTA(Register!A6:A1000))"]];
  overview.getRange("B12").formulas = [["=IF(B11>0,\"REVIEW READY\",\"UNAVAILABLE\")"]];
  overview.getRange("A14:F17").values = [
    ["Quality gate", "Value", "", "", "", ""],
    ["Evidence-linked", spec.evidence_linked ? 1 : 0, "", "", "", ""],
    ["Approval required", spec.approval_required ? 1 : 0, "", "", "", ""],
    ["Fabricated substitutes", 0, "", "", "", ""],
  ];
  overview.getRange("A14:B14").format = { fill: C.copper, font: { bold: true, color: C.surface } };
  const chart = overview.charts.add("bar", overview.getRange("A14:B17"));
  chart.title = "Quality controls";
  chart.hasLegend = false;
  chart.setPosition("D5", "H17");
  overview.freezePanes.freezeRows(3);
  overview.getRange("A1:A40").format.columnWidth = 28;
  overview.getRange("B1:B40").format.columnWidth = 72;

  const register = workbook.worksheets.add("Register");
  register.showGridLines = false;
  register.mergeCells(`A1:${col(spec.headers.length - 1)}1`);
  register.getRange("A1").values = [[spec.title]];
  register.getRange(`A1:${col(spec.headers.length - 1)}1`).format = {
    fill: C.ink, font: { name: "Georgia", bold: true, size: 18, color: C.surface }, rowHeight: 34,
  };
  register.mergeCells(`A2:${col(spec.headers.length - 1)}2`);
  register.getRange("A2").values = [[spec.register_note]];
  register.getRange(`A2:${col(spec.headers.length - 1)}2`).format = {
    fill: C.paper, font: { name: "Aptos", size: 10, color: C.muted }, wrapText: true, rowHeight: 32,
  };
  const rows = normalizeRows(spec);
  register.getRange(`A5:${col(spec.headers.length - 1)}${5 + rows.length}`).values = [spec.headers, ...rows];
  const table = register.tables.add(`A5:${col(spec.headers.length - 1)}${5 + rows.length}`, true, safeName(spec.title, index));
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showFilterButton = true;
  table.showBandedRows = true;
  register.freezePanes.freezeRows(5);
  register.freezePanes.freezeColumns(Math.min(2, spec.headers.length));
  spec.widths.forEach((width, offset) => {
    register.getRange(`${col(offset)}1:${col(offset)}${Math.max(50, rows.length + 10)}`).format.columnWidth = width;
  });
  register.getRange(`A5:${col(spec.headers.length - 1)}${5 + rows.length}`).format.wrapText = true;

  const target = path.join(packageRoot, ...spec.path.split("/"));
  await fs.mkdir(path.dirname(target), { recursive: true });
  const file = await SpreadsheetFile.exportXlsx(workbook);
  await file.save(target);
  const preview = await workbook.render({ sheetName: "Overview", range: "A1:H18", scale: 1, format: "png" });
  const previewName = `${String(index + 1).padStart(2, "0")}-${path.basename(target, ".xlsx")}.png`;
  await fs.writeFile(path.join(previewRoot, previewName), new Uint8Array(await preview.arrayBuffer()));
  return { path: spec.path, preview: previewName, sheets: workbook.worksheets.items.map((sheet) => sheet.name) };
}

const results = [];
for (const [index, spec] of specs.entries()) {
  results.push(await build(spec, index));
}
await fs.writeFile(
  path.join(previewRoot, "v18-workbook-diagnostics.json"),
  JSON.stringify({ generated_at: new Date().toISOString(), count: results.length, results }, null, 2),
);
