const state = {
  view: "query",
  itemNames: [],
  sourceNames: [],
  selectedMaterial: null,
  selectedRecipeId: null,
  selectedUpgradeId: null,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindQuery();
  bindMaterials();
  bindRecipes();
  bindUpgrades();
  bindSources();
  bindData();
  bindGlobalActions();
  bootstrap();
});

async function api(path, options = {}) {
  const request = { ...options };
  request.headers = { ...(options.headers || {}) };
  if (request.body && typeof request.body === "object" && !(request.body instanceof ArrayBuffer)) {
    request.headers["Content-Type"] = "application/json";
    request.body = JSON.stringify(request.body);
  }
  const response = await fetch(path, request);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw new Error(payload?.error || `请求失败：${response.status}`);
  }
  return payload.data;
}

async function bootstrap() {
  try {
    const data = await api("/api/bootstrap");
    state.itemNames = data.item_names || [];
    state.sourceNames = data.source_names || [];
    renderDatalist("#itemNames", state.itemNames);
    renderDatalist("#sourceNames", state.sourceNames);
    renderHealth(data.health);
    await refreshCurrentView();
    updateTradeTax();
  } catch (error) {
    notify(error.message, true);
  }
}

function renderHealth(health) {
  if (!health) return;
  const counts = health.counts || {};
  $("#statusLine").textContent = `数据库：${health.db_path} | 材料 ${counts.items || 0} | 出处 ${counts.source_items || 0} | 配方 ${counts.recipes || 0} | 升级 ${counts.upgrade_steps || 0}`;
  $("#diamondRatio").value = health.diamond_per_rmb || 500;
  const statRows = [
    ["材料", counts.items || 0],
    ["出处记录", counts.source_items || 0],
    ["配方", counts.recipes || 0],
    ["升级步骤", counts.upgrade_steps || 0],
    ["价格", counts.item_prices || 0],
    ["别名", counts.item_aliases || 0],
  ];
  $("#databaseStats").replaceChildren(
    ...statRows.map(([label, value]) => {
      const node = document.createElement("div");
      node.className = "stat";
      node.append(el("span", label), el("strong", String(value)));
      return node;
    }),
  );
}

function bindNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", async () => {
      state.view = button.dataset.view;
      $$(".nav-item").forEach((item) => item.classList.toggle("active", item === button));
      $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.view}`));
      await refreshCurrentView();
    });
  });
}

function bindGlobalActions() {
  $("#refreshButton").addEventListener("click", () => bootstrap());
  $("#globalSearchButton").addEventListener("click", () => applyGlobalSearch());
  $("#globalSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") applyGlobalSearch();
  });
}

async function applyGlobalSearch() {
  const value = $("#globalSearch").value.trim();
  if (state.view === "query") {
    $("#queryText").value = value;
    await runQuery();
  } else if (state.view === "materials") {
    $("#materialSearch").value = value;
    await loadMaterials();
  } else if (state.view === "recipes") {
    $("#recipeSearch").value = value;
    await loadRecipes();
  } else if (state.view === "upgrades") {
    $("#upgradeSearch").value = value;
    await loadUpgrades();
  } else if (state.view === "sources") {
    $("#sourceSearch").value = value;
    await loadSources();
  }
}

async function refreshCurrentView() {
  if (state.view === "materials") return loadMaterials();
  if (state.view === "recipes") return loadRecipes();
  if (state.view === "upgrades") return loadUpgrades();
  if (state.view === "sources") {
    await loadSources();
    return loadAliases();
  }
  return Promise.resolve();
}

function bindQuery() {
  $("#runQuery").addEventListener("click", runQuery);
  $("#queryText").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runQuery();
  });
  $("#copyQueryText").addEventListener("click", async () => {
    await navigator.clipboard.writeText($("#queryTextResult").textContent || "");
    notify("已复制查询结果");
  });
  ["tradeTargetNet", "tradeGross", "tradeTaxRate"].forEach((id) => {
    $(`#${id}`).addEventListener("input", updateTradeTax);
  });
}

