from __future__ import annotations

import math

from .database import MaterialDatabase


DEFAULT_TRADE_TAX_RATE = 0.05


def _validate_tax_rate(tax_rate: float) -> float:
    rate = float(tax_rate)
    if rate < 0 or rate >= 1:
        raise ValueError("交易税率必须大于等于 0 且小于 100%")
    return rate


def net_after_trade_tax(gross_diamonds: float | int, tax_rate: float = DEFAULT_TRADE_TAX_RATE) -> int:
    rate = _validate_tax_rate(tax_rate)
    gross = max(0, int(math.ceil(float(gross_diamonds or 0))))
    return gross - trade_tax_amount(gross, rate)


def required_trade_gross_for_net(net_diamonds: float | int, tax_rate: float = DEFAULT_TRADE_TAX_RATE) -> int:
    rate = _validate_tax_rate(tax_rate)
    target_net = max(0, int(math.ceil(float(net_diamonds or 0))))
    if target_net <= 0:
        return 0
    gross = max(0, int(math.floor(target_net / (1.0 - rate))) - 2)
    while net_after_trade_tax(gross, rate) < target_net:
        gross += 1
    while gross > 0 and net_after_trade_tax(gross - 1, rate) >= target_net:
        gross -= 1
    return gross


def trade_tax_amount(gross_diamonds: float | int, tax_rate: float = DEFAULT_TRADE_TAX_RATE) -> int:
    rate = _validate_tax_rate(tax_rate)
    gross = max(0, int(math.ceil(float(gross_diamonds or 0))))
    return int(math.floor(gross * rate + 1e-9))


def diamonds_to_rmb(db: MaterialDatabase, diamonds: float | int | None) -> float:
    return db.diamonds_to_rmb(diamonds)


def format_diamond_rmb(db: MaterialDatabase, diamonds: float | int | None) -> str:
    value = float(diamonds or 0)
    if value.is_integer():
        diamond_text = str(int(value))
    else:
        diamond_text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{diamond_text}钻 / {db.diamonds_to_rmb(value):.2f} RMB"
