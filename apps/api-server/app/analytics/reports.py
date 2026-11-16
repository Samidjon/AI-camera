"""Reports built from the Analytics Service: daily / weekly / monthly / camera / device / zone /
AI performance, as JSON, CSV or PDF.

PDF uses fpdf2 with a Unicode TrueType font found on the machine (Windows: Arial; Linux / Jetson:
DejaVu or Liberation). Without one, non-Latin-1 characters are replaced with "?" — the report is
still produced; install fonts-dejavu-core on minimal Linux images for full Unicode.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, time, timedelta
from pathlib import Path

from fpdf import FPDF
from sqlalchemy.orm import Session

from app import __version__
from app.analytics import service
from app.core.errors import AppError

KINDS = ("daily", "weekly", "monthly", "camera", "device", "zone", "ai")
TITLES = {
    "daily": "Daily report",
    "weekly": "Weekly report",
    "monthly": "Monthly report",
    "camera": "Camera report",
    "device": "Device report",
    "zone": "Zone report",
    "ai": "AI performance report",
}
FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
]  # fmt: skip


def period_for(kind: str, anchor: date | None, tz, since: datetime | None, until: datetime | None):
    """Daily / weekly / monthly cover whole local days ending with `anchor` (default: today)."""
    if kind not in ("daily", "weekly", "monthly"):
        return since, until
    today = anchor or datetime.now(tz).date()
    days = {"daily": 1, "weekly": 7, "monthly": 30}[kind]
    start = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


def build(db: Session, kind: str, q: service.Query) -> dict:
    if kind not in KINDS:
        raise AppError(f"Unknown report '{kind[:20]}'", code="invalid_report")
    if kind == "camera" and not q.camera_ref:
        raise AppError("The camera report needs device_id and camera_ref", code="camera_required")
    if kind == "device" and not q.device_id:
        raise AppError("The device report needs device_id", code="device_required")
    data = service.full_report(db, q)
    if kind in ("device", "ai"):
        data["ai_performance"] = service.ai_performance(db, q)
    data["kind"] = kind
    data["title"] = TITLES[kind]
    return data


# ---------------------------------------------------------------------------- CSV


def to_csv(report: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "metric", "item", "value"])
    r = report["range"]
    w.writerows([["report", "kind", "", report["kind"]], ["report", "since", "", r["since"]],
                 ["report", "until", "", r["until"]], ["report", "timezone", "", r["timezone"]]])  # fmt: skip
    s = report["standard"]
    w.writerows([["standard", "motion_events", "", s["motion_events"]],
                 ["standard", "activity_duration_s", "", s["activity_duration_s"]],
                 ["standard", "motion_frequency_per_hour", "", s["motion_frequency_per_hour"]]])  # fmt: skip
    for row in s["hourly"]:
        w.writerow(["standard", "motion_events_hourly", row["bucket"], row["value"]])
    for row in s["daily"]:
        w.writerow(["standard", "motion_events_daily", row["bucket"], row["value"]])
    for z in s["zones"]:
        item = f"{z['camera_name']} / {z['name']}"
        w.writerows([["zones", "entries", item, z["entries"]], ["zones", "exits", item, z["exits"]],
                     ["zones", "occupancy_estimate", item, z["occupancy_estimate"]],
                     ["zones", "activity_duration_s", item, z["activity_duration_s"]]])  # fmt: skip
    for ln in s["lines"]:
        item = f"{ln['camera_name']} / {ln['name']}"
        w.writerows([["lines", "forward", item, ln["forward"]], ["lines", "backward", item, ln["backward"]]])
    for c in s["cameras"]:
        w.writerow(["cameras", "motion_events", c["camera_name"], c["motion_events"]])
    a = report["ai"]
    if a["available"]:
        w.writerows([["ai", "people_observations", "", a["people_observations"]], ["ai", "inbound", "", a["inbound"]],
                     ["ai", "outbound", "", a["outbound"]]])  # fmt: skip
        for row in a["hourly"]:
            w.writerow(["ai", "observations_hourly", row["bucket"], row["value"]])
        for p in a["peak_hours"]:
            w.writerow(["ai", "peak_hour", f"{p['hour']:02d}:00", p["observations"]])
        for c in a["cameras"]:
            w.writerow(["ai", "observations_by_camera", c["camera_name"], c["observations"]])
    else:
        w.writerow(["ai", "available", "", "no"])
    for k, v in report["health"]["counts"].items():
        w.writerow(["health", k, "", v])
    for d in report["health"]["camera_downtime"]:
        w.writerow(["health", "camera_downtime_s", d["camera_name"], d["downtime_s"]])
    for d in (report.get("ai_performance") or {}).get("devices", []):
        w.writerows([["ai_performance", "active_share", d["device_name"], d["active_share"]],
                     ["ai_performance", "avg_inference_fps", d["device_name"], d["avg_inference_fps"]],
                     ["ai_performance", "avg_latency_ms", d["device_name"], d["avg_latency_ms"]]])  # fmt: skip
    return buf.getvalue()


# ---------------------------------------------------------------------------- PDF


class _Pdf(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4")
        self.unicode = False
        for regular, bold in FONT_CANDIDATES:
            if Path(regular).exists():
                self.add_font("body", "", regular)
                self.add_font("body", "B", bold if Path(bold).exists() else regular)
                self.unicode = True
                break
        self.family = "body" if self.unicode else "helvetica"
        self.set_auto_page_break(True, margin=15)

    def text(self, value) -> str:  # noqa: D401 - helper, not FPDF.text
        s = str(value)
        return s if self.unicode else s.encode("latin-1", "replace").decode("latin-1")

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(self.family, "", 8)
        self.set_text_color(120)
        self.cell(0, 8, self.text(f"AI Camera Intelligence {__version__} · page {self.page_no()}"), align="C")


def _h(pdf: _Pdf, title: str) -> None:
    pdf.ln(3)
    pdf.set_font(pdf.family, "B", 12)
    pdf.set_text_color(20)
    pdf.cell(0, 8, pdf.text(title), new_x="LMARGIN", new_y="NEXT")


def _note(pdf: _Pdf, text: str) -> None:
    pdf.set_font(pdf.family, "", 8)
    pdf.set_text_color(110)
    pdf.multi_cell(0, 4, pdf.text(text), new_x="LMARGIN", new_y="NEXT")


def _table(pdf: _Pdf, header: list[str], rows: list[list], widths: list[int]) -> None:
    pdf.set_font(pdf.family, "B", 9)
    pdf.set_fill_color(235)
    pdf.set_text_color(30)
    for h, w in zip(header, widths, strict=True):
        pdf.cell(w, 7, pdf.text(h), border=0, fill=True)
    pdf.ln()
    pdf.set_font(pdf.family, "", 9)
    for row in rows or [["—"] + [""] * (len(header) - 1)]:
        for v, w in zip(row, widths, strict=True):
            pdf.cell(w, 6, pdf.text(v if v is not None else "—"))
        pdf.ln()


def _bars(pdf: _Pdf, series: list[dict], label_every: int = 1) -> None:
    """Simple bar chart of a series (bucket, value)."""
    if not series:
        return
    peak = max(s["value"] for s in series) or 1
    x0, y0, w, h = pdf.l_margin, pdf.get_y() + 2, pdf.w - pdf.l_margin - pdf.r_margin, 40
    bw = w / len(series)
    pdf.set_fill_color(60, 160, 200)
    for i, s in enumerate(series):
        bh = h * s["value"] / peak
        if bh > 0:
            pdf.rect(x0 + i * bw + 0.3, y0 + h - bh, max(bw - 0.6, 0.3), bh, style="F")
    pdf.set_draw_color(180)
    pdf.line(x0, y0 + h, x0 + w, y0 + h)
    pdf.set_font(pdf.family, "", 6)
    pdf.set_text_color(110)
    step = max(1, label_every)
    for i in range(0, len(series), step):
        pdf.set_xy(x0 + i * bw, y0 + h + 0.5)
        pdf.cell(bw * step, 3, pdf.text(series[i]["bucket"][-5:]))
    pdf.set_y(y0 + h + 6)
    pdf.set_font(pdf.family, "", 8)
    pdf.cell(0, 4, pdf.text(f"max {peak}"), new_x="LMARGIN", new_y="NEXT")


def to_pdf(report: dict) -> bytes:
    pdf = _Pdf()
    pdf.add_page()
    r = report["range"]
    pdf.set_font(pdf.family, "B", 16)
    pdf.cell(0, 10, pdf.text(report["title"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(pdf.family, "", 9)
    pdf.set_text_color(90)
    pdf.cell(0, 5, pdf.text(f"{r['since']}  →  {r['until']}  ({r['timezone']})"), new_x="LMARGIN", new_y="NEXT")
    f = report["filters"]
    if f["device_id"] or f["camera_ref"]:
        pdf.cell(0, 5, pdf.text(f"device {f['device_id'] or 'all'} · camera {f['camera_ref'] or 'all'}"),
                 new_x="LMARGIN", new_y="NEXT")  # fmt: skip

    s = report["standard"]
    _h(pdf, "Motion analytics (Standard Engine, no AI)")
    _table(pdf, ["Metric", "Value"], [
        ["Motion events", s["motion_events"]],
        ["Activity duration", f"{s['activity_duration_s'] / 60:.1f} min"],
        ["Motion frequency", f"{s['motion_frequency_per_hour']} / hour"],
    ], [90, 90])  # fmt: skip
    series = s["daily"] if len(s["hourly"]) > 48 else s["hourly"]
    _bars(pdf, series, label_every=max(1, len(series) // 12))
    _note(pdf, report["limitations"]["motion"])

    if report["kind"] in ("zone", "daily", "weekly", "monthly", "camera") and s["zones"]:
        _h(pdf, "Zones")
        _table(pdf, ["Camera / zone", "Entries", "Exits", "Occupancy est.", "Avg dwell"], [
            [f"{z['camera_name']} / {z['name']}", z["entries"], z["exits"], z["occupancy_estimate"],
             f"{z['avg_dwell_s']} s" if z["avg_dwell_s"] is not None else None] for z in s["zones"][:30]
        ], [70, 25, 25, 30, 30])  # fmt: skip
        _note(pdf, report["limitations"]["zones"])
    if s["lines"]:
        _h(pdf, "Line crossings (motion objects)")
        _table(pdf, ["Camera / line", "Forward", "Backward"],
               [[f"{x['camera_name']} / {x['name']}", x["forward"], x["backward"]] for x in s["lines"][:30]],
               [110, 35, 35])  # fmt: skip
    if s["cameras"] and report["kind"] != "camera":
        _h(pdf, "Camera comparison (motion)")
        _table(pdf, ["Camera", "Motion events", "Activity (min)"],
               [[c["camera_name"], c["motion_events"], round(c["activity_duration_s"] / 60, 1)] for c in s["cameras"]],
               [100, 40, 40])  # fmt: skip

    a = report["ai"]
    _h(pdf, "People analytics (AI Engine)")
    if not a["available"]:
        _note(pdf, a["reason"])
    else:
        _table(pdf, ["Metric", "Value"], [
            ["People observations", a["people_observations"]], ["Inbound", a["inbound"]], ["Outbound", a["outbound"]],
            ["Peak hours", ", ".join(f"{p['hour']:02d}:00 ({p['observations']})" for p in a["peak_hours"]) or "—"],
        ], [90, 90])  # fmt: skip
        series = a["daily"] if len(a["hourly"]) > 48 else a["hourly"]
        _bars(pdf, series, label_every=max(1, len(series) // 12))
        if a["cameras"]:
            _table(pdf, ["Camera", "Observations", "In", "Out"],
                   [[c["camera_name"], c["observations"], c["inbound"], c["outbound"]] for c in a["cameras"]],
                   [90, 35, 25, 25])  # fmt: skip
        _note(pdf, report["limitations"]["people"] + " " + report["limitations"]["lines"])

    h = report["health"]
    _h(pdf, "Camera, AI and device health")
    _table(pdf, ["Event", "Count"], [[k, v] for k, v in sorted(h["counts"].items())], [110, 70])
    if h["camera_downtime"]:
        _table(pdf, ["Camera", "Downtime (min)", "Still failing"],
               [[d["camera_name"], round(d["downtime_s"] / 60, 1), "yes" if d["still_failing"] else "no"]
                for d in h["camera_downtime"]], [100, 40, 40])  # fmt: skip
        _note(pdf, report["limitations"]["health"])

    perf = report.get("ai_performance")
    if perf:
        _h(pdf, "AI performance (from device heartbeats)")
        _table(pdf, ["Device", "Active share", "Inference FPS", "Avg latency", "Max latency"], [
            [d["device_name"], f"{d['active_share'] * 100:.0f}%", d["avg_inference_fps"],
             f"{d['avg_latency_ms']} ms" if d["avg_latency_ms"] is not None else None,
             f"{d['max_latency_ms']} ms" if d["max_latency_ms"] is not None else None] for d in perf["devices"]
        ], [55, 30, 30, 30, 30])  # fmt: skip
        if not perf["devices"]:
            _note(pdf, "No heartbeats in this period.")
    return bytes(pdf.output())
