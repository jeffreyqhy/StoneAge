import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/tadaema/Documents/sqsd/全服宝箱预览6.1.xlsx";
const outputDir = "/Users/tadaema/Projects/Stone Age_Script/outputs/chest_probability_tool";
const outputPath = path.join(outputDir, "全服宝箱预览6.1_深海6楼出货统计工具.xlsx");

const SHEETS = {
  batch: "深海6楼_批次",
  detail: "深海6楼_明细",
  stats: "深海6楼_统计",
  items: "深海6楼_物品",
};

const ITEMS = [
  "焰狱魔兽自选",
  "战神斧碎片",
  "暴躁蛮牛自选",
  "技能碎片",
  "魔神石",
  "魔法技能碎片",
  "深海鱼鳞",
  "深海贝壳",
  "深海之泪",
  "深海泥土",
  "战神首饰碎片",
  "暴龙玩具3",
  "暴龙玩具4",
  "10点金币",
  "剧毒宝石碎片",
  "沉默勋章3",
  "粉红暴自选",
  "粉红暴骑证",
  "3D人龙自选",
];

const BATCH_ROWS = 500;
const DETAIL_ROWS = 5000;
const batchStartRow = 4;
const batchEndRow = batchStartRow + BATCH_ROWS - 1;
const detailStartRow = 4;
const detailEndRow = detailStartRow + DETAIL_ROWS - 1;
const statsStartRow = 11;
const statsEndRow = statsStartRow + ITEMS.length - 1;

function matrix(rows) {
  return rows;
}

function colWidth(sheet, col, widthPx) {
  try {
    sheet.getRange(`${col}:${col}`).format.columnWidthPx = widthPx;
  } catch {}
}

function rowHeight(sheet, row, heightPx) {
  try {
    sheet.getRange(`${row}:${row}`).format.rowHeightPx = heightPx;
  } catch {}
}

function styleTitle(range, fill = "#0F6B66") {
  try {
    range.merge();
  } catch {}
  try {
    range.format.fill.color = fill;
    range.format.font.color = "#FFFFFF";
    range.format.font.bold = true;
    range.format.font.size = 16;
    range.format.horizontalAlignment = "Center";
    range.format.verticalAlignment = "Center";
  } catch {}
}

function styleHeader(range, fill = "#D7ECE8") {
  try {
    range.format.fill.color = fill;
    range.format.font.bold = true;
    range.format.font.color = "#183D3A";
    range.format.horizontalAlignment = "Center";
    range.format.verticalAlignment = "Center";
  } catch {}
}

function stylePanel(range, fill = "#F4FAF8") {
  try {
    range.format.fill.color = fill;
    range.format.font.color = "#183D3A";
  } catch {}
}

function setNumberFormat(range, formatCode) {
  try {
    range.numberFormat = formatCode;
  } catch {
    try {
      range.format.numberFormat = formatCode;
    } catch {}
  }
}

function addWholeNumberValidation(range, min, max, title, message) {
  range.dataValidation = {
    allowBlank: true,
    rule: { type: "whole", operator: "between", formula1: min, formula2: max },
    errorAlert: {
      style: "stop",
      title,
      message,
    },
  };
}

function addListValidation(range, source) {
  range.dataValidation = {
    allowBlank: true,
    list: { inCellDropDown: true, source },
  };
}

function addStandardBodyStyle(sheet, rangeAddress) {
  const range = sheet.getRange(rangeAddress);
  try {
    range.format.font.name = "Aptos";
    range.format.font.size = 11;
    range.format.verticalAlignment = "Center";
  } catch {}
}