async function runQuery() {
  const params = new URLSearchParams({
    kind: $("#queryKind").value,
    q: $("#queryText").value.trim(),
    target_quantity: $("#queryTargetQty").value || "1",
    from_level: $("#queryFromLevel").value || "0",
    to_level: $("#queryToLevel").value || "1",
    confidence: $("#queryConfidence").value || "0.95",
  });
  try {
    const result = await api(`/api/query?${params.toString()}`);
    renderQueryResult(result);
  } catch (error) {
    $("#queryTextResult").textContent = error.message;
    renderTable("#queryTable", [], []);
  }
}

function renderQueryResult(result) {
  const kind = result.kind;
  if (kind === "material") {
    renderTable("#queryTable", [
      col("name", "材料"),
      col("price_diamonds", "单价钻", fmtQty),
      col("price_rmb", "RMB", fmtRmb),
      col("source_names", "出处", sourceText, "wrap"),
      col("notes", "备注", textValue, "wrap"),
    ], result.rows || []);
    $("#queryTextResult").textContent = (result.rows || []).map((row) => {
      return `- ${row.name}：出处：${sourceText(row.source_names)}；价格：${money(row.price_diamonds, row.price_rmb)}`;
    }).join("\n");
    return;
  }
  if (kind === "source") {
    renderTable("#queryTable", [
      col("item_name", "材料"),
      col("quantities", "数量", sourceText),
      col("price_diamonds", "单价钻", fmtQty),
      col("price_rmb", "RMB", fmtRmb),
      col("source_name", "出处"),
      col("record_count", "记录"),
    ], result.rows || []);
    $("#queryTextResult").textContent = (result.rows || []).map((row) => {
      const qty = sourceText(row.quantities);
      return `- ${row.item_name}${qty ? ` x${qty}` : ""}，价格：${money(row.price_diamonds, row.price_rmb)}`;
    }).join("\n");
    return;
  }
  const materials = result.materials || [];
  renderTable("#queryTable", [
    col("material_name", "材料"),
    col("standard_quantity", "标准", fmtQty),
    col("expected_quantity", "期望", fmtQty),
    col("safe_quantity", "稳妥", fmtQty),
    col("unit_price_diamonds", "单价钻", fmtQty),
    col("safe_total_diamonds", "合计钻", fmtQty),
    col("sources", "出处", (value) => Array.isArray(value) ? value.join(" / ") : sourceText(value), "wrap"),
  ], materials);
  $("#queryTextResult").textContent = result.text || JSON.stringify(result, null, 2);
}

async function updateTradeTax() {
  const params = new URLSearchParams({
    target_net: $("#tradeTargetNet").value || "0",
    gross: $("#tradeGross").value || "0",
    tax_rate: String((Number($("#tradeTaxRate").value) || 0) / 100),
  });
  try {
    const data = await api(`/api/trade-tax?${params.toString()}`);
    $("#tradeRequired").textContent = `${fmtQty(data.required_gross)} 钻，税 ${fmtQty(data.required_tax)}`;
    $("#tradeNet").textContent = `${fmtQty(data.gross_net)} 钻，税 ${fmtQty(data.gross_tax)}`;
  } catch (error) {
    $("#tradeRequired").textContent = error.message;
    $("#tradeNet").textContent = "-";
  }
}

function bindMaterials() {
  $("#loadMaterials").addEventListener("click", loadMaterials);
  $("#materialSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadMaterials();
  });
  $("#newMaterial").addEventListener("click", clearMaterialForm);
  $("#saveMaterial").addEventListener("click", saveMaterial);
  $("#deleteMaterialPrice").addEventListener("click", deleteSelectedMaterialPrice);
  $("#addSourceItem").addEventListener("click", addSourceItemFromMaterial);
  $("#materialSourcesTable").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source-delete]");
    if (button) deleteSourceItem(button.dataset.sourceDelete, () => loadMaterialSources($("#materialName").value));
  });
}

async function loadMaterials() {
  const q = encodeURIComponent($("#materialSearch").value.trim());
  const rows = await api(`/api/materials?q=${q}`);
  renderTable("#materialsTable", [
    col("name", "材料"),
    col("category", "分类"),
    col("price_diamonds", "钻石", fmtQty),
    col("price_rmb", "RMB", fmtRmb),
    col("source_names", "出处", sourceText, "wrap"),
    col("updated_at", "更新时间"),
  ], rows, {
    selected: state.selectedMaterial?.id,
    rowId: (row) => row.id,
    onRow: selectMaterial,
  });
}

