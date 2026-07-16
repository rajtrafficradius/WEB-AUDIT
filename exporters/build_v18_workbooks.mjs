import fs from "node:fs/promises";
import path from "node:path";
const artifactModule = process.env.ARTIFACT_TOOL_MODULE ?? "@oai/artifact-tool";
const { SpreadsheetFile, Workbook } = await import(artifactModule);

const [specPath, packageRoot, previewRoot] = process.argv.slice(2);
if (!specPath || !packageRoot || !previewRoot) throw new Error("Usage: node build_v18_workbooks.mjs <spec.json> <package-root> <preview-root>");
const specs = JSON.parse(await fs.readFile(specPath, "utf8"));
await fs.mkdir(previewRoot, { recursive: true });
const C={paper:"#F6F2E9",surface:"#FFFEFA",ink:"#17201E",muted:"#66716D",indigo:"#3E4C83",copper:"#A15C38",green:"#2F6B57",rule:"#D8D4C9",warn:"#F6E7C8"};
function col(index){let value=index+1,out="";while(value>0){const r=(value-1)%26;out=String.fromCharCode(65+r)+out;value=Math.floor((value-1)/26);}return out;}
function safeName(value,index,sheetIndex){const cleaned=String(value).replace(/[^A-Za-z0-9]/g,"").slice(0,19);return `${cleaned||"Register"}${index}${sheetIndex}`;}
function normalizedRows(sheet){if(sheet.rows?.length)return sheet.rows;return [sheet.headers.map((h,i)=>i===0?"UNAVAILABLE":`No approved ${String(h).toLowerCase()} evidence.`)];}

async function build(spec,index){
  const workbook=Workbook.create();
  const sheets=spec.sheets?.length?spec.sheets:[{name:"Register",headers:spec.headers,rows:spec.rows,widths:spec.widths,note:spec.register_note}];
  for(const [sheetIndex,sheetSpec] of sheets.entries()){
    const ws=workbook.worksheets.add(sheetSpec.name||`Sheet ${sheetIndex+1}`);ws.showGridLines=false;
    const headers=sheetSpec.headers;const rows=normalizedRows(sheetSpec);const last=col(headers.length-1);
    ws.mergeCells(`A1:${last}1`);ws.getRange("A1").values=[[`${spec.title} - ${sheetSpec.name}`]];
    ws.getRange(`A1:${last}1`).format={fill:C.ink,font:{name:"Georgia",bold:true,size:18,color:C.surface},rowHeight:34,verticalAlignment:"center"};
    ws.mergeCells(`A2:${last}2`);ws.getRange("A2").values=[[sheetSpec.note||"Canonical evidence register."]];
    ws.getRange(`A2:${last}2`).format={fill:C.paper,font:{name:"Aptos",size:10,color:C.muted},wrapText:true,rowHeight:31};
    ws.mergeCells(`A3:${last}3`);ws.getRange("A3").values=[[`Evidence as of ${spec.as_of} | Domain ${spec.domain} | Run ${spec.run_id} | ${spec.status}`]];
    ws.getRange(`A3:${last}3`).format={fill:C.surface,font:{name:"Aptos",bold:true,size:9,color:C.indigo},rowHeight:22};
    const bottom=5+rows.length;ws.getRange(`A5:${last}${bottom}`).values=[headers,...rows];
    const table=ws.tables.add(`A5:${last}${bottom}`,true,safeName(spec.title,index,sheetIndex));table.style="TableStyleMedium2";table.showHeaders=true;table.showFilterButton=true;table.showBandedRows=true;
    ws.freezePanes.freezeRows(5);ws.freezePanes.freezeColumns(Math.min(2,headers.length));
    const widths=sheetSpec.widths||headers.map(h=>Math.max(13,Math.min(50,String(h).length*1.7+7)));
    widths.forEach((width,offset)=>{ws.getRange(`${col(offset)}1:${col(offset)}${Math.max(50,bottom+5)}`).format.columnWidth=width;});
    ws.getRange(`A5:${last}${bottom}`).format.wrapText=true;
    ws.getRange(`A5:${last}5`).format={fill:C.indigo,font:{bold:true,color:C.surface},wrapText:true,rowHeight:30};
    if(sheetSpec.visual_summary==="priority"&&sheetSpec.priority_column){
      const startIndex=headers.length+2,start=col(startIndex),end=col(startIndex+1),pcol=col(sheetSpec.priority_column-1);
      ws.getRange(`${start}5:${end}9`).values=[["Priority","Rows"],["P1",null],["P2",null],["P3",null],["P4",null]];
      ws.getRange(`${start}5:${end}5`).format={fill:C.copper,font:{bold:true,color:C.surface}};
      for(let r=6;r<=9;r++)ws.getRange(`${end}${r}`).formulas=[[`=COUNTIF(${pcol}6:${pcol}${bottom},${start}${r})`]];
      const chart=ws.charts.add("bar",ws.getRange(`${start}5:${end}9`));chart.title="Priority distribution";chart.hasLegend=false;chart.setPosition(`${start}11`,`${col(startIndex+5)}25`);
    }
  }
  const target=path.join(packageRoot,...spec.path.split("/"));await fs.mkdir(path.dirname(target),{recursive:true});
  const file=await SpreadsheetFile.exportXlsx(workbook);await file.save(target);
  const first=sheets[0].name;const preview=await workbook.render({sheetName:first,range:"A1:H18",scale:1,format:"png"});
  const previewName=`${String(index+1).padStart(2,"0")}-${path.basename(target,".xlsx")}.png`;await fs.writeFile(path.join(previewRoot,previewName),new Uint8Array(await preview.arrayBuffer()));
  return {path:spec.path,preview:previewName,sheets:workbook.worksheets.items.map(s=>s.name),rows:sheets.reduce((n,s)=>n+(s.rows?.length||0),0)};
}
const results=[];for(const [index,spec] of specs.entries())results.push(await build(spec,index));
await fs.writeFile(path.join(previewRoot,"v18-workbook-diagnostics.json"),JSON.stringify({generated_at:new Date().toISOString(),count:results.length,results},null,2));