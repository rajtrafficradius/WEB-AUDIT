import fs from "node:fs/promises";
import path from "node:path";
const artifactModule = process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { SpreadsheetFile, Workbook } = await import(artifactModule);

const [inputPath, outputRoot, previewRoot] = process.argv.slice(2);
if (!inputPath || !outputRoot || !previewRoot) {
  throw new Error("Usage: node build_workbooks.mjs <input.json> <package-root> <preview-root>");
}

const data = JSON.parse(await fs.readFile(inputPath, "utf8"));
await fs.mkdir(outputRoot, { recursive: true });
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

function letter(index) {
  let value = index + 1;
  let out = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    out = String.fromCharCode(65 + remainder) + out;
    value = Math.floor((value - 1) / 26);
  }
  return out;
}

function applyWidths(sheet, widths, rows = 300) {
  widths.forEach((width, index) => {
    sheet.getRange(`${letter(index)}1:${letter(index)}${rows}`).format.columnWidth = width;
  });
}

function titleBlock(sheet, title, subtitle, columns) {
  const end = letter(columns - 1);
  sheet.showGridLines = false;
  sheet.mergeCells(`A1:${end}1`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${end}1`).format = {
    fill: C.ink,
    font: { name: "Georgia", bold: true, size: 20, color: C.surface },
    verticalAlignment: "center",
    rowHeight: 34,
  };
  sheet.mergeCells(`A2:${end}2`);
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${end}2`).format = {
    fill: C.paper,
    font: { name: "Aptos", size: 10, color: C.muted },
    wrapText: true,
    rowHeight: 30,
  };
  sheet.getRange(`A3:${end}3`).format.borders = {
    bottom: { style: "medium", color: C.copper },
  };
}

function styleTable(sheet, range, name) {
  const table = sheet.tables.add(range, true, name);
  table.style = "TableStyleMedium2";
  table.showHeaders = true;
  table.showFilterButton = true;
  table.showBandedRows = true;
  return table;
}