async function selectMaterial(row) {
  state.selectedMaterial = row;
  $("#materialName").value = row.name || "";
  $("#materialCategory").value = row.category || "";
  $("#materialPrice").value = row.price_diamonds ?? "";
  $("#materialPriceSource").value = row.price_source || "网页版录入";
  $("#materialPriceActive").value = row.is_active === 0 ? "0" : "1";
  $("#materialNotes").value = row.notes || "";
  $("#materialPriceNotes").value = row.price_notes || "";
  $("#sourceItemName").value = row.name || "";
  await loadMaterialSources(row.name || "");
}

function clearMaterialForm() {
  state.selectedMaterial = null;
  $("#materialName").value = "";
  $("#materialCategory").value = "";
  $("#materialPrice").value = "";
  $("#materialPriceSource").value = "网页版录入";
  $("#materialPriceActive").value = "1";
  $("#materialNotes").value = "";
  $("#materialPriceNotes").value = "";
  $("#sourceItemName").value = "";
  renderTable("#materialSourcesTable", [], []);
}

async function saveMaterial() {
  try {
    const data = await api("/api/materials", {
      method: "POST",
      body: {
        name: $("#materialName").value,
        category: $("#materialCategory").value,
        notes: $("#materialNotes").value,
        price_diamonds: $("#materialPrice").value,
        price_source: $("#materialPriceSource").value,
        price_notes: $("#materialPriceNotes").value,
        is_active: $("#materialPriceActive").value === "1",
      },
    });
    state.selectedMaterial = data;
    await bootstrap();
    notify("材料已保存");
  } catch (error) {
    notify(error.message, true);
  }
}

async function deleteSelectedMaterialPrice() {
  const priceId = state.selectedMaterial?.price_id;
  if (!priceId) {
    notify("当前材料没有价格记录", true);
    return;
  }
  if (!confirm("确定删除当前材料价格？")) return;
  await api(`/api/prices/${priceId}`, { method: "DELETE" });
  $("#materialPrice").value = "";
  await loadMaterials();
  notify("价格已删除");
}

async function loadMaterialSources(itemName) {
  if (!itemName) {
    renderTable("#materialSourcesTable", [], []);
    return;
  }
  const rows = await api(`/api/item-sources?item_name=${encodeURIComponent(itemName)}`);
  renderTable("#materialSourcesTable", [
    col("source_name", "出处"),
    col("raw_text", "原文"),
    col("parsed_quantity", "数量", fmtQty),
    col("source_type", "类型"),
    actionCol("操作", (row) => deleteButton("source-delete", row.id)),
  ], rows);
}

async function addSourceItemFromMaterial() {
  try {
    const itemName = $("#sourceItemName").value.trim() || $("#materialName").value.trim();
    const qty = $("#sourceQty").value;
    await api("/api/source-items", {
      method: "POST",
      body: {
        item_name: itemName,
        source_name: $("#sourceName").value,
        parsed_quantity: qty,
        raw_text: qty ? `${itemName}${qty}` : itemName,
        source_type: "manual",
      },
    });
    $("#sourceName").value = "";
    $("#sourceQty").value = "";
    await loadMaterialSources(itemName);
    await bootstrap();
    notify("出处已新增");
  } catch (error) {
    notify(error.message, true);
  }
}

function bindRecipes() {
  $("#loadRecipes").addEventListener("click", loadRecipes);
  $("#recipeSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadRecipes();
  });
  $("#newRecipe").addEventListener("click", clearRecipeForm);
  $("#addRecipeMaterial").addEventListener("click", () => addMaterialEditorRow("#recipeMaterialsTable"));
  $("#saveRecipe").addEventListener("click", saveRecipe);
  $("#deleteRecipe").addEventListener("click", deleteRecipe);
  $("#calculateRecipe").addEventListener("click", calculateRecipe);
}

async function loadRecipes() {
  const q = encodeURIComponent($("#recipeSearch").value.trim());
  const rows = await api(`/api/recipes?q=${q}`);
  renderTable("#recipesTable", [
    col("product_name", "成品"),
    col("category", "分类"),
    col("recipe_type", "类型"),
    col("success_rate", "成功率", percent),
    col("material_count", "材料数"),
    col("updated_at", "更新时间"),
  ], rows, {
    selected: state.selectedRecipeId,
    rowId: (row) => row.id,
    onRow: (row) => loadRecipe(row.id),
  });
}

