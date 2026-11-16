"""2D geometry helpers. Zones and lines are stored in normalized coordinates (0..1) so they stay
valid when a camera changes resolution."""

from __future__ import annotations

from dataclasses import dataclass

Point = tuple[float, float]


@dataclass(frozen=True, slots=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def centroid(self) -> Point:
        return (self.x + self.w / 2, self.y + self.h / 2)

    @property
    def area(self) -> float:
        return self.w * self.h

    def iou(self, other: Box) -> float:
        ix = max(0.0, min(self.x + self.w, other.x + other.w) - max(self.x, other.x))
        iy = max(0.0, min(self.y + self.h, other.y + other.h) - max(self.y, other.y))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0


def point_in_polygon(p: Point, polygon: list[Point]) -> bool:
    """Ray casting; points on the boundary count as inside."""
    x, y = p
    inside = False
    n = len(polygon)
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        if _on_segment((x1, y1), (x2, y2), p):
            return True
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def side_of_line(a: Point, b: Point, p: Point) -> float:
    """>0: p is left of A→B, <0: right, 0: on the line (image coordinates, y down)."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1 = side_of_line(q1, q2, p1)
    d2 = side_of_line(q1, q2, p2)
    d3 = side_of_line(p1, p2, q1)
    d4 = side_of_line(p1, p2, q2)
    if ((d1 > 0) != (d2 > 0)) and d1 != 0 and d2 != 0 and ((d3 > 0) != (d4 > 0)) and d3 != 0 and d4 != 0:
        return True
    return (
        (d1 == 0 and _on_segment(q1, q2, p1))
        or (d2 == 0 and _on_segment(q1, q2, p2))
        or (d3 == 0 and _on_segment(p1, p2, q1))
        or (d4 == 0 and _on_segment(p1, p2, q2))
    )


def _on_segment(a: Point, b: Point, p: Point, eps: float = 1e-9) -> bool:
    if abs(side_of_line(a, b, p)) > eps:
        return False
    return (
        min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
        and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
    )


def compass(dx: float, dy: float, min_move: float = 1e-6) -> str | None:
    """Image-space movement direction as N/NE/E/SE/S/SW/W/NW (N = up). None if not moving."""
    if abs(dx) < min_move and abs(dy) < min_move:
        return None
    import math

    angle = (math.degrees(math.atan2(-dy, dx)) + 360) % 360  # 0 = east, counter-clockwise
    names = ["E", "NE", "N", "NW", "W", "SW", "S", "SE"]
    return names[int((angle + 22.5) // 45) % 8]


def validate_polygon(points: list[Point]) -> list[Point]:
    if not 3 <= len(points) <= 64:
        raise ValueError("A zone needs 3–64 points")
    for x, y in points:
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("Zone coordinates must be normalized to 0..1")
    area = 0.0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    if abs(area) / 2 < 1e-4:
        raise ValueError("Zone is too small or degenerate")
    return [(float(x), float(y)) for x, y in points]
