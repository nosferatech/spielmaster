"""Price lookup and quoting."""
import json

from acme.audit import audit

_PRICES = {"w-1": 9.99, "w-2": 24.50, "w-3": 3.00}


def price_for(widget_id):
    """Return unit price for a widget_id. (widget_id becomes part_id in v2.)"""
    return _PRICES.get(widget_id)


def quote(widget_ids):
    total = 0.0
    lines = []
    for wid in widget_ids:
        p = price_for(wid)
        if p is None:
            raise KeyError(f"no price for {wid}")
        total += p
        lines.append({"widget_id": wid, "price": p})
    audit(f"quote n={len(lines)} total={total:.2f}", actor="pricing")
    return json.dumps({"lines": lines, "total": round(total, 2)})
