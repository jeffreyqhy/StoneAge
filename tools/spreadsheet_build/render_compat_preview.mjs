import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = "/Users/tadaema/Projects/Stone Age_Script/outputs/chest_probability_tool/全服宝箱预览6.1_深海6楼出货统计工具_兼容版.xlsx";
const previewDir = "/Users/tadaema/Projects/Stone Age_Script/outputs/chest_probability_tool/_preview_compat";

async function saveRenderBlob(blob, filePath) {
  if (blob && typeof blob.arrayBuffer === "function") {
    await fs.writeFile(filePath, Buffer.from(await blob.arrayBuffer()));
    return;
  }
  if (blob && typeof blob.bytes === "function") {
    await fs.writeFile(filePath, Buffer.from(await blob.bytes()));
  }
}

await fs.mkdir(previewDir, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));

for (const [sheetName, range] of [
  ["深海6楼_物品", "A1:C22"],
  ["深海6楼_录入", "A1:D24"],
  ["深海6楼_统计", "A1:F29"],
]) {
  const render = await workbook.render({ sheetName, range, scale: 1 });
  await saveRenderBlob(render, path.join(previewDir, `${sheetName}.png`));
  console.log(JSON.stringify({ sheetName, size: render.size ?? render.byteLength ?? null }));
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);