async function saveWorkbook(workbook, target, previews) {
  for (const [sheetName, previewName, range] of previews) {
    const blob = await workbook.render({ sheetName, range, autoCrop: range ? undefined : "all", scale: 1, format: "png" });
    await fs.writeFile(path.join(previewRoot, previewName), new Uint8Array(await blob.arrayBuffer()));
  }
  const exported = await SpreadsheetFile.exportXlsx(workbook);
  await fs.mkdir(path.dirname(target), { recursive: true });
  await exported.save(target);
  const inspectionPath = `${target}.inspect.ndjson`;
  try {
    await fs.rename(inspectionPath, path.join(previewRoot, `${path.basename(target)}.inspect.ndjson`));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  return target;
}

function buildAuditWorkbook() {
  const workbook = Workbook.create();
  const executive = workbook.worksheets.add("Executive");
  titleBlock(executive, "Kakawa Enterprise SEO Audit · v19", `Evidence as of ${data.run.evidence_as_of} · Run ${data.run.id} · Rule ${data.run.rule_version}`, 10);
  executive.getRange("A5:B11").values = [
    ["Decision measure", "Canonical value"],
    ["Approved domain", data.client.domain],
    ["Evidence coverage", data.run.evidence_coverage],
    ["Overall score", data.run.overall_score ?? "Withheld"],
    ["Score publication rule", data.run.overall_score_reason],
    ["External changes", "None — advisory package only"],
    ["Release status", data.qa.release_status],
  ];
  executive.getRange("A5:B5").format = { fill: C.indigo, font: { bold: true, color: C.surface } };
  executive.getRange("B7").format.numberFormat = "0%";
  executive.getRange("A5:B11").format.borders = { preset: "all", style: "thin", color: C.rule };
  executive.getRange("A13:B13").values = [["Category", "Score"]];
  const categoryRows = data.categories.map((item) => [item.category, item.score ?? 0]);
  executive.getRange(`A14:B${13 + categoryRows.length}`).values = categoryRows;
  executive.getRange("A13:B13").format = { fill: C.copper, font: { bold: true, color: C.surface } };
  const chart = executive.charts.add("bar", executive.getRange(`A13:B${13 + categoryRows.length}`));
  chart.title = "Evidence-covered category scores";
  chart.hasLegend = false;
  chart.xAxis = { numberFormatCode: "0" };
  chart.setPosition("D5", "J20");
  executive.freezePanes.freezeRows(3);
  applyWidths(executive, [28, 68, 3, 14, 14, 14, 14, 14, 14, 14]);

  const sources = workbook.worksheets.add("Source Coverage");
  titleBlock(sources, "Source coverage", "Connection, capture, scope and explicit-unavailable states used by this run.", 8);
  const sourceRows = data.sources.map((item) => [
    item.id,
    item.label,
    item.kind,
    item.status,
    item.captured_at,
    item.scope,
    item.coverage ?? 0,
    item.unavailable_reason ?? "—",
  ]);
  sources.getRange(`A5:H${5 + sourceRows.length}`).values = [["ID", "Source", "Kind", "Status", "Captured", "Scope", "Coverage", "Unavailable reason"], ...sourceRows];
  sources.getRange(`G6:G${5 + sourceRows.length}`).format.numberFormat = "0%";
  sources.getRange(`G6:G${5 + sourceRows.length}`).conditionalFormats.add("dataBar", { color: C.indigo, gradient: false });
  styleTable(sources, `A5:H${5 + sourceRows.length}`, "SourceCoverageTable");
  sources.freezePanes.freezeRows(5);
  sources.freezePanes.freezeColumns(2);
  applyWidths(sources, [13, 34, 18, 18, 22, 42, 13, 46]);

  const issues = workbook.worksheets.add("Issue Register");
  titleBlock(issues, "Issue register", "Versioned deterministic findings with confidence, reach, approval class and evidence lineage.", 12);
  const issueRows = data.findings.map((item) => [
    item.id,
    item.priority,
    item.category,
    item.title,
    item.impact,
    item.confidence,
    item.reach,
    item.effort,
    item.approval_class,
    item.rule_version,
    item.as_of_date,
    item.evidence_ids.join(", "),
  ]);
  issues.getRange(`A5:L${5 + issueRows.length}`).values = [["ID", "Priority", "Category", "Finding", "Impact", "Confidence", "Reach", "Effort", "Approval class", "Rule version", "As of", "Evidence"], ...issueRows];
  issues.getRange(`F6:F${5 + issueRows.length}`).format.numberFormat = "0%";
  issues.getRange(`B6:B${5 + issueRows.length}`).conditionalFormats.add("containsText", { text: "P1", format: { fill: "#F2D7D5", font: { bold: true, color: C.red } } });
  styleTable(issues, `A5:L${5 + issueRows.length}`, "IssueRegisterTable");
  issues.freezePanes.freezeRows(5);
  issues.freezePanes.freezeColumns(4);
  applyWidths(issues, [12, 10, 21, 44, 52, 13, 12, 12, 23, 18, 15, 26]);

  const evidence = workbook.worksheets.add("Evidence Index");
  titleBlock(evidence, "Evidence index", "Canonical measured and observed records. URLs are normalized; originals remain separately recorded.", 10);
  const evidenceRows = data.evidence.map((item) => [
    item.id,
    item.source_id,
    item.evidence_type,
    item.observed_value,
    item.original_url,
    item.normalized_url,
    item.captured_at,
    item.locale,
    item.scope,
    item.confidence,
  ]);
  evidence.getRange(`A5:J${5 + evidenceRows.length}`).values = [["ID", "Source", "Type", "Observed value", "Original URL", "Normalized URL", "Captured", "Locale", "Scope", "Confidence"], ...evidenceRows];
  evidence.getRange(`J6:J${5 + evidenceRows.length}`).format.numberFormat = "0%";
  styleTable(evidence, `A5:J${5 + evidenceRows.length}`, "EvidenceIndexTable");
  evidence.freezePanes.freezeRows(5);
  evidence.freezePanes.freezeColumns(3);
  applyWidths(evidence, [14, 13, 21, 50, 54, 54, 23, 11, 30, 13]);

  const pages = workbook.worksheets.add("Page Inventory");
  titleBlock(pages, "Normalized page inventory", "Approved-domain HTML pages observed in the acceptance crawl. Redirect and canonical relationships remain explicit.", 11);
  const pageRows = data.pages.map((item) => [
    item.id,
    item.original_url,
    item.normalized_url,
    item.status_code,
    item.title,
    item.h1,
    item.canonical_url,
    item.indexability,
    item.word_count,
    item.internal_links,
    item.captured_at,
  ]);
  pages.getRange(`A5:K${5 + pageRows.length}`).values = [["ID", "Original URL", "Normalized URL", "HTTP", "Title", "H1", "Canonical", "Indexability", "Words", "Internal links", "Captured"], ...pageRows];
  styleTable(pages, `A5:K${5 + pageRows.length}`, "PageInventoryTable");
  pages.freezePanes.freezeRows(5);
  pages.freezePanes.freezeColumns(3);
  applyWidths(pages, [13, 52, 52, 10, 48, 45, 52, 17, 11, 16, 22]);

  const rules = workbook.worksheets.add("Scoring Rules");
  titleBlock(rules, "Transparent scoring rules", "Category scores include only covered deterministic rules. Overall health is withheld below 70% weighted evidence coverage.", 8);
  const ruleRows = data.categories.map((item) => [item.category, item.score ?? "Withheld", item.coverage, item.weight, item.rule_version, item.status, item.unavailable_reason ?? "—", item.evidence_ids.join(", ")]);
  rules.getRange(`A5:H${5 + ruleRows.length}`).values = [["Category", "Score", "Coverage", "Weight", "Rule version", "Status", "Unavailable reason", "Evidence"], ...ruleRows];
  rules.getRange(`B6:D${5 + ruleRows.length}`).format.numberFormat = "0%";
  styleTable(rules, `A5:H${5 + ruleRows.length}`, "ScoringRulesTable");
  rules.freezePanes.freezeRows(5);
  applyWidths(rules, [28, 13, 13, 12, 18, 18, 45, 30]);

  const recon = workbook.worksheets.add("Reconciliation");
  titleBlock(recon, "Cross-artifact reconciliation", "Canonical counts used by the UI, workbooks, reports, deck and package manifest.", 6);
  const reconRows = data.qa.reconciliation.map((item) => [item.measure, item.canonical, item.package, item.result, item.rule, item.evidence]);
  recon.getRange(`A5:F${5 + reconRows.length}`).values = [["Measure", "Canonical", "Package", "Result", "Rule", "Evidence"], ...reconRows];
  styleTable(recon, `A5:F${5 + reconRows.length}`, "ReconciliationTable");
  recon.freezePanes.freezeRows(5);
  applyWidths(recon, [36, 16, 16, 18, 48, 38]);
  return workbook;
}

function buildActionWorkbook() {
  const workbook = Workbook.create();
  const readme = workbook.worksheets.add("Read Me");
  titleBlock(readme, "Kakawa 16-Week Canonical Action Plan · v19", `Evidence as of ${data.run.evidence_as_of} · Action IDs are authoritative across every derivative.`, 8);
  readme.getRange("A5:B12").values = [
    ["Control", "Meaning"],
    ["Canonical source", "The Action Plan sheet; changes create a versioned record."],
    ["Approval classes", "Admin approval is required for redirects, canonicals, robots, schema and disavow candidates."],
    ["External execution", "Not performed by this application."],
    ["Private baselines", "Remain unavailable until GSC/GA4/SEMrush connections are approved."],
    ["Status values", "Not started, Ready, In progress, Blocked, Review, Approved, Complete"],
    ["Priority", "P1–P4 reflects impact, confidence, reach, criticality, dependencies and effort."],
    ["Run", data.run.id],
  ];
  readme.getRange("A5:B5").format = { fill: C.indigo, font: { bold: true, color: C.surface } };
  readme.getRange("A5:B12").format.borders = { preset: "all", style: "thin", color: C.rule };
  applyWidths(readme, [28, 90, 10, 10, 10, 10, 10, 10]);

  const plan = workbook.worksheets.add("Action Plan");
  titleBlock(plan, "Canonical action plan", "Owners, dependencies, effort, KPIs, evidence, risk and approval controls.", 17);
  const rows = data.actions.map((item) => [
    item.id,
    item.phase,
    item.week,
    item.week_end,
    item.priority,
    item.action,
    item.owner,
    item.dependencies.join(", ") || "None",
    item.effort,
    item.kpi,
    item.approval_class,
    item.status,
    item.evidence_ids.join(", "),
    item.confidence,
    null,
    item.implementation_risk,
    item.notes,
  ]);
  plan.getRange(`A5:Q${5 + rows.length}`).values = [["ID", "Phase", "Week start", "Week end", "Priority", "Action", "Owner", "Dependencies", "Effort", "KPI", "Approval class", "Status", "Evidence", "Confidence", "Duration", "Implementation risk", "Notes"], ...rows];
  plan.getRange("O6").formulas = [["=D6-C6+1"]];
  if (rows.length > 1) plan.getRange(`O6:O${5 + rows.length}`).fillDown();
  plan.getRange(`N6:N${5 + rows.length}`).format.numberFormat = "0%";
  plan.getRange(`L6:L${5 + rows.length}`).dataValidation = { rule: { type: "list", values: ["Not started", "Ready", "In progress", "Blocked", "Review", "Approved", "Complete"] } };
  plan.getRange(`E6:E${5 + rows.length}`).conditionalFormats.add("containsText", { text: "P1", format: { fill: "#F2D7D5", font: { bold: true, color: C.red } } });
  plan.getRange(`L6:L${5 + rows.length}`).conditionalFormats.add("containsText", { text: "Complete", format: { fill: "#DDEBE4", font: { bold: true, color: C.green } } });
  styleTable(plan, `A5:Q${5 + rows.length}`, "CanonicalActionPlanTable");
  plan.freezePanes.freezeRows(5);
  plan.freezePanes.freezeColumns(5);
  applyWidths(plan, [12, 20, 12, 12, 10, 58, 22, 28, 15, 34, 23, 18, 24, 13, 12, 22, 44]);

  const gantt = workbook.worksheets.add("16-Week Gantt");
  titleBlock(gantt, "16-week Gantt", "Formula-backed schedule derived from canonical week start and end fields.", 21);
  const ganttHeaders = ["ID", "Action", "Start", "End", "Owner", ...Array.from({ length: 16 }, (_, i) => i + 1)];
  gantt.getRange("A5:U5").values = [ganttHeaders];
  gantt.getRange("A5:U5").format = { fill: C.indigo, font: { bold: true, color: C.surface }, horizontalAlignment: "center", wrapText: true };
  const ganttCore = data.actions.map((item) => [item.id, item.action, item.week, item.week_end, item.owner]);
  gantt.getRange(`A6:E${5 + ganttCore.length}`).values = ganttCore;
  for (let row = 6; row < 6 + ganttCore.length; row += 1) {
    for (let weekCol = 5; weekCol < 21; weekCol += 1) {
      const cell = `${letter(weekCol)}${row}`;
      gantt.getRange(cell).formulas = [[`=IF(AND(${letter(weekCol)}$5>=$C${row},${letter(weekCol)}$5<=$D${row}),1,0)`]];
    }
  }
  const matrix = gantt.getRange(`F6:U${5 + ganttCore.length}`);
  matrix.format.numberFormat = ";;;";
  matrix.conditionalFormats.add("cellIs", { operator: "equal", formula: 1, format: { fill: C.indigo } });
  gantt.getRange(`A5:U${5 + ganttCore.length}`).format.borders = { preset: "all", style: "thin", color: C.rule };
  gantt.freezePanes.freezeRows(5);
  gantt.freezePanes.freezeColumns(5);
  applyWidths(gantt, [12, 55, 10, 10, 23, ...Array(16).fill(5.5)]);

  const dashboard = workbook.worksheets.add("Dashboard");
  titleBlock(dashboard, "Delivery dashboard", "Formula-backed counts update when canonical action statuses and priorities change.", 10);
  dashboard.getRange("A5:B9").values = [["Priority", "Actions"], ["P1", null], ["P2", null], ["P3", null], ["P4", null]];
  dashboard.getRange("B6").formulas = [["=COUNTIF('Action Plan'!$E:$E,A6)"]];
  dashboard.getRange("B6:B9").fillDown();
  dashboard.getRange("A5:B5").format = { fill: C.indigo, font: { bold: true, color: C.surface } };
  dashboard.getRange("D5:E11").values = [["Status", "Actions"], ["Not started", null], ["Ready", null], ["In progress", null], ["Blocked", null], ["Review", null], ["Complete", null]];
  dashboard.getRange("E6").formulas = [["=COUNTIF('Action Plan'!$L:$L,D6)"]];
  dashboard.getRange("E6:E11").fillDown();
  dashboard.getRange("D5:E5").format = { fill: C.copper, font: { bold: true, color: C.surface } };
  const priorityChart = dashboard.charts.add("column", dashboard.getRange("A5:B9"));
  priorityChart.title = "Actions by priority";
  priorityChart.hasLegend = false;
  priorityChart.setPosition("A13", "E28");
  const statusChart = dashboard.charts.add("bar", dashboard.getRange("D5:E11"));
  statusChart.title = "Actions by status";
  statusChart.hasLegend = false;
  statusChart.setPosition("F5", "J20");
  applyWidths(dashboard, [18, 14, 4, 22, 14, 16, 16, 16, 16, 16]);
  return workbook;
}

function buildQaWorkbook() {
  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("Release Summary");
  titleBlock(summary, "Kakawa v19 Release QA", `Run ${data.run.id} · Evidence as of ${data.run.evidence_as_of}`, 8);
  summary.getRange("A5:B12").values = [
    ["Release control", "Result"],
    ["Release status", data.qa.release_status],
    ["Critical failures", data.qa.critical_failures],
    ["High failures", data.qa.high_failures],
    ["Wrong-domain URLs", data.qa.wrong_domain_urls],
    ["Unsupported claims", data.qa.unsupported_claims],
    ["Unapproved risky assets", data.qa.unapproved_risky_assets],
    ["Statement", data.qa.release_statement],
  ];
  summary.getRange("A5:B5").format = { fill: C.indigo, font: { bold: true, color: C.surface } };
  summary.getRange("A5:B12").format.borders = { preset: "all", style: "thin", color: C.rule };
  summary.getRange("B6").conditionalFormats.add("containsText", { text: "PASS", format: { fill: "#DDEBE4", font: { bold: true, color: C.green } } });
  applyWidths(summary, [32, 96, 8, 8, 8, 8, 8, 8]);

  const gates = workbook.worksheets.add("QA Gates");
  titleBlock(gates, "QA gates", "Critical/High failures must be zero before package release.", 7);
  const gateRows = data.qa.gates.map((item) => [item.id, item.name, item.status, item.critical_failures, item.high_failures, item.evidence, item.checked_at]);
  gates.getRange(`A5:G${5 + gateRows.length}`).values = [["ID", "Gate", "Status", "Critical", "High", "Evidence", "Checked"], ...gateRows];
  gates.getRange(`C6:C${5 + gateRows.length}`).conditionalFormats.add("containsText", { text: "PASS", format: { fill: "#DDEBE4", font: { bold: true, color: C.green } } });
  styleTable(gates, `A5:G${5 + gateRows.length}`, "QAGatesTable");
  gates.freezePanes.freezeRows(5);
  applyWidths(gates, [13, 38, 14, 12, 12, 60, 23]);

  const reconciliation = workbook.worksheets.add("Reconciliation");
  titleBlock(reconciliation, "Reconciliation", "Counts must match canonical records across UI, workbooks, reports, deck and manifest.", 6);
  const reconRows = data.qa.reconciliation.map((item) => [item.measure, item.canonical, item.package, item.result, item.rule, item.evidence]);
  reconciliation.getRange(`A5:F${5 + reconRows.length}`).values = [["Measure", "Canonical", "Package", "Result", "Rule", "Evidence"], ...reconRows];
  reconciliation.getRange(`D6:D${5 + reconRows.length}`).conditionalFormats.add("containsText", { text: "PASS", format: { fill: "#DDEBE4", font: { bold: true, color: C.green } } });
  styleTable(reconciliation, `A5:F${5 + reconRows.length}`, "QAReconciliationTable");
  reconciliation.freezePanes.freezeRows(5);
  applyWidths(reconciliation, [38, 16, 16, 17, 48, 40]);

  const availability = workbook.worksheets.add("Availability Matrix");
  titleBlock(availability, "Evidence availability", "Missing credentials are explicit unavailable states, never fabricated substitutes.", 7);
  const availabilityRows = data.sources.map((item) => [item.label, item.kind, item.status, item.captured_at, item.scope, item.coverage ?? 0, item.unavailable_reason ?? "—"]);
  availability.getRange(`A5:G${5 + availabilityRows.length}`).values = [["Source", "Kind", "Status", "Captured", "Scope", "Coverage", "Unavailable reason"], ...availabilityRows];
  availability.getRange(`F6:F${5 + availabilityRows.length}`).format.numberFormat = "0%";
  styleTable(availability, `A5:G${5 + availabilityRows.length}`, "AvailabilityMatrixTable");
  availability.freezePanes.freezeRows(5);
  applyWidths(availability, [34, 18, 18, 22, 40, 13, 56]);

  const generation = workbook.worksheets.add("Generation Ledger");
  titleBlock(generation, "Generation ledger", "Configured model IDs are recorded even when calls are withheld because credentials or approved fact packs are unavailable.", 10);
  const generationRows = data.generation_ledger.map((item) => [item.id, item.task, item.configured_model, item.returned_model ?? "Unavailable", item.prompt_version, item.status, item.request_hash ?? "Unavailable", item.response_hash ?? "Unavailable", item.tokens, item.cost, item.unavailable_reason ?? "—"]);
  generation.getRange(`A5:K${5 + generationRows.length}`).values = [["ID", "Task", "Configured model", "Returned model", "Prompt", "Status", "Request hash", "Response hash", "Tokens", "Cost", "Unavailable reason"], ...generationRows];
  generation.getRange(`J6:J${5 + generationRows.length}`).format.numberFormat = "$0.0000";
  styleTable(generation, `A5:K${5 + generationRows.length}`, "GenerationLedgerTable");
  generation.freezePanes.freezeRows(5);
  applyWidths(generation, [13, 30, 23, 23, 18, 18, 35, 35, 12, 13, 60]);
  return workbook;
}

const audit = buildAuditWorkbook();
const action = buildActionWorkbook();
const qa = buildQaWorkbook();

await saveWorkbook(
  audit,
  path.join(outputRoot, "01_Evidence_and_Audits", "Kakawa_Enterprise_SEO_Audit_v19.xlsx"),
  [["Executive", "audit-executive.png", "A1:J21"], ["Issue Register", "audit-issues.png", "A1:L18"]],
);
await saveWorkbook(
  action,
  path.join(outputRoot, "03_Action_Plan", "Kakawa_16_Week_Action_Plan_v19.xlsx"),
  [["Dashboard", "action-dashboard.png", "A1:J28"], ["16-Week Gantt", "action-gantt.png", "A1:U22"]],
);
await saveWorkbook(
  qa,
  path.join(outputRoot, "06_QA_and_Manifest", "Kakawa_QA_v19.xlsx"),
  [["Release Summary", "qa-release.png", "A1:H14"], ["QA Gates", "qa-gates.png", "A1:G18"]],
);

const diagnostics = {
  generated_at: new Date().toISOString(),
  files: [
    "01_Evidence_and_Audits/Kakawa_Enterprise_SEO_Audit_v19.xlsx",
    "03_Action_Plan/Kakawa_16_Week_Action_Plan_v19.xlsx",
    "06_QA_and_Manifest/Kakawa_QA_v19.xlsx",
  ],
  previews: ["audit-executive.png", "audit-issues.png", "action-dashboard.png", "action-gantt.png", "qa-release.png", "qa-gates.png"],
  sheets: {
    audit: audit.worksheets.items.map((sheet) => sheet.name),
    action: action.worksheets.items.map((sheet) => sheet.name),
    qa: qa.worksheets.items.map((sheet) => sheet.name),
  },
};
await fs.writeFile(path.join(previewRoot, "workbook-diagnostics.json"), JSON.stringify(diagnostics, null, 2));
process.exit(0);