async function saveRenderBlob(blob, filePath) {
  if (blob && typeof blob.arrayBuffer === "function") {
    const buffer = Buffer.from(await blob.arrayBuffer());
    await fs.writeFile(filePath, buffer);
    return;
  }
  if (blob && typeof blob.bytes === "function") {
    const bytes = await blob.bytes();
    await fs.writeFile(filePath, Buffer.from(bytes));
  }
}

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const itemSheet = workbook.worksheets.add(SHEETS.items);
const batchSheet = workbook.worksheets.add(SHEETS.batch);
const detailSheet = workbook.worksheets.add(SHEETS.detail);
const statsSheet = workbook.worksheets.add(SHEETS.stats);

// Item list
itemSheet.getRange("A1:C1").values = [["深海6楼箱子物品清单", "", ""]];
styleTitle(itemSheet.getRange("A1:C1"), "#0F6B66");
rowHeight(itemSheet, 1, 28);
itemSheet.getRange("A3:C3").values = [["序号", "物品", "备注"]];
styleHeader(itemSheet.getRange("A3:C3"));
itemSheet.getRange(`A4:C${3 + ITEMS.length}`).values = ITEMS.map((item, idx) => [
  idx + 1,
  item,
  "",
]);
addStandardBodyStyle(itemSheet, `A1:C${3 + ITEMS.length}`);
colWidth(itemSheet, "A", 58);
colWidth(itemSheet, "B", 170);
colWidth(itemSheet, "C", 180);

// Batch log
batchSheet.getRange("A1:E1").values = [["深海6楼箱子开箱批次", "", "", "", ""]];
styleTitle(batchSheet.getRange("A1:E1"), "#0F6B66");
rowHeight(batchSheet, 1, 30);
batchSheet.getRange("A2:E2").values = [[
  "每次开箱先在这里新增一行：填日期、开了多少箱；批次ID可直接用于明细备注。",
  "",
  "",
  "",
  "",
]];
try {
  batchSheet.getRange("A2:E2").merge();
  batchSheet.getRange("A2:E2").format.fill.color = "#EEF7F5";
  batchSheet.getRange("A2:E2").format.font.color = "#295754";
} catch {}
batchSheet.getRange("A3:E3").values = [["批次ID", "日期", "箱子数", "场景", "备注"]];
styleHeader(batchSheet.getRange("A3:E3"));
const batchRows = Array.from({ length: BATCH_ROWS }, (_, idx) => [
  `S6-${String(idx + 1).padStart(3, "0")}`,
  "",
  "",
  "深海6楼",
  "",
]);
batchSheet.getRange(`A${batchStartRow}:E${batchEndRow}`).values = batchRows;
addWholeNumberValidation(
  batchSheet.getRange(`C${batchStartRow}:C${batchEndRow}`),
  1,
  999999,
  "箱子数无效",
  "箱子数请输入大于 0 的整数。"
);
addStandardBodyStyle(batchSheet, `A1:E${batchEndRow}`);
setNumberFormat(batchSheet.getRange(`B${batchStartRow}:B${batchEndRow}`), "yyyy-mm-dd");
setNumberFormat(batchSheet.getRange(`C${batchStartRow}:C${batchEndRow}`), "0");
colWidth(batchSheet, "A", 88);
colWidth(batchSheet, "B", 105);
colWidth(batchSheet, "C", 82);
colWidth(batchSheet, "D", 88);
colWidth(batchSheet, "E", 260);

