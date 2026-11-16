"""Preview overlays drawn by the agent into the MJPEG stream (each layer can be toggled).

Layers: ``regions`` (motion mask), ``boxes`` (motion bounding boxes), ``tracks`` (temporary motion
track IDs + trail), ``zones`` (polygons + occupancy estimate), ``lines`` (crossing lines + counts +
forward-direction arrow). Drawing works on a copy; the shared frame is never modified.
"""

from __future__ import annotations

import cv2
import numpy as np

from aci_agent.standard_engine.pipeline import VisionState
from aci_agent.standard_engine.rules import CrossingLine, Zone

LAYERS = ("regions", "boxes", "tracks", "zones", "lines", "people")

# BGR colours
C_REGION = (60, 170, 255)
C_BOX = (40, 200, 255)
C_TRACK = (90, 230, 120)
C_ZONE = (255, 190, 60)
C_EXCLUDE = (120, 120, 120)
C_LINE = (230, 120, 255)
C_PERSON = (60, 220, 255)


def parse_layers(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    if value == "all":
        return frozenset(LAYERS)
    return frozenset(v for v in value.split(",") if v in LAYERS)


def _px(pt, w, h):
    return int(round(pt[0] * (w - 1))), int(round(pt[1] * (h - 1)))


def _label(img, text, org, color, scale):
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = org
    cv2.rectangle(img, (x, y - th - 4), (x + tw + 4, y + 2), (0, 0, 0), -1)
    cv2.putText(img, text, (x + 2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def draw_overlays(
    image: np.ndarray,
    state: VisionState | None,
    zones: list[Zone],
    lines: list[CrossingLine],
    layers: frozenset[str],
    line_totals: dict[str, dict[str, int]] | None = None,
    people: list[dict] | None = None,
) -> np.ndarray:
    if not layers:
        return image
    out = image.copy()
    h, w = out.shape[:2]
    scale = max(0.4, min(1.2, w / 1600))
    thick = max(1, int(round(w / 640)))

    if "regions" in layers and state is not None and state.mask is not None and state.mask.size:
        mask = cv2.resize(state.mask, (w, h), interpolation=cv2.INTER_NEAREST)
        tint = np.zeros_like(out)
        tint[:] = C_REGION
        sel = mask > 0
        out[sel] = cv2.addWeighted(out, 0.55, tint, 0.45, 0)[sel]

    if "zones" in layers:
        for z in zones:
            if not z.is_enabled:
                continue
            pts = np.array([_px(p, w, h) for p in z.polygon], np.int32)
            color = C_EXCLUDE if z.role == "exclude" else C_ZONE
            overlay = out.copy()
            cv2.fillPoly(overlay, [pts], color)
            cv2.addWeighted(overlay, 0.15, out, 0.85, 0, dst=out)
            cv2.polylines(out, [pts], True, color, thick, cv2.LINE_AA)
            occ = (state.zone_occupancy.get(z.id, 0) if state else 0) if z.role == "monitor" else None
            text = z.name + (f" · {occ} obj (est.)" if occ is not None else " · excluded")
            # Label inside the polygon's top-left corner so it doesn't collide with line labels.
            x0, y0 = int(pts[:, 0].min()), int(pts[:, 1].min())
            _label(out, text, (x0 + 4, y0 + int(18 * scale) + 4), color, scale * 0.8)

    if "lines" in layers:
        for ln in lines:
            if not ln.is_enabled:
                continue
            a, b = _px(ln.a, w, h), _px(ln.b, w, h)
            cv2.line(out, a, b, C_LINE, thick + 1, cv2.LINE_AA)
            # forward-direction arrow: normal (dy, -dx) from the midpoint
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = max((dx * dx + dy * dy) ** 0.5, 1)
            nx, ny = dy / length, -dx / length
            tip = (int(mx + nx * 30 * scale * 2), int(my + ny * 30 * scale * 2))
            cv2.arrowedLine(out, (int(mx), int(my)), tip, C_LINE, thick, cv2.LINE_AA, tipLength=0.35)
            counts = (line_totals or {}).get(ln.id) or (state.line_counts.get(ln.id) if state else None) or {}
            text = f"{ln.name}  fwd {counts.get('forward', 0)} / back {counts.get('backward', 0)}"
            _label(out, text, (min(a[0], b[0]), max(14, min(a[1], b[1]) - 6)), C_LINE, scale * 0.8)

    if state is not None and ("boxes" in layers or "tracks" in layers):
        boxes = [(t.box, t) for t in state.tracks] if state.tracks else [(r, None) for r in state.regions]
        for box, track in boxes:
            x, y, bw, bh = box
            p1, p2 = _px((x, y), w, h), _px((x + bw, y + bh), w, h)
            if "boxes" in layers:
                cv2.rectangle(out, p1, p2, C_BOX, thick, cv2.LINE_AA)
            if "tracks" in layers and track is not None:
                trail = [_px(p, w, h) for p in track.trail]
                for i in range(1, len(trail)):
                    cv2.line(out, trail[i - 1], trail[i], C_TRACK, thick, cv2.LINE_AA)
                arrow = f" {track.direction}" if track.direction else ""
                _label(out, f"{track.track_id.rsplit('-', 1)[-1]}{arrow}", (p1[0], max(14, p1[1] - 4)), C_TRACK, scale)

    if "people" in layers and people:
        # AI person tracks (temporary IDs), labelled with visible attributes only.
        for p in people:
            x, y, bw, bh = p["box"]
            p1, p2 = _px((x, y), w, h), _px((x + bw, y + bh), w, h)
            cv2.rectangle(out, p1, p2, C_PERSON, thick + 1, cv2.LINE_AA)
            label = f"{p['track_id'].rsplit('-', 1)[-1]} person {p['confidence']:.2f}"
            if p.get("attributes"):
                label += f" | {p['attributes']}"
            _label(out, label, (p1[0], max(14, p1[1] - 4)), C_PERSON, scale)

    if state is not None:
        status = "MOTION" if state.motion_active else state.status.upper().replace("_", " ")
        _label(out, f"STANDARD ENGINE · {status}", (8, h - 8), (220, 220, 220), scale * 0.8)
    return out
