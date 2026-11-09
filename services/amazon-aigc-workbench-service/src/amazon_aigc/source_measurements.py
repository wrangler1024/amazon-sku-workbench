"""Read-only supplier measurements. Package data is never a confirmed product fact."""

import math
from decimal import Decimal, InvalidOperation


def positive(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number <= 0:
        return None
    converted = float(number)
    return converted if math.isfinite(converted) and converted > 0 else None


def package_measurements(raw):
    raw = raw if isinstance(raw, dict) else {}
    # TMAPI shipping API documents kg for the weight supplied by its product API.
    # Dimensions have no default unit in the documented response; don't guess.
    unit = str(raw.get("length_unit") or raw.get("dimension_unit") or "").strip().lower()
    return {
        "weight_kg": positive(raw.get("weight")),
        "length": positive(raw.get("length")),
        "width": positive(raw.get("width")),
        "height": positive(raw.get("height")),
        "dimension_unit": unit if unit in ("mm", "cm", "m", "in") else "",
    }


def measurement_projection(data):
    return {
        "package_by_sku": {
            str(sku["skuid"]): package_measurements(sku.get("package_info"))
            for sku in data.get("skus", [])
            if isinstance(sku, dict) and sku.get("skuid")
        },
        "default_shipping_weight_kg": positive((data.get("delivery_info") or {}).get("unit_weight")),
    }