async function loadRecipe(id) {
  const recipe = await api(`/api/recipes/${id}`);
  state.selectedRecipeId = recipe.id;
  $("#recipeId").value = recipe.id || "";
  $("#recipeProduct").value = recipe.product_name || "";
  $("#recipeCategory").value = recipe.category || "其他";
  $("#recipeType").value = recipe.recipe_type || "打造";
  $("#recipeSuccess").value = (Number(recipe.success_rate || 1) * 100).toFixed(2);
  $("#recipeOutputQty").value = recipe.output_quantity || 1;
  $("#recipeDiamond").value = recipe.diamond_cost || 0;
  $("#recipeNotes").value = recipe.notes || "";
  $("#recipeFailMaterials").checked = Boolean(recipe.failure_consumes_materials);
  $("#recipeFailDiamonds").checked = Boolean(recipe.failure_consumes_diamonds);
  $("#recipeFailCoin").checked = Boolean(recipe.failure_consumes_coin);
  renderMaterialEditor("#recipeMaterialsTable", recipe.materials || []);
  await loadRecipes();
}

function clearRecipeForm() {
  state.selectedRecipeId = null;
  $("#recipeId").value = "";
  $("#recipeProduct").value = "";
  $("#recipeCategory").value = "其他";
  $("#recipeType").value = "打造";
  $("#recipeSuccess").value = "100";
  $("#recipeOutputQty").value = "1";
  $("#recipeDiamond").value = "0";
  $("#recipeNotes").value = "";
  $("#recipeFailMaterials").checked = true;
  $("#recipeFailDiamonds").checked = true;
  $("#recipeFailCoin").checked = true;
  renderMaterialEditor("#recipeMaterialsTable", []);
}

async function saveRecipe() {
  try {
    const id = $("#recipeId").value;
    const result = await api("/api/recipes", {
      method: "POST",
      body: {
        id: id || null,
        recipe: {
          product_name: $("#recipeProduct").value,
          category: $("#recipeCategory").value,
          recipe_type: $("#recipeType").value,
          success_rate: Number($("#recipeSuccess").value || 100) / 100,
          output_quantity: Number($("#recipeOutputQty").value || 1),
          diamond_cost: Number($("#recipeDiamond").value || 0),
          failure_consumes_materials: $("#recipeFailMaterials").checked,
          failure_consumes_diamonds: $("#recipeFailDiamonds").checked,
          failure_consumes_coin: $("#recipeFailCoin").checked,
          notes: $("#recipeNotes").value,
        },
        materials: readMaterialEditor("#recipeMaterialsTable"),
      },
    });
    await loadRecipe(result.id);
    await bootstrap();
    notify("配方已保存");
  } catch (error) {
    notify(error.message, true);
  }
}

async function deleteRecipe() {
  const id = $("#recipeId").value;
  if (!id || !confirm("确定删除这个配方？")) return;
  await api(`/api/recipes/${id}`, { method: "DELETE" });
  clearRecipeForm();
  await loadRecipes();
  notify("配方已删除");
}

async function calculateRecipe() {
  try {
    const result = await api("/api/recipe-cost", {
      method: "POST",
      body: {
        product_name: $("#recipeProduct").value,
        target_quantity: Number($("#queryTargetQty").value || 1),
        confidence: Number($("#queryConfidence").value || 0.95),
      },
    });
    state.view = "query";
    $(".nav-item[data-view='query']").click();
    renderQueryResult(result);
  } catch (error) {
    notify(error.message, true);
  }
}

function bindUpgrades() {
  $("#loadUpgrades").addEventListener("click", loadUpgrades);
  $("#upgradeSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadUpgrades();
  });
  $("#newUpgrade").addEventListener("click", clearUpgradeForm);
  $("#addUpgradeMaterial").addEventListener("click", () => addMaterialEditorRow("#upgradeMaterialsTable"));
  $("#saveUpgrade").addEventListener("click", saveUpgrade);
  $("#deleteUpgrade").addEventListener("click", deleteUpgrade);
  $("#calculateUpgrade").addEventListener("click", calculateUpgrade);
}

