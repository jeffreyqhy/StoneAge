const state = {
  data: null,
  view: "materials",
  query: "",
};

const DEFAULT_SITE = {
  meta: {
    title: "石器时代-精灵召唤",
    subtitle: "官方网站",
    body: "经典回合、精灵养成、家族协作与活动公告统一整理。",
    badge: "官方论坛",
    meta: { author: "烈焰部落 - 花儿", service_wechat: "djinhe" },
  },
  stats: [
    { title: "游戏下载", body: "客户端下载与更新入口", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2", badge: "入口" },
    { title: "游戏公告", body: "版本更新与维护说明", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3", badge: "公告" },
    { title: "客服微信", body: "djinhe", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=7", badge: "客服" },
  ],
  boards: [
    {
      title: "游戏下载",
      body: "客户端下载、安卓包、iOS TestFlight 与社群入口。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2",
      badge: "下载",
      meta: {
        posts: [
          { title: "游戏下载", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=1", author: "admin" },
          { title: "下載", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=16", author: "admin" },
        ],
      },
    },
    {
      title: "游戏公告",
      body: "版本更新、活动上下架、奖励调整与维护说明。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3",
      badge: "公告",
      meta: {
        posts: [
          { title: "4月4日21点不停机更新，如有争议将重新调整", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=15", author: "admin" },
          { title: "3月27日23点不停机更新，如有争议将重新调整", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=14", author: "admin" },
          { title: "3月23日14点30不停机更新", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=13", author: "admin" },
          { title: "3月21日15点不停机更新", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=12", author: "admin" },
          { title: "3月14日18点30不停机更新", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=8", author: "admin" },
          { title: "2月28日中午11点不停机更新", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=7", author: "admin" },
          { title: "2月14日18點不停機更新", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=2", author: "admin" },
        ],
      },
    },
    {
      title: "问题解答",
      body: "客服与常见问题入口，后续可继续补充官方 FAQ。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=4",
      badge: "答疑",
      meta: { posts: [{ title: "客服微信：djinhe", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=3", author: "admin" }] },
    },
    {
      title: "攻略分享",
      body: "过滤玩家广告贴，只展示 admin 维护的攻略内容。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=5",
      badge: "攻略",
      meta: { posts: [{ title: "游戏设置", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=9", author: "admin" }] },
    },
    {
      title: "练宠活动",
      body: "练宠活动与奖励说明。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=6",
      badge: "活动",
      meta: { posts: [{ title: "赤炼灵姬练宠活动", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=4", author: "admin" }] },
    },
    {
      title: "客服微信",
      body: "官方客服微信入口。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=7",
      badge: "客服",
      meta: { posts: [{ title: "客服微信", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=6", author: "admin" }] },
    },
    {
      title: "家族收人",
      body: "家族招募与组队社群入口。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=8",
      badge: "家族",
      meta: { posts: [] },
    },
    {
      title: "来吉卡",
      body: "来吉卡相关说明与活动入口。",
      url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=9",
      badge: "来吉卡",
      meta: { posts: [{ title: "来吉卡", url: "https://www.djinhe.cn/forum.php?mod=viewthread&tid=5", author: "admin" }] },
    },
  ],
  announcements: [
    { title: "4月4日21点不停机更新", body: "最新更新公告以官方论坛原帖为准。", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=3", badge: "更新" },
    { title: "游戏下载", body: "客户端下载、补丁与安装说明集中在论坛下载版块。", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=2", badge: "下载" },
    { title: "赤炼灵姬练宠活动", body: "活动规则与奖励以论坛活动帖为准。", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=6", badge: "活动" },
  ],
  features: [
    { title: "精灵召唤", body: "围绕精灵培养、练宠活动和长期成长路线做内容整理。", badge: "召唤" },
    { title: "装备打造", body: "资料库作为独立工具入口提供价格、出处、配方和升级路线计算。", badge: "打造" },
    { title: "家族协作", body: "家族收人、组队副本和攻略分享都可从官网入口进入。", badge: "家族" },
    { title: "市场交易", body: "交易计算器收进工具区，首页只保留官方内容入口。", badge: "交易" },
  ],
  links: [
    { title: "官方论坛", body: "公告、攻略、活动与客服入口", url: "https://www.djinhe.cn/", badge: "论坛" },
    { title: "攻略分享", body: "玩家经验与副本资料", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=5", badge: "攻略" },
    { title: "家族收人", body: "家族招募与组队信息", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=8", badge: "家族" },
    { title: "来吉卡", body: "相关活动与说明", url: "https://www.djinhe.cn/forum.php?mod=forumdisplay&fid=9", badge: "活动" },
  ],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

document.addEventListener("DOMContentLoaded", () => {
  bindUi();
  loadData();
});

function bindUi() {
  bindToolsPanel();
  $("#searchButton").addEventListener("click", () => {
    state.query = $("#searchInput").value.trim();
    renderCurrentView();
  });
  $("#searchInput").addEventListener("input", () => {
    state.query = $("#searchInput").value.trim();
    renderCurrentView();
  });
  $$(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      state.view = button.dataset.view;
      $$(".tab").forEach((item) => item.classList.toggle("active", item === button));
      $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${state.view}`));
      renderCurrentView();
    });
  });
  [
    "recipeTargetQty",
    "recipeConfidence",
    "upgradeFrom",
    "upgradeTo",
    "upgradeConfidence",
    "tradeTargetNet",
    "tradeTaxRate",
    "tradeGross",
  ].forEach((id) => {
    $(`#${id}`).addEventListener("input", renderCurrentView);
  });
  if (location.hash === "#database" || location.hash === "#tools") openToolsPanel();
  window.addEventListener("hashchange", () => {
    if (location.hash === "#database" || location.hash === "#tools") openToolsPanel();
  });
}

function bindToolsPanel() {
  $$("[data-open-tools]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      openToolsPanel(trigger.dataset.toolView || "");
    });
  });
  $("#closeToolsButton").addEventListener("click", () => {
    $("#database").classList.add("is-hidden");
    document.querySelector("#tools").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function openToolsPanel(view = "") {
  $("#database").classList.remove("is-hidden");
  if (view) {
    const tab = $(`.tab[data-view="${view}"]`);
    if (tab) tab.click();
  }
  $("#database").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadData() {
  try {
    const response = await fetch("material-data.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`资料载入失败：${response.status}`);
    state.data = await response.json();
    renderChrome();
    renderCurrentView();
  } catch (error) {
    $("#siteMeta").textContent = `${error.message}。请确认 material-data.json 已和网页文件放在同一目录。`;
  }
}

function renderChrome() {
  const data = state.data;
  const site = data.site || DEFAULT_SITE;
  const meta = site.meta || {};
  const metaExtra = meta.meta || {};
  const counts = data.counts || {};
  $("#heroBadge").textContent = meta.subtitle || meta.badge || "官方资料站";
  $("#heroTitle").textContent = meta.title || "石器时代-精灵召唤";
  $("#heroBody").textContent = meta.body || "经典回合、精灵养成、家族协作与活动公告统一整理。";
  $("#heroAuthor").textContent = `作者：${metaExtra.author || "烈焰部落 - 花儿"}`;
  const download = findSiteItem(site, "游戏下载") || findSiteItem(site, "下载");
  if (download?.url) $("#heroDownloadLink").href = download.url;
  $("#siteMeta").textContent = "仅展示 admin 发布内容，所有详情以论坛原帖为准。";
  const siteStats = (site.stats || []).slice(0, 3).map((item) => [item.title, item.body || item.badge || ""]);
  const stats = [
    ...siteStats,
    ["资料工具", `${fmtQty((counts.materials || 0) + (counts.source_items || 0))} 条数据`],
  ].slice(0, 4);
  $("#summaryStrip").replaceChildren(...stats.map(([label, value]) => {
    const node = document.createElement("div");
    node.className = "summary-card";
    node.append(el("span", label), el("strong", String(value)));
    return node;
  }));
  renderForumBoards(site.boards || DEFAULT_SITE.boards || []);
  renderOfficialCards("#featureGrid", site.features || [], "feature-card");
  renderOfficialLinks(site.links || []);
}

function renderForumBoards(boards) {
  const cards = boards.map((board) => {
    const card = document.createElement("article");
    card.className = "board-card";
    const titleLink = document.createElement("a");
    titleLink.href = board.url || "#home";
    titleLink.target = board.url ? "_blank" : "";
    titleLink.rel = board.url ? "noopener" : "";
    titleLink.append(el("span", board.badge || "板块", "badge"), el("h3", board.title || "未命名板块"));
    const posts = (board.meta?.posts || []).filter((post) => post.author === "admin");
    const list = document.createElement("div");
    list.className = "post-list";
    posts.slice(0, 6).forEach((post) => {
      const link = document.createElement("a");
      link.href = post.url || board.url || "#home";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = post.title || "未命名内容";
      list.append(link);
    });
    if (!posts.length) list.append(el("span", "暂无 admin 发布内容，等待官方补充。", "muted"));
    card.append(titleLink, el("p", board.body || ""), list);
    return card;
  });
  $("#forumBoards").replaceChildren(...(cards.length ? cards : [empty("暂无论坛板块。")]));
}

function renderOfficialCards(selector, items, className) {
  const cards = items.map((item) => {
    const card = document.createElement(item.url ? "a" : "article");
    card.className = className;
    if (item.url) {
      card.href = item.url;
      card.target = "_blank";
      card.rel = "noopener";
    }
    card.append(
      el("span", item.badge || item.subtitle || "官方", "badge"),
      el("h3", item.title || "未命名"),
      el("p", item.body || item.subtitle || ""),
    );
    return card;
  });
  $(selector).replaceChildren(...(cards.length ? cards : [empty("暂无资料。")]));
}

function renderOfficialLinks(items) {
  const cards = items.map((item) => {
    const link = document.createElement("a");
    link.className = "link-card";
    link.href = item.url || "#home";
    link.target = item.url ? "_blank" : "";
    link.rel = item.url ? "noopener" : "";
    link.append(
      el("span", item.badge || "入口", "badge"),
      el("h3", item.title || "入口"),
      el("p", item.body || ""),
    );
    return link;
  });
  $("#officialLinks").replaceChildren(...(cards.length ? cards : [empty("暂无入口。")]));
}

function findSiteItem(site, text) {
  const sections = ["stats", "announcements", "links"];
  for (const section of sections) {
    const item = (site[section] || []).find((row) => `${row.title || ""}${row.badge || ""}`.includes(text));
    if (item) return item;
  }
  return null;
}

function renderCurrentView() {
  if (!state.data) return;
  if (state.view === "materials") renderMaterials();
  if (state.view === "sources") renderSources();
  if (state.view === "recipes") renderRecipes();
  if (state.view === "upgrades") renderUpgrades();
  if (state.view === "trade") renderTradeTool();
}

function renderMaterials() {
  const rows = filterRows(state.data.materials || [], ["name", "category", "source_names", "notes", "price_source"]);
  $("#materialsCount").textContent = `${rows.length} 条`;
  renderTable("#materialsTable", [
    col("name", "材料"),
    col("category", "分类"),
    col("price_diamonds", "钻石", fmtQty),
    col("price_rmb", "RMB", fmtRmb),
    col("source_names", "出处", sourceText, "wrap"),
    col("price_updated_at", "价格更新", formatDate),
    col("notes", "备注", textValue, "wrap"),
  ], rows);
}

function renderSources() {
  const sourceItems = filterRows(state.data.source_items || [], ["item_name", "source_name", "raw_text", "notes"]);
  const groups = new Map();
  for (const row of sourceItems) {
    const name = row.source_name || "未命名出处";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(row);
  }
  $("#sourcesCount").textContent = `${groups.size} 个出处，${sourceItems.length} 条记录`;
  const nodes = Array.from(groups.entries()).sort(([a], [b]) => a.localeCompare(b, "zh-Hans-CN")).map(([name, rows]) => {
    const card = document.createElement("article");
    card.className = "source-card";
    card.append(el("h3", name));
    const chips = document.createElement("div");
    chips.className = "chip-row";
    rows.slice(0, 24).forEach((row) => {
      const qty = row.parsed_quantity ? ` x${fmtQty(row.parsed_quantity)}` : "";
      const chipNode = document.createElement("span");
      chipNode.className = "chip";
      chipNode.textContent = `${row.item_name}${qty}`;
      chips.append(chipNode);
    });
    if (rows.length > 24) chips.append(el("span", `还有 ${rows.length - 24} 条`, "chip"));
    card.append(chips);
    return card;
  });
  $("#sourceGroups").replaceChildren(...(nodes.length ? nodes : [empty("没有匹配的出处。")]));
}

function renderRecipes() {
  const targetQuantity = Math.max(1, Number($("#recipeTargetQty").value || 1));
  const confidence = Number($("#recipeConfidence").value || 0.95);
  const recipes = filterRows(state.data.recipes || [], ["product_name", "category", "recipe_type", "notes"]);
  const cards = recipes.map((recipe) => renderRecipeCard(recipe, targetQuantity, confidence));
  $("#recipeCards").replaceChildren(...(cards.length ? cards : [empty("没有匹配的配方。")]));
}

function renderRecipeCard(recipe, targetQuantity, confidence) {
  const outputQuantity = Math.max(0.000001, Number(recipe.output_quantity || 1));
  const requiredSuccesses = Math.ceil(targetQuantity / outputQuantity);
  const rate = rateValue(recipe.success_rate);
  const standardAttempts = requiredSuccesses;
  const expectedAttempts = requiredSuccesses / rate;
  const safeAttempts = attemptsForConfidence(requiredSuccesses, rate, confidence);
  const materialRows = (recipe.materials || []).map((material) => {
    const base = Number(material.quantity || 0);
    const expectedQty = base * (recipe.failure_consumes_materials ? expectedAttempts : standardAttempts);
    const safeQty = base * (recipe.failure_consumes_materials ? safeAttempts : standardAttempts);
    return { name: material.material_name, standard: base * standardAttempts, expected: expectedQty, safe: safeQty, price: priceOf(material.material_name) };
  });
  const directExpected = Number(recipe.diamond_cost || 0) * (recipe.failure_consumes_diamonds ? expectedAttempts : standardAttempts);
  const directSafe = Number(recipe.diamond_cost || 0) * (recipe.failure_consumes_diamonds ? safeAttempts : standardAttempts);
  const standardTotal = totalCost(materialRows, "standard") + Number(recipe.diamond_cost || 0) * standardAttempts;
  const expectedTotal = totalCost(materialRows, "expected") + directExpected;
  const safeTotal = totalCost(materialRows, "safe") + directSafe;
  return infoCard(
    recipe.product_name,
    `${recipe.category || "未分类"} · ${recipe.recipe_type || "配方"} · 成功率 ${(rate * 100).toFixed(2)}%`,
    [
      ["标准成本", money(standardTotal)],
      ["期望成本", money(expectedTotal)],
      [`${Math.round(confidence * 100)}% 稳妥`, money(safeTotal)],
    ],
    materialRows.map((row) => `${row.name} x${fmtQty(row.safe)}${row.price ? ` · 单价 ${fmtQty(row.price.price_diamonds)}钻` : " · 暂无价格"}`),
  );
}

function renderUpgrades() {
  const fromLevel = Number($("#upgradeFrom").value || 0);
  const toLevel = Number($("#upgradeTo").value || 1);
  const confidence = Number($("#upgradeConfidence").value || 0.95);
  const steps = filterRows(state.data.upgrades || [], ["equipment_name", "notes"]);
  const names = unique(steps.map((step) => step.equipment_name));
  const cards = names.map((name) => renderUpgradeCard(name, fromLevel, toLevel, confidence)).filter(Boolean);
  $("#upgradeCards").replaceChildren(...(cards.length ? cards : [empty("没有匹配的升级资料。")]));
}

function renderUpgradeCard(equipmentName, fromLevel, toLevel, confidence) {
  const steps = (state.data.upgrades || []).filter((step) => step.equipment_name === equipmentName);
  if (!steps.some((step) => Number(step.from_level) >= fromLevel && Number(step.to_level) <= toLevel)) return null;
  const plan = calculateUpgradePlan(equipmentName, fromLevel, toLevel, 1, confidence);
  if (plan.missing.length) {
    return infoCard(
      equipmentName,
      `${fromLevel} -> ${toLevel}`,
      [["缺少资料", plan.missing.join(" / ")]],
      plan.missing.map((item) => `缺少升级步骤：${item}`),
    );
  }
  const comparison = marketComparison(equipmentName, toLevel, plan.costs.safe, 1);
  const routeOptions = buildUpgradeRouteOptions(equipmentName, fromLevel, toLevel, 1, confidence);
  const recommendedRoute = routeOptions[0];
  const materialText = [
    recommendedRoute ? `推荐路线：${recommendedRoute.label} · ${money(recommendedRoute.safeCost)}` : "",
    `结论：自己合成 ${money(plan.costs.safe)}；${comparison}`,
    "最终路线对比",
    ...routeOptions.map((option) => `${option.label}：${money(option.safeCost)}`),
    "底层材料（中间装备已展开）",
    ...plan.materials.map((row) => {
      const price = priceOf(row.name);
      return `${row.name} x${fmtQty(row.safe)}${price ? ` · 单价 ${fmtQty(price.price_diamonds)}钻 · 合计 ${money(row.safe * Number(price.price_diamonds || 0))}` : " · 暂无价格"}`;
    }),
    "材料出处",
    ...plan.materials.map((row) => `${row.name}：${sourcesOf(row.name).slice(0, 8).join(" / ") || "暂无出处资料"}`),
    "算法依据",
    ...(plan.expanded.length ? [`已展开：${Array.from(new Set(plan.expanded)).join(" / ")}`] : ["未发现中间装备。"]),
    ...plan.details.map((detail) => {
      const step = detail.step;
      return `${step.from_level} -> ${step.to_level}：成功率 ${(rateValue(step.success_rate) * 100).toFixed(2)}%，稳妥尝试 ${fmtQty(detail.diamondAttempts.safe)} 次`;
    }),
  ].filter(Boolean);
  return infoCard(
    equipmentName,
    `${fromLevel} -> ${toLevel}，自己合成 vs 直接买`,
    [
      ["标准成本", money(plan.costs.standard)],
      ["期望成本", money(plan.costs.expected)],
      [`${Math.round(confidence * 100)}% 稳妥`, money(plan.costs.safe)],
    ],
    materialText,
  );
}

function buildUpgradeRouteOptions(equipmentName, fromLevel, toLevel, targetQuantity, confidence) {
  const start = Number(fromLevel);
  const end = Number(toLevel);
  const selfPlan = calculateUpgradePlan(equipmentName, start, end, targetQuantity, confidence);
  if (selfPlan.missing.length) return [];
  const options = [{
    label: `从 ${start} 级一路自己合到 ${end} 级`,
    safeCost: selfPlan.costs.safe,
  }];
  for (let level = start + 1; level <= end; level += 1) {
    const market = marketPriceForLevel(equipmentName, level);
    if (!market) continue;
    if (level === end) {
      options.push({
        label: `直接买 ${market.name}`,
        safeCost: Number(market.price_diamonds || 0) * targetQuantity,
      });
      continue;
    }
    const routePlan = calculateUpgradePlan(equipmentName, level, end, targetQuantity, confidence, level);
    if (routePlan.missing.length) continue;
    let safeCost = routePlan.costs.safe;
    if (!routePlan.materials.some((row) => normalize(row.name) === normalize(market.name))) {
      safeCost += Number(market.price_diamonds || 0) * targetQuantity;
    }
    options.push({
      label: `买 ${market.name} 后升到 ${end} 级`,
      safeCost,
    });
  }
  return options.sort((a, b) => a.safeCost - b.safeCost);
}

function renderTradeTool() {
  const ratePercent = Number($("#tradeTaxRate").value || 0);
  const rate = Math.max(0, Math.min(99, ratePercent)) / 100;
  const targetNet = Math.max(0, Number($("#tradeTargetNet").value || 0));
  const gross = Math.max(0, Number($("#tradeGross").value || 0));
  const requiredGross = requiredTradeGrossForNet(targetNet, rate);
  const guaranteedNet = netAfterTradeTax(requiredGross, rate);
  const requiredTax = tradeTaxAmount(requiredGross, rate);
  const actualNet = netAfterTradeTax(gross, rate);
  const actualTax = tradeTaxAmount(gross, rate);
  $("#tradeRequiredGross").textContent = `${fmtQty(requiredGross)} 钻`;
  $("#tradeRequiredTax").textContent = `${fmtQty(requiredTax)} 钻`;
  $("#tradeGuaranteedNet").textContent = `${fmtQty(guaranteedNet)} 钻`;
  $("#tradeActualNet").textContent = `${fmtQty(actualNet)} 钻`;
  $("#tradeActualTax").textContent = `${fmtQty(actualTax)} 钻`;
}

function renderTable(selector, columns, rows) {
  const table = $(selector);
  table.replaceChildren();
  const thead = document.createElement("thead");
  const head = document.createElement("tr");
  columns.forEach((column) => head.append(el("th", column.label)));
  thead.append(head);
  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = Math.max(1, columns.length);
    td.className = "wrap";
    td.textContent = "没有匹配的数据。";
    tr.append(td);
    tbody.append(tr);
    table.append(thead, tbody);
    return;
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    columns.forEach((column) => {
      const td = document.createElement("td");
      if (column.className) td.className = column.className;
      td.textContent = column.format(row[column.key], row);
      tr.append(td);
    });
    tbody.append(tr);
  });
  table.append(thead, tbody);
}

function infoCard(title, subtitle, metrics, materials) {
  const card = document.createElement("article");
  card.className = "info-card";
  card.append(el("h3", title), el("p", subtitle, "muted"));
  const metricGrid = document.createElement("div");
  metricGrid.className = "metric-grid";
  metrics.forEach(([label, value]) => {
    const metric = document.createElement("div");
    metric.className = "metric";
    metric.append(el("span", label), el("strong", value));
    metricGrid.append(metric);
  });
  card.append(metricGrid);
  const list = document.createElement("ul");
  list.className = "materials-list";
  materials.slice(0, 16).forEach((item) => {
    const li = document.createElement("li");
    li.append(el("span", item));
    list.append(li);
  });
  if (materials.length > 16) list.append(el("li", `还有 ${materials.length - 16} 项材料`));
  card.append(list);
  return card;
}

function filterRows(rows, keys) {
  const query = normalize(state.query);
  if (!query) return rows;
  return rows.filter((row) => keys.some((key) => normalize(row[key]).includes(query)));
}

function priceOf(name) {
  const normalized = normalize(name);
  return (state.data.materials || []).find((row) => normalize(row.name) === normalized && row.price_diamonds !== null && row.price_diamonds !== undefined);
}

function totalCost(rows, field) {
  return rows.reduce((total, row) => {
    const price = row.price ? Number(row.price.price_diamonds || 0) : 0;
    return total + Number(row[field] || 0) * price;
  }, 0);
}

function costMap(map) {
  let total = 0;
  for (const [name, qty] of map.entries()) {
    const price = priceOf(name);
    total += qty * (price ? Number(price.price_diamonds || 0) : 0);
  }
  return total;
}

function calculateUpgradePlan(equipmentName, fromLevel, toLevel, targetQuantity, confidence, baseLevel = fromLevel) {
  const start = Number(fromLevel);
  const baseLevelNumber = Number(baseLevel);
  const end = Number(toLevel);
  const stepsByFrom = new Map();
  const missing = [];
  for (const step of state.data.upgrades || []) {
    if (step.equipment_name === equipmentName) stepsByFrom.set(Number(step.from_level), step);
  }
  for (let level = baseLevelNumber; level < end; level += 1) {
    if (!stepsByFrom.has(level)) missing.push(`${level} -> ${level + 1}`);
  }
  if (missing.length) return { missing };

  const aggregate = new Map();
  const direct = { standard: 0, expected: 0, safe: 0 };
  const detailByLevel = new Map();
  const expanded = [];

  function addMaterial(name, quantities) {
    if (!name) return;
    const key = normalize(name);
    if (!aggregate.has(key)) aggregate.set(key, { name, standard: 0, expected: 0, safe: 0 });
    const row = aggregate.get(key);
    row.standard += Number(quantities.standard || 0);
    row.expected += Number(quantities.expected || 0);
    row.safe += Number(quantities.safe || 0);
  }

  function recordStep(level, step, successes, materialAttempts, diamondAttempts) {
    if (!detailByLevel.has(level)) {
      detailByLevel.set(level, {
        step,
        successes: { standard: 0, expected: 0, safe: 0 },
        materialAttempts: { standard: 0, expected: 0, safe: 0 },
        diamondAttempts: { standard: 0, expected: 0, safe: 0 },
      });
    }
    const detail = detailByLevel.get(level);
    for (const key of ["standard", "expected", "safe"]) {
      detail.successes[key] += successes[key];
      detail.materialAttempts[key] += materialAttempts[key];
      detail.diamondAttempts[key] += diamondAttempts[key];
    }
  }

  function expandLevel(level, successes, stack = [], countBase = false) {
    if (level <= baseLevelNumber) {
      if (countBase) addMaterial(equipmentItemName(equipmentName, baseLevelNumber), successes);
      return;
    }
    if (stack.includes(level)) throw new Error(`升级材料存在循环引用：${equipmentName}${level}`);
    const step = stepsByFrom.get(level - 1);
    const rate = rateValue(step.success_rate);
    let materialAttempts = {
      standard: successes.standard,
      expected: successes.expected / rate,
      safe: attemptsForConfidence(ceilQty(successes.safe), rate, confidence),
    };
    let diamondAttempts = { ...materialAttempts };
    if (!step.failure_consumes_materials) materialAttempts = { ...successes };
    if (!step.failure_consumes_diamonds) diamondAttempts = { ...successes };
    recordStep(level - 1, step, successes, materialAttempts, diamondAttempts);
    for (const key of ["standard", "expected", "safe"]) {
      direct[key] += Number(step.diamond_cost || 0) * Number(diamondAttempts[key] || 0);
    }
    let hasExplicitPreviousEquipment = false;
    for (const material of step.materials || []) {
      const name = material.material_name || "";
      const qty = Number(material.quantity || 0);
      const required = {
        standard: qty * materialAttempts.standard,
        expected: qty * materialAttempts.expected,
        safe: qty * materialAttempts.safe,
      };
      const materialLevel = sameEquipmentLevel(name, equipmentName);
      if (materialLevel === level - 1) hasExplicitPreviousEquipment = true;
      if (materialLevel !== null && baseLevelNumber < materialLevel && materialLevel < level) {
        expanded.push(name);
        expandLevel(materialLevel, required, [...stack, level], true);
      } else {
        addMaterial(name, required);
      }
    }
    if (!hasExplicitPreviousEquipment) expandLevel(level - 1, successes, [...stack, level], false);
  }

  expandLevel(end, { standard: targetQuantity, expected: targetQuantity, safe: targetQuantity });
  const materials = Array.from(aggregate.values()).sort((a, b) => normalize(a.name).localeCompare(normalize(b.name), "zh-Hans-CN"))
    .map((row) => ({
      ...row,
      standard: ceilQty(row.standard),
      expected: ceilQty(row.expected),
      safe: ceilQty(row.safe),
    }));
  const costs = {
    standard: direct.standard + totalMaterialCost(materials, "standard"),
    expected: direct.expected + totalMaterialCost(materials, "expected"),
    safe: direct.safe + totalMaterialCost(materials, "safe"),
  };
  return {
    missing: [],
    materials,
    costs,
    direct,
    expanded,
    details: Array.from(detailByLevel.entries()).sort(([a], [b]) => a - b).map(([, detail]) => detail),
  };
}

function totalMaterialCost(rows, field) {
  return rows.reduce((total, row) => {
    const price = priceOf(row.name);
    return total + Number(row[field] || 0) * (price ? Number(price.price_diamonds || 0) : 0);
  }, 0);
}

function marketComparison(equipmentName, level, buildCost, quantity) {
  const price = marketPriceForLevel(equipmentName, level);
  if (!price) return `直接买 ${equipmentItemName(equipmentName, level)}：暂无市场价`;
  const marketTotal = Number(price.price_diamonds || 0) * Number(quantity || 1);
  const diff = marketTotal - buildCost;
  if (diff > 0) return `直接买 ${price.name} ${money(marketTotal)}；自己合成更省 ${money(diff)}`;
  if (diff < 0) return `直接买 ${price.name} ${money(marketTotal)}；直接买更省 ${money(Math.abs(diff))}`;
  return `直接买 ${price.name} ${money(marketTotal)}；两者持平`;
}

function marketPriceForLevel(equipmentName, level) {
  const base = String(equipmentName || "").trim();
  const candidates = [`${base}${level}`, `${base}${level}级`, `${base} ${level}`, `${base}Lv${level}`, `${base}LV${level}`];
  for (const candidate of candidates) {
    const price = priceOf(candidate);
    if (price) return { ...price, name: candidate };
  }
  return null;
}

function sameEquipmentLevel(itemName, equipmentName) {
  const item = normalizeCompact(itemName);
  const equipment = normalizeCompact(equipmentName);
  if (!item || !equipment || !item.startsWith(equipment)) return null;
  const suffix = item.slice(equipment.length);
  const match = suffix.match(/^(?:lv|level|l)?(\d+)(?:级)?$/i);
  return match ? Number(match[1]) : null;
}

function equipmentItemName(equipmentName, level) {
  return `${String(equipmentName || "").trim()}${Number(level)}`;
}

function sourcesOf(name) {
  const row = (state.data.materials || []).find((item) => normalize(item.name) === normalize(name));
  return String(row?.source_names || "").split(",").filter(Boolean);
}

function ceilQty(value) {
  return Math.ceil(Math.max(0, Number(value || 0)) - 1e-12);
}

function netAfterTradeTax(grossDiamonds, taxRate) {
  return Math.floor(Math.max(0, Number(grossDiamonds || 0)) * (1 - taxRate));
}

function requiredTradeGrossForNet(netDiamonds, taxRate) {
  const net = Math.max(0, Number(netDiamonds || 0));
  if (net <= 0) return 0;
  return Math.ceil(net / (1 - taxRate));
}

function tradeTaxAmount(grossDiamonds, taxRate) {
  const gross = Math.ceil(Math.max(0, Number(grossDiamonds || 0)));
  return gross - netAfterTradeTax(gross, taxRate);
}

function attemptsForConfidence(successes, successRate, confidence) {
  const k = Math.ceil(successes);
  const p = Number(successRate);
  let conf = Number(confidence);
  if (k <= 0) return 0;
  if (p <= 0) return k;
  if (p >= 1) return k;
  if (conf >= 1) conf = 0.999999;
  const start = Math.max(k, Math.ceil(k / p));
  const cap = Math.min(100000, Math.max(start + 1000, Math.floor(start * 20 + 100)));
  for (let n = start; n <= cap; n += 1) {
    if (binomialTailAtLeast(n, k, p) >= conf) return n;
  }
  return cap;
}

function binomialTailAtLeast(n, k, p) {
  if (k <= 0) return 1;
  if (k > n) return 0;
  if (p <= 0) return 0;
  if (p >= 1) return 1;
  const q = 1 - p;
  let prob = q ** n;
  let cdf = prob;
  for (let i = 0; i < k - 1; i += 1) {
    prob = q === 0 ? 0 : prob * (n - i) / (i + 1) * p / q;
    cdf += prob;
    if (cdf >= 1) return 0;
  }
  return Math.max(0, Math.min(1, 1 - cdf));
}

function addMap(map, key, value) {
  map.set(key, (map.get(key) || 0) + value);
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function rateValue(value) {
  const rate = Number(value || 1);
  if (rate > 1) return rate / 100;
  return Math.max(0.000001, rate);
}

function normalize(value) {
  return String(value || "").trim().toLowerCase();
}

function normalizeCompact(value) {
  return normalize(value).replace(/\s+/g, "");
}

function col(key, label, format = textValue, className = "") {
  return { key, label, format, className };
}

function el(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function empty(text) {
  return el("div", text, "empty");
}

function textValue(value) {
  return value === null || value === undefined ? "" : String(value);
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

function money(diamonds) {
  return `${fmtQty(diamonds)}钻 / ${fmtRmb(Number(diamonds || 0) / Number(state.data.diamond_per_rmb || 500))} RMB`;
}

function sourceText(value) {
  return String(value || "").split(",").filter(Boolean).join(" / ");
}

function formatDate(value) {
  if (!value) return "";
  return String(value).replace("T", " ").slice(0, 16);
}
