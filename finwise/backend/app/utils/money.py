from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TWO = Decimal("0.01")


def D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid decimal value: {value!r}")


def q2(value) -> Decimal:
    return D(value).quantize(TWO, rounding=ROUND_HALF_UP)


def r2(value: float | Decimal | None) -> float | None:
    """Round for JSON output."""
    if value is None:
        return None
    return float(round(float(value), 2))
