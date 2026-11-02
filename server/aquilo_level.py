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
the tank counts as full. Stdlib only, so the service ships as a pair of files.
"""
from __future__ import annotations


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