async function loadUpgrades() {
  const q = encodeURIComponent($("#upgradeSearch").value.trim());
  const rows = await api(`/api/upgrades?q=${q}`);
  renderTable("#upgradesTable", [
    col("equipment_name", "装备"),
    col("from_level", "从"),
    col("to_level", "到"),
    col("success_rate", "成功率", percent),
    col("diamond_cost", "钻石", fmtQty),
    col("material_count", "材料数"),
    col("updated_at", "更新时间"),
  ], rows, {
    selected: state.selectedUpgradeId,
    rowId: (row) => row.id,
    onRow: (row) => loadUpgrade(row.id),
  });
}

async function loadUpgrade(id) {
  const step = await api(`/api/upgrades/${id}`);
  state.selectedUpgradeId = step.id;
  $("#upgradeId").value = step.id || "";
  $("#upgradeEquipment").value = step.equipment_name || "";
  $("#upgradeFrom").value = step.from_level || 0;
  $("#upgradeTo").value = step.to_level || 1;
  $("#upgradeSuccess").value = (Number(step.success_rate || 1) * 100).toFixed(2);
  $("#upgradeDiamond").value = step.diamond_cost || 0;
  $("#upgradeCoin").value = step.coin_cost || 0;
  $("#upgradeNotes").value = step.notes || "";
  $("#upgradeFailMaterials").checked = Boolean(step.failure_consumes_materials);
  $("#upgradeFailDiamonds").checked = Boolean(step.failure_consumes_diamonds);
  $("#upgradeDowngrade").checked = Boolean(step.failure_downgrades_level);
  renderMaterialEditor("#upgradeMaterialsTable", step.materials || []);
  await loadUpgrades();
}

function clearUpgradeForm() {
  state.selectedUpgradeId = null;
  $("#upgradeId").value = "";
  $("#upgradeEquipment").value = "";
  $("#upgradeFrom").value = "0";
  $("#upgradeTo").value = "1";
  $("#upgradeSuccess").value = "100";
  $("#upgradeDiamond").value = "0";
  $("#upgradeCoin").value = "0";
  $("#upgradeNotes").value = "";
  $("#upgradeFailMaterials").checked = true;
  $("#upgradeFailDiamonds").checked = true;
  $("#upgradeDowngrade").checked = false;
  renderMaterialEditor("#upgradeMaterialsTable", []);
}

async function saveUpgrade() {
  try {
    const id = $("#upgradeId").value;
    const result = await api("/api/upgrades", {
      method: "POST",
      body: {
        id: id || null,
        step: {
          equipment_name: $("#upgradeEquipment").value,
          from_level: Number($("#upgradeFrom").value || 0),
          to_level: Number($("#upgradeTo").value || 1),
          success_rate: Number($("#upgradeSuccess").value || 100) / 100,
          diamond_cost: Number($("#upgradeDiamond").value || 0),
          coin_cost: Number($("#upgradeCoin").value || 0),
          failure_consumes_materials: $("#upgradeFailMaterials").checked,
          failure_consumes_diamonds: $("#upgradeFailDiamonds").checked,
          failure_downgrades_level: $("#upgradeDowngrade").checked,
          notes: $("#upgradeNotes").value,
        },
        materials: readMaterialEditor("#upgradeMaterialsTable"),
      },
    });
    await loadUpgrade(result.id);
    await bootstrap();
    notify("升级步骤已保存");
  } catch (error) {
    notify(error.message, true);
  }
}

async function deleteUpgrade() {
  const id = $("#upgradeId").value;
  if (!id || !confirm("确定删除这个升级步骤？")) return;
  await api(`/api/upgrades/${id}`, { method: "DELETE" });
  clearUpgradeForm();
  await loadUpgrades();
  notify("升级步骤已删除");
}

async function calculateUpgrade() {
  try {
    const result = await api("/api/upgrade-cost", {
      method: "POST",
      body: {
        equipment_name: $("#upgradeEquipment").value,
        from_level: Number($("#upgradeFrom").value || 0),
        to_level: Number($("#upgradeTo").value || 1),
        confidence: Number($("#queryConfidence").value || 0.95),
      },
    });
    state.view = "query";
    $(".nav-item[data-view='query']").click();
    renderQueryResult(result);
  } catch (error) {
    notify(error.message, true);
  }
}

