"""Visible appearance attributes of a person track.

Only VISIBLE, NON-SENSITIVE clothing/accessory attributes are produced. The system never infers
identity, age, gender, ethnicity, emotion, health or any personal / psychological trait.
Every attribute carries its value, a confidence and its provenance; anything that can't be
determined reliably is ``unknown``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

COLORS = ("black", "white", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink", "brown", "beige")
UPPER_TYPES = ("t-shirt", "shirt", "jacket", "coat", "hoodie", "sweater", "dress", "vest", "other")
LOWER_TYPES = ("trousers", "jeans", "shorts", "skirt", "dress", "other")
UNKNOWN = "unknown"

ATTRIBUTE_NAMES = ("upper_color", "lower_color", "upper_type", "lower_type", "bag", "backpack", "headwear")


@dataclass(slots=True)
class AttributeValue:
    value: str = UNKNOWN  # colour / type name, "yes" / "no", or "unknown"
    confidence: float = 0.0
    source: str = "none"  # e.g. "local_color_v1", "claude:claude-opus-5-5"


@dataclass(slots=True)
class AppearanceAttributes:
    upper_color: AttributeValue = field(default_factory=AttributeValue)
    lower_color: AttributeValue = field(default_factory=AttributeValue)
    upper_type: AttributeValue = field(default_factory=AttributeValue)
    lower_type: AttributeValue = field(default_factory=AttributeValue)
    bag: AttributeValue = field(default_factory=AttributeValue)
    backpack: AttributeValue = field(default_factory=AttributeValue)
    headwear: AttributeValue = field(default_factory=AttributeValue)
    analysed_at: str | None = None

    def merge(self, other: AppearanceAttributes) -> AppearanceAttributes:
        """Keep, per attribute, the known value with the higher confidence."""
        out = AppearanceAttributes(analysed_at=datetime.now(UTC).isoformat())
        for name in ATTRIBUTE_NAMES:
            a, b = getattr(self, name), getattr(other, name)
            if a.value == UNKNOWN:
                best = b
            elif b.value == UNKNOWN:
                best = a
            else:
                best = a if a.confidence >= b.confidence else b
            setattr(out, name, best)
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        parts = []
        if self.upper_color.value != UNKNOWN:
            parts.append(f"{self.upper_color.value} top")
        if self.lower_color.value != UNKNOWN:
            parts.append(f"{self.lower_color.value} bottom")
        for name in ("bag", "backpack", "headwear"):
            if getattr(self, name).value == "yes":
                parts.append(name)
        return ", ".join(parts)
