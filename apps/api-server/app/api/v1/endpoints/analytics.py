"""Analytics and reports (permission events:read; people sections additionally observations:read)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.analytics import reports, service
from app.api.deps import DbDep, require_permission
from app.core.permissions import has
from app.models import User

router = APIRouter()
CanRead = Annotated[User, Depends(require_permission("events:read"))]
PEOPLE_HIDDEN = {"available": False, "reason": "Your role can't see people analytics (observations:read)."}


def _query(
    since: datetime | None = None,
    until: datetime | None = None,
    tz: Annotated[str, Query(max_length=64, description="IANA time zone, e.g. Asia/Tashkent")] = "UTC",
    device_id: uuid.UUID | None = None,
    camera_ref: Annotated[str | None, Query(max_length=64)] = None,
) -> dict:
    return {"since": since, "until": until, "tz": tz, "device_id": device_id, "camera_ref": camera_ref}


QueryDep = Annotated[dict, Depends(_query)]


def _filter_people(user: User, data: dict) -> dict:
    if not has(user.role.permissions, "observations:read"):
        data["ai"] = PEOPLE_HIDDEN
    return data


@router.get("/summary")
def summary(user: CanRead, db: DbDep, params: QueryDep):
    """Everything for the Analytics page: standard (motion) and AI (people) separately, health."""
    return _filter_people(user, service.full_report(db, service.make_query(**params)))


@router.get("/ai-performance")
def ai_performance(_: CanRead, db: DbDep, params: QueryDep):
    return service.ai_performance(db, service.make_query(**params))


@router.get("/reports/{kind}")
def report(
    kind: str,
    user: CanRead,
    db: DbDep,
    params: QueryDep,
    format: Annotated[Literal["json", "csv", "pdf"], Query()] = "json",  # noqa: A002
    day: Annotated[date | None, Query(description="Daily/weekly/monthly: last day of the period")] = None,
):
    """Daily / weekly / monthly / camera / device / zone / AI performance report."""
    probe = service.make_query(tz=params["tz"], since=None, until=None)
    since, until = reports.period_for(kind, day, probe.tz, params["since"], params["until"])
    q = service.make_query(**(params | {"since": since, "until": until}))
    data = _filter_people(user, reports.build(db, kind, q))
    if format == "json":
        return data
    stamp = q.until.astimezone(q.tz).strftime("%Y%m%d")
    name = f"{kind}-report-{stamp}"
    if format == "csv":
        return Response(reports.to_csv(data), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})  # fmt: skip
    return Response(reports.to_pdf(data), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})  # fmt: skip