function bindSources() {
  $("#loadSources").addEventListener("click", loadSources);
  $("#sourceSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadSources();
  });
  $("#sourcesTable").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source-delete]");
    if (button) deleteSourceItem(button.dataset.sourceDelete, loadSources);
  });
  $("#loadAliases").addEventListener("click", loadAliases);
  $("#aliasSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadAliases();
  });
  $("#addAlias").addEventListener("click", addAlias);
  $("#aliasesTable").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-alias-delete]");
    if (button) deleteAlias(button.dataset.aliasDelete);
  });
}

async function loadSources() {
  const q = encodeURIComponent($("#sourceSearch").value.trim());
  const rows = await api(`/api/source-items?q=${q}`);
  renderTable("#sourcesTable", [
    col("item_name", "材料"),
    col("source_name", "出处"),
    col("raw_text", "原文", textValue, "wrap"),
    col("parsed_quantity", "数量", fmtQty),
    col("sheet_name", "Sheet"),
    col("row_index", "行"),
    actionCol("操作", (row) => deleteButton("source-delete", row.id)),
  ], rows);
}

async function deleteSourceItem(id, callback) {
  if (!id || !confirm("确定删除这条出处关系？")) return;
  await api(`/api/source-items/${id}`, { method: "DELETE" });
  await callback();
  await bootstrap();
  notify("出处关系已删除");
}

async function loadAliases() {
  const q = encodeURIComponent($("#aliasSearch").value.trim());
  const rows = await api(`/api/aliases?q=${q}`);
  renderTable("#aliasesTable", [
    col("item_name", "正式名"),
    col("alias", "别名"),
    col("created_at", "创建时间"),
    actionCol("操作", (row) => deleteButton("alias-delete", row.id)),
  ], rows);
}

async function addAlias() {
  try {
    await api("/api/aliases", {
      method: "POST",
      body: { item_name: $("#aliasItem").value, alias: $("#aliasValue").value },
    });
    $("#aliasValue").value = "";
    await bootstrap();
    await loadAliases();
    notify("别名已新增");
  } catch (error) {
    notify(error.message, true);
  }
}

async function deleteAlias(id) {
  if (!id) return;
  await api(`/api/aliases/${id}`, { method: "DELETE" });
  await bootstrap();
  await loadAliases();
  notify("别名已删除");
}

function bindData() {
  $("#saveSettings").addEventListener("click", saveSettings);
  $$("[data-download]").forEach((button) => {
    button.addEventListener("click", () => {
      window.location.href = button.dataset.download;
    });
  });
  $("#jsonImport").addEventListener("change", importJsonBackup);
  $("#priceCsvImport").addEventListener("change", importPriceCsv);
  $("#excelImport").addEventListener("change", importExcel);
}

async function saveSettings() {
  try {
    await api("/api/settings", {
      method: "POST",
      body: { diamond_per_rmb: Number($("#diamondRatio").value || 0) },
    });
    await bootstrap();
    notify("设置已保存");
  } catch (error) {
    notify(error.message, true);
  }
}

async function importJsonBackup(event) {
  const file = event.target.files[0];
  if (!file) return;
  if (!confirm("恢复 JSON 会覆盖当前材料库数据库。确定继续？")) {
    event.target.value = "";
    return;
  }
  try {
    const payload = JSON.parse(await file.text());
    await api("/api/import/json", { method: "POST", body: payload });
    await bootstrap();
    notify("数据库 JSON 已导入");
  } catch (error) {
    notify(error.message, true);
  } finally {
    event.target.value = "";
  }
}

async function importPriceCsv(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const text = await file.text();
    const data = await api("/api/import/prices-csv", {
      method: "POST",
      headers: { "Content-Type": "text/csv; charset=utf-8" },
      body: text,
    });
    await bootstrap();
    notify(`已导入 ${data.imported} 条价格`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    event.target.value = "";
  }
}

