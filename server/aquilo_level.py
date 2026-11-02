#!/usr/bin/env python3
"""The one implementation of the Aquilo level arithmetic.

This lived in three copies before: the calculator, its own validator, and the
service that runs in production. The validator compared its own copy, so a
mistake in the code that actually ships would have passed. One function now, and
the validator exercises exactly what the service runs.

The vendor's own mapping, read off the retained settings and checked against
every point in its recorded history:

    lvl_cm   = read1 / 100           # radar distance down to the liquid surface
    pct      = clamp((depth - lvl) / (depth - full) * 100, 0, 100)
    lvlToFull= lvl - full

Depth comes from the tank settings: the manual value in manual mode, and in auto
mode the depth the cloud derived, which it reports in hundredths of a centimetre
as ``output.maxLevel``. ``full`` is ``settings.maxLevel``, the offset at which
the tank counts as full. Stdlib only, so the service can ship as a pair of files.
"""
from __future__ import annotations

from bisect import bisect_right


def pct_exact(lvl_cm: float, depth_cm: float, full_cm: float) -> float:
    """Return the fill percentage before rounding.

    Args:
        lvl_cm: Measured distance to the surface, in centimetres.
        depth_cm: Tank depth in centimetres.
        full_cm: The "full" offset in centimetres.

    Returns:
        The percentage, clamped to 0..100. A reading past the empty reference
        goes slightly negative and the vendor publishes 0 there; both of the
        only deviations in its whole recorded history were that case, at the
        sewage tank's lstEmpty moment.

    Raises:
        ValueError: If the usable span is not positive, which would make the
            percentage meaningless rather than merely wrong.
    """
    span = depth_cm - full_cm
    if span <= 0:
        raise ValueError(f"tank depth {depth_cm} is not above its full offset {full_cm}")
    return max(0.0, min(100.0, (depth_cm - lvl_cm) / span * 100))


def levels(read1: float, depth_cm: float, full_cm: float) -> dict[str, float]:
    """Turn a raw radar reading into the fields the vendor publishes.

    Args:
        read1: Raw reading, in hundredths of a centimetre.
        depth_cm: Tank depth in centimetres.
        full_cm: The "full" offset in centimetres.

    Returns:
        ``{"lvl", "pct", "lvlToFull"}``, rounded the way the vendor rounds them.

    Raises:
        ValueError: Propagated from :func:`pct_exact`.
    """
    lvl = read1 / 100
    return {
        "lvl": round(lvl, 1),
        "pct": round(pct_exact(lvl, depth_cm, full_cm)),
        "lvlToFull": round(lvl - full_cm),
    }


def battery_pct(millivolts: float, curve: list[list[float]]) -> int:
    """Turn a battery voltage into a percentage along a per-hardware curve.

    The vendor derives its percentage from the voltage alone, per hardware
    revision, so a table of knots recovered from its recorded history
    reproduces it. Between knots the value is interpolated linearly; outside
    them it clamps to the first and last knot.

    Args:
        millivolts: Battery voltage in millivolts.
        curve: ``[[millivolts, percent], ...]`` sorted by ascending voltage.

    Returns:
        The percentage, rounded, within 0..100.

    Raises:
        ValueError: If the curve has fewer than two knots.
    """
    if len(curve) < 2:
        raise ValueError("a battery curve needs at least two knots")
    volts = [k[0] for k in curve]
    if millivolts <= volts[0]:
        return round(curve[0][1])
    if millivolts >= volts[-1]:
        return round(curve[-1][1])
    i = bisect_right(volts, millivolts)
    (v0, p0), (v1, p1) = curve[i - 1], curve[i]
    return round(p0 + (p1 - p0) * (millivolts - v0) / (v1 - v0))
