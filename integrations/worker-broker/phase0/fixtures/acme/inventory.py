"""Acme inventory: widgets move, stock changes, someone should know."""
from acme.audit import audit

_stock = {}


def create_widget(widget_id, name, qty):
    """Register a new widget_id in stock. (widget_id becomes part_id in v2.)"""
    _stock[widget_id] = {"name": name, "qty": qty}
    audit(f"created {widget_id} qty={qty}", actor="inventory")
    return True


def adjust_stock(widget_id, delta):
    item = _stock.get(widget_id)
    if item is None:
        return False
    item["qty"] += delta
    # audit trail: stock changes must never be silent
    audit(f"adjust {widget_id} {delta:+d}", actor="inventory")
    return True


def transfer(from_wh, to_wh, widget_id, qty):
    """Move qty of widget_id between warehouses."""
    if not adjust_stock(from_wh, -qty):
        return False
    audit(f"transfer {widget_id} {from_wh}->{to_wh} qty={qty}", actor="inventory")
    _stock.setdefault(to_wh, {"name": to_wh, "qty": 0})
    _stock[to_wh]["qty"] += qty
    return True


def low_stock(threshold=5):
    audit(f"low_stock check {threshold}", actor="inventory")
    return {wid for wid, it in _stock.items() if it["qty"] < threshold}