async function importExcel(event) {
  const file = event.target.files[0];
  if (!file) return;
  const mode = $("#excelMode").value;
  if (mode === "replace" && !confirm("覆盖模式会删除旧的 Excel 导入记录。确定继续？")) {
    event.target.value = "";
    return;
  }
  try {
    const params = new URLSearchParams({ mode, filename: file.name });
    const data = await api(`/api/import/excel?${params.toString()}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: await file.arrayBuffer(),
    });
    await bootstrap();
    notify(`Excel 已导入 ${data.record_count || 0} 条，跳过 ${data.skipped_count || 0} 条`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    event.target.value = "";
  }
}

function renderMaterialEditor(selector, rows) {
  const table = $(selector);
  table.replaceChildren();
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["材料名称", "数量", "备注", "操作"].forEach((label) => headRow.append(th(label)));
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  table.append(thead, tbody);
  (rows || []).forEach((row) => addMaterialEditorRow(selector, row));
}

function addMaterialEditorRow(selector, row = {}) {
  const tbody = $(`${selector} tbody`) || createMaterialEditorBody(selector);
  const tr = document.createElement("tr");
  tr.append(
    editorCell("material_name", row.material_name || "", "text", "材料名称", "itemNames"),
    editorCell("quantity", row.quantity || 1, "number", "数量"),
    editorCell("notes", row.notes || "", "text", "备注"),
  );
  const action = document.createElement("td");
  action.className = "actions";
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "删除";
  button.addEventListener("click", () => tr.remove());
  action.append(button);
  tr.append(action);
  tbody.append(tr);
}

function createMaterialEditorBody(selector) {
  renderMaterialEditor(selector, []);
  return $(`${selector} tbody`);
}

function editorCell(name, value, type, placeholder, listId = "") {
  const td = document.createElement("td");
  const input = document.createElement("input");
  input.name = name;
  input.type = type;
  input.value = value;
  input.placeholder = placeholder;
  if (type === "number") {
    input.min = "0";
    input.step = "0.01";
  }
  if (listId) input.setAttribute("list", listId);
  td.append(input);
  return td;
}

function readMaterialEditor(selector) {
  return $$(`${selector} tbody tr`).map((row) => {
    const data = {};
    row.querySelectorAll("input").forEach((input) => {
      data[input.name] = input.value;
    });
    return {
      material_name: data.material_name || "",
      quantity: Number(data.quantity || 0),
      notes: data.notes || "",
    };
  }).filter((row) => row.material_name && row.quantity > 0);
}

function renderTable(selector, columns, rows, options = {}) {
  const table = $(selector);
  table.replaceChildren();
  if (!columns.length) return;
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach((column) => headRow.append(th(column.label)));
  thead.append(headRow);
  const tbody = document.createElement("tbody");
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (options.selected && options.rowId?.(row) === options.selected) tr.classList.add("selected");
    columns.forEach((column) => {
      const td = document.createElement("td");
      if (column.className) td.className = column.className;
      if (column.action) {
        td.classList.add("actions");
        td.append(column.action(row));
      } else {
        td.textContent = column.format ? column.format(row[column.key], row) : textValue(row[column.key]);
      }
      tr.append(td);
    });
    if (options.onRow) {
      tr.addEventListener("click", (event) => {
        if (!event.target.closest("button")) options.onRow(row);
      });
    }
    tbody.append(tr);
  });
  table.append(thead, tbody);
}

function col(key, label, format = textValue, className = "") {
  return { key, label, format, className };
}

function actionCol(label, action) {
  return { label, action };
}

function th(text) {
  return el("th", text);
}

function el(tag, text = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  return node;
}

function deleteButton(name, id) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "danger";
  button.dataset[toDatasetKey(name)] = String(id);
  button.textContent = "删除";
  return button;
}

function toDatasetKey(value) {
  return value.replace(/-([a-z])/g, (_, char) => char.toUpperCase());
}

function renderDatalist(selector, values) {
  const list = $(selector);
  list.replaceChildren(...values.map((value) => {
    const option = document.createElement("option");
    option.value = value;
    return option;
  }));
}

function textValue(value) {
  if (value === null || value === undefined) return "";
  return String(value);
}

function fmtQty(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function fmtRmb(value) {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "";
}

function percent(value) {
  if (value === null || value === undefined || value === "") return "";
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function sourceText(value) {
  if (Array.isArray(value)) return value.join(" / ");
  return String(value || "").split(",").filter(Boolean).join(" / ");
}

function money(diamonds, rmb) {
  if (diamonds === null || diamonds === undefined || diamonds === "") return "暂无价格";
  return `${fmtQty(diamonds)}钻 / ${fmtRmb(rmb)} RMB`;
}

let toastTimer = null;
function notify(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.style.background = isError ? "#7f1d1d" : "#111827";
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2600);
}
