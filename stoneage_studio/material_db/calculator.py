from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from .database import MaterialDatabase
from .normalizer import normalize_item_name


def ceil_quantity(value: float) -> int:
    return int(math.ceil(max(0.0, float(value)) - 1e-12))


def attempts_for_confidence(successes: int, success_rate: float, confidence: float, *, max_attempts: int = 100000) -> int:
    k = int(math.ceil(successes))
    p = float(success_rate)
    conf = float(confidence)
    if k <= 0:
        return 0
    if p <= 0:
        raise ValueError("成功率必须大于 0")
    if p >= 1:
        return k
    if conf <= 0:
        return k
    if conf >= 1:
        conf = 0.999999
    start = max(k, int(math.ceil(k / p)))
    cap = min(max_attempts, max(start + 1000, int(start * 20 + 100)))
    for n in range(start, cap + 1):
        if _binomial_tail_at_least(n, k, p) >= conf:
            return n
    return cap


def _binomial_tail_at_least(n: int, k: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    q = 1.0 - p
    # Sum P(X < k). This recurrence avoids scipy and keeps small cases exact.
    try:
        prob = q**n
    except OverflowError:
        prob = 0.0
    cdf = prob
    for i in range(0, k - 1):
        if q == 0:
            prob = 0.0
        else:
            prob = prob * (n - i) / (i + 1) * p / q
        cdf += prob
        if cdf >= 1.0:
            return 0.0
    return max(0.0, min(1.0, 1.0 - cdf))


class MaterialCalculator:
    def __init__(self, db: MaterialDatabase) -> None:
        self.db = db

    def recipe_cost(self, product_name: str, *, target_quantity: int = 1, confidence: float = 0.95) -> dict[str, Any]:
        recipe = self.db.find_recipe(product_name)
        if not recipe:
            raise ValueError(f"没有找到成品配方：{product_name}")
        target = max(1, int(target_quantity or 1))
        output_quantity = max(0.000001, float(recipe.get("output_quantity") or 1))
        required_successes = int(math.ceil(target / output_quantity))
        p = _rate(recipe.get("success_rate"))
        standard_attempts = required_successes
        expected_attempts = required_successes / p
        safe_attempts = attempts_for_confidence(required_successes, p, confidence)
        product_price = self.db.get_price(str(recipe.get("product_name") or product_name))
        material_rows = []
        costs = _empty_costs()
        for material in recipe.get("materials") or []:
            base_qty = float(material.get("quantity") or 0) * required_successes
            expected_qty = float(material.get("quantity") or 0) * (
                expected_attempts if recipe.get("failure_consumes_materials", 1) else standard_attempts
            )
            safe_qty = float(material.get("quantity") or 0) * (
                safe_attempts if recipe.get("failure_consumes_materials", 1) else standard_attempts
            )
            material_name = str(material.get("material_name") or "")
            price = self.db.get_price(material_name)
            sources = self.db.search_sources(material_name, limit=50)
            row = _material_result(
                self.db,
                material_name,
                base_qty,
                expected_qty,
                safe_qty,
                price,
                sources,
                material.get("notes") or "",
            )
            _add_costs(costs, row)
            material_rows.append(row)
        standard_direct = float(recipe.get("diamond_cost") or 0) * standard_attempts
        expected_direct = float(recipe.get("diamond_cost") or 0) * (
            expected_attempts if recipe.get("failure_consumes_diamonds", 1) else standard_attempts
        )
        safe_direct = float(recipe.get("diamond_cost") or 0) * (
            safe_attempts if recipe.get("failure_consumes_diamonds", 1) else standard_attempts
        )
        for key, value in (("standard", standard_direct), ("expected", expected_direct), ("safe", safe_direct)):
            costs[key]["direct_diamonds"] = value
            costs[key]["total_diamonds"] = costs[key]["material_diamonds"] + value
            costs[key]["direct_rmb"] = self.db.diamonds_to_rmb(value)
            costs[key]["total_rmb"] = self.db.diamonds_to_rmb(costs[key]["total_diamonds"])
        product_value = float(product_price["price_diamonds"]) * target if product_price else None
        profit = {
            key: (product_value - costs[key]["total_diamonds"] if product_value is not None else None)
            for key in ("standard", "expected", "safe")
        }
        result = {
            "kind": "recipe",
            "recipe": recipe,
            "target_quantity": target,
            "required_successes": required_successes,
            "success_rate": p,
            "confidence": confidence,
            "attempts": {"standard": standard_attempts, "expected": expected_attempts, "safe": safe_attempts},
            "materials": material_rows,
            "costs": costs,
            "product_price": product_price,
            "product_value_diamonds": product_value,
            "profit_diamonds": profit,
            "diamond_per_rmb": self.db.diamond_per_rmb(),
        }
        result["text"] = self.format_recipe_result(result)
        return result

    def upgrade_cost(
        self,
        equipment_name: str,
        from_level: int,
        to_level: int,
        *,
        target_quantity: int = 1,
        confidence: float = 0.95,
    ) -> dict[str, Any]:
        start = int(from_level)
        end = int(to_level)
        target = max(1, int(target_quantity or 1))
        if end <= start:
            raise ValueError("目标等级必须大于起始等级")
        steps_by_from: dict[int, dict[str, Any]] = {}
        missing: list[str] = []
        for level in range(start, end):
            step = self.db.find_upgrade_step(equipment_name, level, level + 1)
            if not step:
                missing.append(f"{level} -> {level + 1}")
            else:
                steps_by_from[level] = step
        if missing:
            return {
                "kind": "upgrade",
                "equipment_name": equipment_name,
                "from_level": start,
                "to_level": end,
                "target_quantity": target,
                "missing_steps": missing,
                "steps": list(steps_by_from.values()),
                "text": "缺少升级资料：\n" + "\n".join(f"- {item}" for item in missing),
            }

        def calculate_plan(target_level: int, quantity: int, *, base_level: int = start) -> dict[str, Any]:
            aggregate: dict[str, dict[str, Any]] = {}
            direct = {"standard": 0.0, "expected": 0.0, "safe": 0.0}
            detail_by_level: dict[int, dict[str, Any]] = {}
            expanded_intermediates: list[dict[str, Any]] = []
            downgrade_warning = False

            def add_material(name: str, quantities: dict[str, float]) -> None:
                display_name = str(name or "").strip()
                if not display_name:
                    return
                normalized = normalize_item_name(display_name)
                if normalized not in aggregate:
                    aggregate[normalized] = {
                        "material_name": display_name,
                        "standard": 0.0,
                        "expected": 0.0,
                        "safe": 0.0,
                    }
                for key in ("standard", "expected", "safe"):
                    aggregate[normalized][key] += float(quantities.get(key) or 0)

            def attempts_for_successes(successes: dict[str, float], step: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
                p = _rate(step.get("success_rate"))
                material_attempts = {
                    "standard": float(successes["standard"]),
                    "expected": float(successes["expected"]) / p,
                    "safe": float(attempts_for_confidence(ceil_quantity(successes["safe"]), p, confidence)),
                }
                diamond_attempts = dict(material_attempts)
                if not step.get("failure_consumes_materials", 1):
                    material_attempts = {key: float(successes[key]) for key in ("standard", "expected", "safe")}
                if not step.get("failure_consumes_diamonds", 1):
                    diamond_attempts = {key: float(successes[key]) for key in ("standard", "expected", "safe")}
                return material_attempts, diamond_attempts

            def record_step(level: int, step: dict[str, Any], successes: dict[str, float], material_attempts: dict[str, float], diamond_attempts: dict[str, float]) -> None:
                row = detail_by_level.setdefault(
                    level,
                    {
                        "step": step,
                        "successes": {"standard": 0.0, "expected": 0.0, "safe": 0.0},
                        "material_attempts": {"standard": 0.0, "expected": 0.0, "safe": 0.0},
                        "diamond_attempts": {"standard": 0.0, "expected": 0.0, "safe": 0.0},
                    },
                )
                for key in ("standard", "expected", "safe"):
                    row["successes"][key] += float(successes[key])
                    row["material_attempts"][key] += float(material_attempts[key])
                    row["diamond_attempts"][key] += float(diamond_attempts[key])

            def expand_level(level: int, successes: dict[str, float], stack: tuple[int, ...] = (), *, count_base: bool = False) -> None:
                nonlocal downgrade_warning
                if level <= base_level:
                    if count_base:
                        add_material(_equipment_item_name(equipment_name, base_level), successes)
                    return
                if level in stack:
                    raise ValueError(f"升级材料存在循环引用：{equipment_name}{level}")
                step = steps_by_from[level - 1]
                if step.get("failure_downgrades_level"):
                    downgrade_warning = True
                material_attempts, diamond_attempts = attempts_for_successes(successes, step)
                record_step(level - 1, step, successes, material_attempts, diamond_attempts)
                for key in direct:
                    direct[key] += float(step.get("diamond_cost") or 0) * float(diamond_attempts[key])
                has_explicit_previous_equipment = False
                for material in step.get("materials") or []:
                    material_name = str(material.get("material_name") or "").strip()
                    qty = float(material.get("quantity") or 0)
                    required = {key: qty * float(material_attempts[key]) for key in ("standard", "expected", "safe")}
                    material_level = _same_equipment_level(material_name, equipment_name)
                    if material_level == level - 1:
                        has_explicit_previous_equipment = True
                    if material_level is not None and base_level < material_level < level:
                        expanded_intermediates.append(
                            {
                                "material_name": material_name,
                                "material_level": material_level,
                                "parent_level": level,
                                "quantities": dict(required),
                            }
                        )
                        expand_level(material_level, required, (*stack, level), count_base=True)
                    else:
                        add_material(material_name, required)
                if not has_explicit_previous_equipment:
                    expand_level(level - 1, successes, (*stack, level), count_base=False)

            expand_level(target_level, {"standard": float(quantity), "expected": float(quantity), "safe": float(quantity)})
            return {
                "aggregate": aggregate,
                "direct": direct,
                "steps": [detail_by_level[level] for level in sorted(detail_by_level)],
                "expanded_intermediates": expanded_intermediates,
                "downgrade_warning": downgrade_warning,
            }

        def costs_for_plan(plan_data: dict[str, Any]) -> dict[str, dict[str, float]]:
            plan_costs = _empty_costs()
            for plan_item in plan_data["aggregate"].values():
                price = self.db.get_price(str(plan_item["material_name"]))
                row = _material_result(
                    self.db,
                    str(plan_item["material_name"]),
                    float(plan_item["standard"]),
                    float(plan_item["expected"]),
                    float(plan_item["safe"]),
                    price,
                    [],
                    "",
                )
                _add_costs(plan_costs, row)
            for cost_key in plan_data["direct"]:
                plan_costs[cost_key]["direct_diamonds"] = plan_data["direct"][cost_key]
                plan_costs[cost_key]["total_diamonds"] = plan_costs[cost_key]["material_diamonds"] + plan_data["direct"][cost_key]
                plan_costs[cost_key]["direct_rmb"] = self.db.diamonds_to_rmb(plan_data["direct"][cost_key])
                plan_costs[cost_key]["total_rmb"] = self.db.diamonds_to_rmb(plan_costs[cost_key]["total_diamonds"])
            return plan_costs

        plan = calculate_plan(end, target)

        material_rows = []
        costs = _empty_costs()
        for item in sorted(plan["aggregate"].values(), key=lambda row: normalize_item_name(row["material_name"])):
            name = str(item["material_name"])
            quantities = item
            price = self.db.get_price(name)
            sources = self.db.search_sources(name, limit=50)
            row = _material_result(
                self.db,
                name,
                quantities["standard"],
                quantities["expected"],
                quantities["safe"],
                price,
                sources,
                "",
            )
            _add_costs(costs, row)
            material_rows.append(row)
        for key in plan["direct"]:
            costs[key]["direct_diamonds"] = plan["direct"][key]
            costs[key]["total_diamonds"] = costs[key]["material_diamonds"] + plan["direct"][key]
            costs[key]["direct_rmb"] = self.db.diamonds_to_rmb(plan["direct"][key])
            costs[key]["total_rmb"] = self.db.diamonds_to_rmb(costs[key]["total_diamonds"])

        market_comparisons = []
        for level in range(start + 1, end + 1):
            level_plan = calculate_plan(level, target)
            level_costs = costs_for_plan(level_plan)
            market_name, market_price = _market_price_for_level(self.db, equipment_name, level)
            market_total = float(market_price["price_diamonds"]) * target if market_price else None
            market_comparisons.append(
                {
                    "level": level,
                    "item_name": market_name,
                    "market_price": market_price,
                    "market_total_diamonds": market_total,
                    "market_total_rmb": self.db.diamonds_to_rmb(market_total) if market_total is not None else None,
                    "build_costs": level_costs,
                    "difference_diamonds": {
                        key: (market_total - level_costs[key]["total_diamonds"] if market_total is not None else None)
                        for key in ("standard", "expected", "safe")
                    },
                }
            )
        route_options = [
            {
                "kind": "self_build",
                "entry_level": start,
                "label": f"从 {start} 级一路自己合到 {end} 级",
                "costs": costs,
                "market_item_name": None,
                "market_total_diamonds": None,
            }
        ]
        for level in range(start + 1, end + 1):
            market_name, market_price = _market_price_for_level(self.db, equipment_name, level)
            if not market_price:
                continue
            market_total = float(market_price["price_diamonds"]) * target
            if level == end:
                route_costs = _empty_costs()
                for key in route_costs:
                    route_costs[key]["material_diamonds"] = market_total
                    route_costs[key]["material_rmb"] = self.db.diamonds_to_rmb(market_total)
                    route_costs[key]["total_diamonds"] = market_total
                    route_costs[key]["total_rmb"] = self.db.diamonds_to_rmb(market_total)
                label = f"直接买 {market_name}"
            else:
                route_plan = calculate_plan(end, target, base_level=level)
                route_costs = costs_for_plan(route_plan)
                if normalize_item_name(market_name) not in route_plan["aggregate"]:
                    for key in route_costs:
                        route_costs[key]["material_diamonds"] += market_total
                        route_costs[key]["material_rmb"] = self.db.diamonds_to_rmb(route_costs[key]["material_diamonds"])
                        route_costs[key]["total_diamonds"] += market_total
                        route_costs[key]["total_rmb"] = self.db.diamonds_to_rmb(route_costs[key]["total_diamonds"])
                label = f"买 {market_name} 后升到 {end} 级"
            route_options.append(
                {
                    "kind": "market_entry",
                    "entry_level": level,
                    "label": label,
                    "costs": route_costs,
                    "market_item_name": market_name,
                    "market_total_diamonds": market_total,
                }
            )
        recommended_route = min(route_options, key=lambda row: float(row["costs"]["safe"]["total_diamonds"]))
        result = {
            "kind": "upgrade",
            "equipment_name": equipment_name,
            "from_level": start,
            "to_level": end,
            "target_quantity": target,
            "confidence": confidence,
            "steps": plan["steps"],
            "expanded_intermediates": plan["expanded_intermediates"],
            "materials": material_rows,
            "costs": costs,
            "market_comparisons": market_comparisons,
            "route_options": route_options,
            "recommended_route": recommended_route,
            "diamond_per_rmb": self.db.diamond_per_rmb(),
            "downgrade_warning": plan["downgrade_warning"],
        }
        result["text"] = self.format_upgrade_result(result)
        return result

    def format_recipe_result(self, result: dict[str, Any]) -> str:
        recipe = result["recipe"]
        confidence_label = f"{int(result['confidence'] * 100)}% 稳妥"
        product_price = result.get("product_price")
        lines = [
            f"成品：{recipe.get('product_name')}",
            f"目标数量：{result['target_quantity']}",
            f"成功率：{result['success_rate'] * 100:.2f}%",
            f"产出数量：{_fmt_qty(recipe.get('output_quantity'))}",
            f"钻石比例：{_fmt_qty(result['diamond_per_rmb'])}钻 = 1 RMB",
            "",
            "成品市场价：",
        ]
        if product_price:
            value = float(product_price["price_diamonds"]) * int(result["target_quantity"])
            lines.append(f"- {_fmt_money(value, self.db)}")
        else:
            lines.append("- 暂无价格")
        for key, label in (("standard", "标准材料"), ("expected", "期望准备"), ("safe", confidence_label + "准备")):
            lines.extend(["", f"{label}："])
            for row in result["materials"]:
                qty = row[f"{key}_quantity"]
                price_text = _price_text(row, qty, self.db)
                source_text = " / ".join(row["sources"][:8]) if row["sources"] else "暂无出处资料"
                lines.append(f"- {row['material_name']} x{_fmt_qty(qty)}，{price_text}，出处：{source_text}")
        for key, label in (("standard", "标准成本"), ("expected", "期望成本"), ("safe", confidence_label + "成本")):
            cost = result["costs"][key]
            lines.extend(
                [
                    "",
                    f"{label}：",
                    f"- 材料成本：{_fmt_money(cost['material_diamonds'], self.db)}",
                    f"- 直接钻石消耗：{_fmt_money(cost['direct_diamonds'], self.db)}",
                    f"- 总成本：{_fmt_money(cost['total_diamonds'], self.db)}",
                ]
            )
        lines.append("")
        lines.append("盈亏：")
        if product_price:
            lines.append(f"- 成品市场价：{_fmt_money(result['product_value_diamonds'], self.db)}")
            for key, label in (("standard", "标准盈亏"), ("expected", "期望盈亏"), ("safe", confidence_label + "盈亏")):
                lines.append(f"- {label}：{_fmt_money(result['profit_diamonds'][key], self.db)}")
        else:
            lines.append("- 暂无成品价格，无法计算盈亏")
        return "\n".join(lines)

    def format_upgrade_result(self, result: dict[str, Any]) -> str:
        confidence_label = f"{int(result['confidence'] * 100)}% 稳妥"
        recommended = result.get("recommended_route") or {}
        lines = [
            f"装备：{result['equipment_name']}",
            f"目标：从 {result['from_level']} 级合到 {result['to_level']} 级，数量 {result.get('target_quantity', 1)}",
            f"钻石比例：{_fmt_qty(result['diamond_per_rmb'])}钻 = 1 RMB",
            "",
            "结论：",
            f"- 自己合成：标准 {_fmt_money(result['costs']['standard']['total_diamonds'], self.db)}；"
            f"期望 {_fmt_money(result['costs']['expected']['total_diamonds'], self.db)}；"
            f"{confidence_label} {_fmt_money(result['costs']['safe']['total_diamonds'], self.db)}",
        ]
        if recommended:
            recommended_cost = float(recommended["costs"]["safe"]["total_diamonds"])
            self_cost = float(result["costs"]["safe"]["total_diamonds"])
            if recommended.get("kind") == "self_build":
                lines.append(f"- 推荐路线：{recommended['label']}，{confidence_label}成本 {_fmt_money(recommended_cost, self.db)}")
            else:
                saved = max(0.0, self_cost - recommended_cost)
                lines.append(
                    f"- 推荐路线：{recommended['label']}，{confidence_label}成本 {_fmt_money(recommended_cost, self.db)}；"
                    f"比全程自己合省 {_fmt_money(saved, self.db)}"
                )
        if result.get("downgrade_warning"):
            lines.append("备注：存在失败降级字段；第一版暂未实现失败降级计算。")

        lines.extend(["", "最终路线对比："])
        for option in sorted(result.get("route_options") or [], key=lambda row: float(row["costs"]["safe"]["total_diamonds"])):
            cost = option["costs"]["safe"]["total_diamonds"]
            lines.append(f"- {option['label']}：{confidence_label}成本 {_fmt_money(cost, self.db)}")

        lines.extend(["", "单独买到某级对比："])
        for comparison in result.get("market_comparisons") or []:
            lines.append("- " + _comparison_text(comparison, "safe", confidence_label, self.db, prefix=f"到 {comparison['level']} 级"))

        missing_prices = [row["material_name"] for row in result["materials"] if not row.get("has_price")]
        lines.extend(["", "底层材料清单（中间装备已自动展开，不再把 2/3/4 级当成买入材料）："])
        for row in result["materials"]:
            price_text = _price_text(row, row["safe_quantity"], self.db)
            lines.append(
                f"- {row['material_name']}：标准 x{_fmt_qty(row['standard_quantity'])}；"
                f"期望 x{_fmt_qty(row['expected_quantity'])}；{confidence_label} x{_fmt_qty(row['safe_quantity'])}；"
                f"{price_text}"
            )
        cost = result["costs"]["safe"]
        lines.extend(
            [
                f"- 直接钻石消耗：{_fmt_money(cost['direct_diamonds'], self.db)}",
                f"- {confidence_label}可计价总成本：{_fmt_money(cost['total_diamonds'], self.db)}",
            ]
        )
        if missing_prices:
            lines.append(f"- 暂无价格，未计入总成本：{' / '.join(missing_prices)}")

        lines.extend(["", "材料出处："])
        for row in result["materials"]:
            source_text = " / ".join(row["sources"][:10]) if row["sources"] else "暂无出处资料"
            lines.append(f"- {row['material_name']}：{source_text}")

        lines.extend(["", "算法依据："])
        if result.get("expanded_intermediates"):
            expanded = sorted({str(row["material_name"]) for row in result["expanded_intermediates"]})
            lines.append(f"- 已展开中间装备：{' / '.join(expanded)}")
        else:
            lines.append("- 未发现需要展开的中间装备。")
        for detail in result["steps"]:
            step = detail["step"]
            lines.append(
                f"- {step.get('from_level')} -> {step.get('to_level')}："
                f"成功率 {_rate(step.get('success_rate')) * 100:.2f}%，"
                f"标准需成功 {_fmt_qty(detail['successes']['standard'])} 次，"
                f"期望尝试 {_fmt_qty(detail['diamond_attempts']['expected'])} 次，"
                f"{confidence_label}尝试 {_fmt_qty(detail['diamond_attempts']['safe'])} 次"
            )
        return "\n".join(lines)


def _material_result(
    db: MaterialDatabase,
    name: str,
    standard_qty: float,
    expected_qty: float,
    safe_qty: float,
    price: dict[str, Any] | None,
    sources: list[dict[str, Any]],
    notes: str,
) -> dict[str, Any]:
    unit_price = float(price["price_diamonds"]) if price else None
    unique_sources = []
    seen = set()
    for source in sources:
        source_name = str(source.get("source_name") or "")
        if source_name and source_name not in seen:
            unique_sources.append(source_name)
            seen.add(source_name)
    row = {
        "material_name": name,
        "standard_quantity": ceil_quantity(standard_qty),
        "expected_quantity": ceil_quantity(expected_qty),
        "safe_quantity": ceil_quantity(safe_qty),
        "unit_price_diamonds": unit_price,
        "unit_price_rmb": db.diamonds_to_rmb(unit_price) if unit_price is not None else None,
        "has_price": unit_price is not None,
        "sources": unique_sources,
        "has_sources": bool(unique_sources),
        "notes": notes,
    }
    for key in ("standard", "expected", "safe"):
        qty = float(row[f"{key}_quantity"])
        total = qty * unit_price if unit_price is not None else 0.0
        row[f"{key}_total_diamonds"] = total
        row[f"{key}_total_rmb"] = db.diamonds_to_rmb(total)
    return row


def _empty_costs() -> dict[str, dict[str, float]]:
    return {
        key: {
            "material_diamonds": 0.0,
            "material_rmb": 0.0,
            "direct_diamonds": 0.0,
            "direct_rmb": 0.0,
            "total_diamonds": 0.0,
            "total_rmb": 0.0,
        }
        for key in ("standard", "expected", "safe")
    }


def _add_costs(costs: dict[str, dict[str, float]], row: dict[str, Any]) -> None:
    for key in ("standard", "expected", "safe"):
        costs[key]["material_diamonds"] += float(row.get(f"{key}_total_diamonds") or 0)
        costs[key]["material_rmb"] += float(row.get(f"{key}_total_rmb") or 0)


def _rate(value: Any) -> float:
    rate = float(value or 0)
    if rate > 1:
        rate /= 100.0
    if rate <= 0:
        raise ValueError("成功率必须大于 0")
    if rate > 1:
        raise ValueError("成功率不能超过 100%")
    return rate


def _fmt_qty(value: Any) -> str:
    number = float(value or 0)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _fmt_money(value: Any, db: MaterialDatabase) -> str:
    diamonds = float(value or 0)
    return f"{_fmt_qty(diamonds)}钻 / {db.diamonds_to_rmb(diamonds):.2f} RMB"


def _price_text(row: dict[str, Any], quantity: float, db: MaterialDatabase) -> str:
    unit_price = row.get("unit_price_diamonds")
    if unit_price is None:
        return "暂无价格"
    total = float(unit_price) * float(quantity)
    return f"单价 {_fmt_qty(unit_price)}钻，合计 {_fmt_money(total, db)}"


_LEVEL_TOKEN_RE = re.compile(r"^(?:lv|level|l)?\s*(\d+)\s*(?:级)?$", re.IGNORECASE)
_PREFIX_LEVEL_RE = re.compile(r"^(?:lv|level|l)?\s*(\d+)\s*(?:级)?(.+)$", re.IGNORECASE)
_CHINESE_LEVELS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _equipment_item_name(equipment_name: str, level: int) -> str:
    return f"{normalize_item_name(equipment_name)}{int(level)}"


def _same_equipment_level(item_name: str, equipment_name: str) -> int | None:
    item = normalize_item_name(item_name).replace(" ", "")
    equipment = normalize_item_name(equipment_name).replace(" ", "")
    if not item or not equipment:
        return None

    if item.startswith(equipment):
        suffix = item[len(equipment) :].strip()
        level = _parse_level_suffix(suffix)
        if level is not None:
            return level

    prefix_match = _PREFIX_LEVEL_RE.match(item)
    if prefix_match and normalize_item_name(prefix_match.group(2)).replace(" ", "") == equipment:
        return int(prefix_match.group(1))
    return None


def _parse_level_suffix(value: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    match = _LEVEL_TOKEN_RE.match(text)
    if match:
        return int(match.group(1))
    text = text.removesuffix("级").removesuffix("等")
    if text in _CHINESE_LEVELS:
        return _CHINESE_LEVELS[text]
    return None


def _market_price_for_level(db: MaterialDatabase, equipment_name: str, level: int) -> tuple[str, dict[str, Any] | None]:
    base = normalize_item_name(equipment_name)
    candidates = [
        f"{base}{level}",
        f"{base}{level}级",
        f"{base} {level}",
        f"{base}Lv{level}",
        f"{base}LV{level}",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = normalize_item_name(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        price = db.get_price(candidate)
        if price:
            return candidate, price
    return candidates[0], None


def _comparison_text(comparison: dict[str, Any], key: str, label: str, db: MaterialDatabase, *, prefix: str) -> str:
    build = float(comparison["build_costs"][key]["total_diamonds"])
    market = comparison.get("market_total_diamonds")
    if market is None:
        return f"{prefix}：自己合成 {label} {_fmt_money(build, db)}；市场价暂无"
    difference = float(market) - build
    if difference > 0:
        verdict = f"自己合成更省 {_fmt_money(difference, db)}"
    elif difference < 0:
        verdict = f"直接买更省 {_fmt_money(abs(difference), db)}"
    else:
        verdict = "两者成本持平"
    return f"{prefix}：自己合成 {label} {_fmt_money(build, db)}；直接买 {_fmt_money(market, db)}；{verdict}"