// Drop detail log
detailSheet.getRange("A1:E1").values = [["深海6楼箱子掉落明细", "", "", "", ""]];
styleTitle(detailSheet.getRange("A1:E1"), "#0F6B66");
rowHeight(detailSheet, 1, 30);
detailSheet.getRange("A2:E2").values = [[
  "每批开完后，在这里用下拉选择物品并填写数量；同一批有几种物品就填几行。",
  "",
  "",
  "",
  "",
]];
try {
  detailSheet.getRange("A2:E2").merge();
  detailSheet.getRange("A2:E2").format.fill.color = "#EEF7F5";
  detailSheet.getRange("A2:E2").format.font.color = "#295754";
} catch {}
detailSheet.getRange("A3:E3").values = [["批次ID", "日期", "物品", "数量", "备注"]];
styleHeader(detailSheet.getRange("A3:E3"));
const detailRows = Array.from({ length: DETAIL_ROWS }, () => ["", "", "", "", ""]);
detailSheet.getRange(`A${detailStartRow}:E${detailEndRow}`).values = detailRows;
addListValidation(
  detailSheet.getRange(`A${detailStartRow}:A${detailEndRow}`),
  `='${SHEETS.batch}'!$A$${batchStartRow}:$A$${batchEndRow}`
);
addListValidation(
  detailSheet.getRange(`C${detailStartRow}:C${detailEndRow}`),
  `='${SHEETS.items}'!$B$4:$B$${3 + ITEMS.length}`
);
addWholeNumberValidation(
  detailSheet.getRange(`D${detailStartRow}:D${detailEndRow}`),
  0,
  999999,
  "数量无效",
  "数量请输入 0 或正整数。"
);
addStandardBodyStyle(detailSheet, `A1:E${detailEndRow}`);
setNumberFormat(detailSheet.getRange(`B${detailStartRow}:B${detailEndRow}`), "yyyy-mm-dd");
setNumberFormat(detailSheet.getRange(`D${detailStartRow}:D${detailEndRow}`), "0");
colWidth(detailSheet, "A", 88);
colWidth(detailSheet, "B", 105);
colWidth(detailSheet, "C", 175);
colWidth(detailSheet, "D", 78);
colWidth(detailSheet, "E", 260);

// Statistics
statsSheet.getRange("A1:G1").values = [["深海6楼箱子出货统计", "", "", "", "", "", ""]];
styleTitle(statsSheet.getRange("A1:G1"), "#0F6B66");
rowHeight(statsSheet, 1, 30);
statsSheet.getRange("A3:B8").values = [
  ["累计箱子数", ""],
  ["记录掉落总数量", ""],
  ["已录批次数", ""],
  ["明细行数", ""],
  ["未填批次的明细", ""],
  ["未填数量的明细", ""],
];
statsSheet.getRange("B3:B8").formulas = [
  [`=SUM('${SHEETS.batch}'!$C$${batchStartRow}:$C$${batchEndRow})`],
  [`=SUM('${SHEETS.detail}'!$D$${detailStartRow}:$D$${detailEndRow})`],
  [`=COUNT('${SHEETS.batch}'!$C$${batchStartRow}:$C$${batchEndRow})`],
  [`=COUNT('${SHEETS.detail}'!$D$${detailStartRow}:$D$${detailEndRow})`],
  [`=COUNTIFS('${SHEETS.detail}'!$C$${detailStartRow}:$C$${detailEndRow},"<>",'${SHEETS.detail}'!$A$${detailStartRow}:$A$${detailEndRow},"")`],
  [`=COUNTIFS('${SHEETS.detail}'!$C$${detailStartRow}:$C$${detailEndRow},"<>",'${SHEETS.detail}'!$D$${detailStartRow}:$D$${detailEndRow},"")`],
];
stylePanel(statsSheet.getRange("A3:B8"), "#F4FAF8");
try {
  statsSheet.getRange("A3:A8").format.font.bold = true;
  statsSheet.getRange("B3:B8").format.font.bold = true;
  statsSheet.getRange("B3:B8").format.horizontalAlignment = "Right";
} catch {}
setNumberFormat(statsSheet.getRange("B3:B8"), "0");

statsSheet.getRange("D3:G8").values = [
  ["说明", "", "", ""],
  ["估计出货率 = 该物品记录数量 / 累计箱子数。若某物品一次掉落多个，请把数量按实际件数记录。", "", "", ""],
  ["数量占比 = 该物品数量 / 全部掉落总数量，用来观察掉落构成。", "", "", ""],
  ["批次页只记录箱子数，明细页只记录掉落；这样长期累计时不容易重复计算箱子数。", "", "", ""],
  ["", "", "", ""],
  ["", "", "", ""],
];
try {
  statsSheet.getRange("D3:G3").merge();
  statsSheet.getRange("D4:G4").merge();
  statsSheet.getRange("D5:G5").merge();
  statsSheet.getRange("D6:G6").merge();
  statsSheet.getRange("D3:G8").format.fill.color = "#FFF8EA";
  statsSheet.getRange("D3:G3").format.font.bold = true;
  statsSheet.getRange("D4:G6").format.wrapText = true;
} catch {}

