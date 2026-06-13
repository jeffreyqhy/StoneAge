import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "/Users/tadaema/Documents/sqsd/全服宝箱预览6.1.xlsx";
const outputDir = "/Users/tadaema/Projects/Stone Age_Script/outputs/chest_probability_tool";
const outputPath = path.join(outputDir, "全服宝箱预览6.1_深海6楼出货统计工具_简化版.xlsx");

const SHEETS = {
  input: "深海6楼_录入",
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

const INPUT_ROWS = 5000;
const inputStartRow = 4;
const inputEndRow = inputStartRow + INPUT_ROWS - 1;
const statsStartRow = 11;
const statsEndRow = statsStartRow + ITEMS.length - 1;
const dailyStartRow = 11;
const dailyRows = 500;
const dailyEndRow = dailyStartRow + dailyRows - 1;

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
    await fs.writeFile(filePath, Buffer.from(await blob.arrayBuffer()));
    return;
  }
  if (blob && typeof blob.bytes === "function") {
    await fs.writeFile(filePath, Buffer.from(await blob.bytes()));
  }
}

const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);

const itemSheet = workbook.worksheets.add(SHEETS.items);
const inputSheet = workbook.worksheets.add(SHEETS.input);
const statsSheet = workbook.worksheets.add(SHEETS.stats);

// Item list
itemSheet.getRange("A1:C1").values = [["深海6楼箱子物品清单", "", ""]];
styleTitle(itemSheet.getRange("A1:C1"));
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

// Simplified input log
inputSheet.getRange("A1:D1").values = [["深海6楼箱子掉落录入", "", "", ""]];
styleTitle(inputSheet.getRange("A1:D1"));
rowHeight(inputSheet, 1, 30);
inputSheet.getRange("A2:D2").values = [[
  "只填日期、物品、数量；数量合计就是箱子数，同一天自动算一个批次。",
  "",
  "",
  "",
]];
try {
  inputSheet.getRange("A2:D2").merge();
  inputSheet.getRange("A2:D2").format.fill.color = "#EEF7F5";
  inputSheet.getRange("A2:D2").format.font.color = "#295754";
} catch {}
inputSheet.getRange("A3:D3").values = [["日期", "物品", "数量", "备注"]];
styleHeader(inputSheet.getRange("A3:D3"));
inputSheet.getRange(`A${inputStartRow}:D${inputEndRow}`).values = Array.from(
  { length: INPUT_ROWS },
  () => ["", "", "", ""]
);
addListValidation(
  inputSheet.getRange(`B${inputStartRow}:B${inputEndRow}`),
  `='${SHEETS.items}'!$B$4:$B$${3 + ITEMS.length}`
);
addWholeNumberValidation(
  inputSheet.getRange(`C${inputStartRow}:C${inputEndRow}`),
  0,
  999999,
  "数量无效",
  "数量请输入 0 或正整数。"
);
addStandardBodyStyle(inputSheet, `A1:D${inputEndRow}`);
setNumberFormat(inputSheet.getRange(`A${inputStartRow}:A${inputEndRow}`), "yyyy-mm-dd");
setNumberFormat(inputSheet.getRange(`C${inputStartRow}:C${inputEndRow}`), "0");
colWidth(inputSheet, "A", 105);
colWidth(inputSheet, "B", 175);
colWidth(inputSheet, "C", 78);
colWidth(inputSheet, "D", 260);

// Statistics
statsSheet.getRange("A1:J1").values = [["深海6楼箱子出货统计", "", "", "", "", "", "", "", "", ""]];
styleTitle(statsSheet.getRange("A1:J1"));
rowHeight(statsSheet, 1, 30);
statsSheet.getRange("A3:B8").values = [
  ["累计箱子数", ""],
  ["记录批次数", ""],
  ["平均每批箱子数", ""],
  ["明细行数", ""],
  ["未填日期的明细", ""],
  ["未填数量的明细", ""],
];
statsSheet.getRange("B3:B8").formulas = [
  [`=SUM('${SHEETS.input}'!$C$${inputStartRow}:$C$${inputEndRow})`],
  [
    `=IFERROR(SUMPRODUCT(('${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow}<>"")/COUNTIF('${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow},'${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow})),0)`,
  ],
  [`=IF($B$4>0,$B$3/$B$4,"")`],
  [`=COUNT('${SHEETS.input}'!$C$${inputStartRow}:$C$${inputEndRow})`],
  [`=COUNTIFS('${SHEETS.input}'!$B$${inputStartRow}:$B$${inputEndRow},"<>",'${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow},"")`],
  [`=COUNTIFS('${SHEETS.input}'!$B$${inputStartRow}:$B$${inputEndRow},"<>",'${SHEETS.input}'!$C$${inputStartRow}:$C$${inputEndRow},"")`],
];
stylePanel(statsSheet.getRange("A3:B8"));
try {
  statsSheet.getRange("A3:A8").format.font.bold = true;
  statsSheet.getRange("B3:B8").format.font.bold = true;
  statsSheet.getRange("B3:B8").format.horizontalAlignment = "Right";
} catch {}
setNumberFormat(statsSheet.getRange("B3:B8"), "0.00");

