"""On-device clothing colour estimation (no neural network, no network).

Upper clothing = 25–50 % of the person box height, lower = 55–90 % (central 60 % of the width to
limit background). The dominant HSV colour bin and its share of pixels give value and confidence.
Clothing type, bag, backpack and headwear cannot be judged reliably this way → ``unknown``.

Safeguards (privacy + accuracy): colours are only estimated for a full-body view (box height ≥ 1.6×
width, not cut off by the frame), and skin-tone pixels are excluded from the vote, so the system
never reports a skin colour as a clothing colour. Anything else yields ``unknown``.
"""

from __future__ import annotations

import cv2
import numpy as np

from aci_agent.ai_engine.attributes.models import UNKNOWN, AppearanceAttributes, AttributeValue

SOURCE = "local_color_v1"
MIN_CONFIDENCE = 0.35


def _colour_name(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Vectorised HSV (OpenCV ranges: H 0–179, S/V 0–255) → colour index into NAMES."""
    names = np.full(h.shape, -1, np.int8)
    dark = v < 50
    low_sat = s < 40
    names[dark] = 0  # black
    names[~dark & low_sat & (v > 200)] = 1  # white
    names[~dark & low_sat & (v <= 200)] = 2  # gray
    chroma = ~dark & ~low_sat
    hh = h.astype(np.int16)
    brown = chroma & (hh >= 8) & (hh < 22) & (v < 150)
    names[brown] = 10
    beige = chroma & (hh >= 10) & (hh < 30) & (s < 90) & (v >= 150)
    names[beige] = 11
    rest = chroma & ~brown & ~beige
    names[rest & ((hh < 8) | (hh >= 170))] = 3  # red
    names[rest & (hh >= 8) & (hh < 22)] = 4  # orange
    names[rest & (hh >= 22) & (hh < 34)] = 5  # yellow
    names[rest & (hh >= 34) & (hh < 85)] = 6  # green
    names[rest & (hh >= 85) & (hh < 130)] = 7  # blue
    names[rest & (hh >= 130) & (hh < 155)] = 8  # purple
    names[rest & (hh >= 155) & (hh < 170)] = 9  # pink
    return names


NAMES = ("black", "white", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink", "brown", "beige")


def _skin_like(h: np.ndarray, s: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Broad skin-tone range (all complexions). Used only to EXCLUDE pixels from the colour vote."""
    return (h <= 25) & (s >= 30) & (s <= 180) & (v >= 50)


MIN_ASPECT = 1.6  # full-body view; head-and-shoulders crops are not analysed


def _dominant(region: np.ndarray) -> AttributeValue:
    if region.size == 0 or region.shape[0] < 4 or region.shape[1] < 4:
        return AttributeValue(UNKNOWN, 0.0, SOURCE)
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    keep = ~_skin_like(h, s, v)
    if keep.mean() < 0.5:  # mostly skin-coloured: no reliable clothing colour here
        return AttributeValue(UNKNOWN, 0.0, SOURCE)
    idx = _colour_name(h, s, v)[keep].ravel()
    idx = idx[idx >= 0]
    if idx.size == 0:
        return AttributeValue(UNKNOWN, 0.0, SOURCE)
    counts = np.bincount(idx, minlength=len(NAMES))
    best = int(counts.argmax())
    share = float(counts[best] / idx.size)
    if share < MIN_CONFIDENCE:
        return AttributeValue(UNKNOWN, round(share, 2), SOURCE)  # mixed / patterned: not reliable
    return AttributeValue(NAMES[best], round(share, 2), SOURCE)


def analyse_colors(image: np.ndarray, box: tuple[float, float, float, float]) -> AppearanceAttributes:
    x, y, w, h = (int(round(v)) for v in box)
    H, W = image.shape[:2]
    attrs = AppearanceAttributes()
    cut_off = y <= 2 or y + h >= H - 2 or x <= 2 or x + w >= W - 2
    if w <= 0 or h / w < MIN_ASPECT or cut_off:
        # partial view (e.g. only head and shoulders): colours would describe skin or background
        attrs.upper_color = attrs.lower_color = AttributeValue(UNKNOWN, 0.0, SOURCE)
        return attrs
    x0, x1 = max(0, x + int(w * 0.2)), min(W, x + int(w * 0.8))

    def band(a: float, b: float) -> np.ndarray:
        return image[max(0, y + int(h * a)) : min(H, y + int(h * b)), x0:x1]

    attrs.upper_color = _dominant(band(0.25, 0.50))
    attrs.lower_color = _dominant(band(0.55, 0.90))
    return attrs