statsSheet.getRange("A10:G10").values = [[
  "物品",
  "累计数量",
  "估计出货率",
  "数量占比",
  "数量排名",
  "相对条",
  "备注",
]];
styleHeader(statsSheet.getRange("A10:G10"));
statsSheet.getRange(`A${statsStartRow}:A${statsEndRow}`).values = ITEMS.map((item) => [item]);
statsSheet.getRange(`B${statsStartRow}:G${statsEndRow}`).formulas = ITEMS.map((_, idx) => {
  const row = statsStartRow + idx;
  return [
    `=SUMIF('${SHEETS.detail}'!$C$${detailStartRow}:$C$${detailEndRow},A${row},'${SHEETS.detail}'!$D$${detailStartRow}:$D$${detailEndRow})`,
    `=IF($B$3>0,B${row}/$B$3,"")`,
    `=IF($B$4>0,B${row}/$B$4,"")`,
    `=IF(B${row}>0,RANK.EQ(B${row},$B$${statsStartRow}:$B$${statsEndRow}),"")`,
    `=IFERROR(REPT("█",ROUND(C${row}/MAX($C$${statsStartRow}:$C$${statsEndRow})*18,0)),"")`,
    "",
  ];
});
addStandardBodyStyle(statsSheet, `A1:G${statsEndRow}`);
setNumberFormat(statsSheet.getRange(`B${statsStartRow}:B${statsEndRow}`), "0");
setNumberFormat(statsSheet.getRange(`C${statsStartRow}:D${statsEndRow}`), "0.00%");
setNumberFormat(statsSheet.getRange(`E${statsStartRow}:E${statsEndRow}`), "0");
try {
  statsSheet.getRange(`A${statsStartRow}:G${statsEndRow}`).format.fill.color = "#FFFFFF";
  statsSheet.getRange(`F${statsStartRow}:F${statsEndRow}`).format.font.color = "#0F6B66";
  statsSheet.getRange(`F${statsStartRow}:F${statsEndRow}`).format.font.name = "Aptos";
} catch {}
colWidth(statsSheet, "A", 175);
colWidth(statsSheet, "B", 90);
colWidth(statsSheet, "C", 105);
colWidth(statsSheet, "D", 95);
colWidth(statsSheet, "E", 78);
colWidth(statsSheet, "F", 150);
colWidth(statsSheet, "G", 180);

for (const sheet of [itemSheet, batchSheet, detailSheet, statsSheet]) {
  try {
    sheet.showGridlines = false;
  } catch {}
}

const statsInspect = await workbook.inspect({
  kind: "table",
  range: `${SHEETS.stats}!A1:G${statsEndRow}`,
  include: "values,formulas",
  tableMaxRows: 32,
  tableMaxCols: 7,
});
console.log(statsInspect.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const previewDir = path.join(outputDir, "_preview");
await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range] of [
  [SHEETS.items, `A1:C${3 + ITEMS.length}`],
  [SHEETS.batch, "A1:E24"],
  [SHEETS.detail, "A1:E24"],
  [SHEETS.stats, `A1:G${statsEndRow}`],
]) {
  const render = await workbook.render({ sheetName, range, scale: 1 });
  try {
    await saveRenderBlob(render, path.join(previewDir, `${sheetName}.png`));
  } catch {}
  console.log(JSON.stringify({ rendered: sheetName, bytes: render.size ?? render.byteLength ?? null }));
}

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath }));