statsSheet.getRange("D3:G6").values = [
  ["说明", "", "", ""],
  ["每个箱子只按最终掉落物记录一次，所以数量合计就是开箱数。", "", "", ""],
  ["同一天多行记录会自动算作 1 个批次。", "", "", ""],
  ["出货率 = 该物品累计数量 / 累计箱子数。", "", "", ""],
];
try {
  statsSheet.getRange("D3:G3").merge();
  statsSheet.getRange("D4:G4").merge();
  statsSheet.getRange("D5:G5").merge();
  statsSheet.getRange("D6:G6").merge();
  statsSheet.getRange("D3:G6").format.fill.color = "#FFF8EA";
  statsSheet.getRange("D3:G3").format.font.bold = true;
  statsSheet.getRange("D4:G6").format.wrapText = true;
} catch {}

statsSheet.getRange("A10:F10").values = [[
  "物品",
  "累计数量",
  "出货率",
  "数量排名",
  "相对条",
  "备注",
]];
styleHeader(statsSheet.getRange("A10:F10"));
statsSheet.getRange(`A${statsStartRow}:A${statsEndRow}`).values = ITEMS.map((item) => [item]);
statsSheet.getRange(`B${statsStartRow}:F${statsEndRow}`).formulas = ITEMS.map((_, idx) => {
  const row = statsStartRow + idx;
  return [
    `=SUMIF('${SHEETS.input}'!$B$${inputStartRow}:$B$${inputEndRow},A${row},'${SHEETS.input}'!$C$${inputStartRow}:$C$${inputEndRow})`,
    `=IF($B$3>0,B${row}/$B$3,"")`,
    `=IF(B${row}>0,RANK.EQ(B${row},$B$${statsStartRow}:$B$${statsEndRow}),"")`,
    `=IFERROR(REPT("█",ROUND(C${row}/MAX($C$${statsStartRow}:$C$${statsEndRow})*18,0)),"")`,
    "",
  ];
});
setNumberFormat(statsSheet.getRange(`B${statsStartRow}:B${statsEndRow}`), "0");
setNumberFormat(statsSheet.getRange(`C${statsStartRow}:C${statsEndRow}`), "0.00%");
setNumberFormat(statsSheet.getRange(`D${statsStartRow}:D${statsEndRow}`), "0");

statsSheet.getRange("H10:J10").values = [["日期", "箱子数", "记录行数"]];
styleHeader(statsSheet.getRange("H10:J10"));
statsSheet.getRange("H11").formulas = [
  [`=IFERROR(SORT(UNIQUE(FILTER('${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow},'${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow}<>""))),"")`],
];
statsSheet.getRange(`I${dailyStartRow}:J${dailyEndRow}`).formulas = Array.from(
  { length: dailyRows },
  (_, idx) => {
    const row = dailyStartRow + idx;
    return [
      `=IF(H${row}="","",SUMIFS('${SHEETS.input}'!$C$${inputStartRow}:$C$${inputEndRow},'${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow},H${row}))`,
      `=IF(H${row}="","",COUNTIFS('${SHEETS.input}'!$A$${inputStartRow}:$A$${inputEndRow},H${row},'${SHEETS.input}'!$B$${inputStartRow}:$B$${inputEndRow},"<>"))`,
    ];
  }
);
setNumberFormat(statsSheet.getRange(`H${dailyStartRow}:H${dailyEndRow}`), "yyyy-mm-dd");
setNumberFormat(statsSheet.getRange(`I${dailyStartRow}:J${dailyEndRow}`), "0");
addStandardBodyStyle(statsSheet, `A1:J${Math.max(statsEndRow, dailyEndRow)}`);
try {
  statsSheet.getRange(`E${statsStartRow}:E${statsEndRow}`).format.font.color = "#0F6B66";
} catch {}
colWidth(statsSheet, "A", 175);
colWidth(statsSheet, "B", 90);
colWidth(statsSheet, "C", 92);
colWidth(statsSheet, "D", 78);
colWidth(statsSheet, "E", 150);
colWidth(statsSheet, "F", 150);
colWidth(statsSheet, "G", 28);
colWidth(statsSheet, "H", 105);
colWidth(statsSheet, "I", 78);
colWidth(statsSheet, "J", 78);

for (const sheet of [itemSheet, inputSheet, statsSheet]) {
  try {
    sheet.showGridlines = false;
  } catch {}
}

const statsInspect = await workbook.inspect({
  kind: "table",
  range: `${SHEETS.stats}!A1:J30`,
  include: "values,formulas",
  tableMaxRows: 30,
  tableMaxCols: 10,
});
console.log(statsInspect.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

const previewDir = path.join(outputDir, "_preview_simple");
await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range] of [
  [SHEETS.items, `A1:C${3 + ITEMS.length}`],
  [SHEETS.input, "A1:D24"],
  [SHEETS.stats, "A1:J30"],
]) {
  const render = await workbook.render({ sheetName, range, scale: 1 });
  await saveRenderBlob(render, path.join(previewDir, `${sheetName}.png`));
  console.log(JSON.stringify({ rendered: sheetName, bytes: render.size ?? render.byteLength ?? null }));
}

await fs.mkdir(outputDir, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath }));
