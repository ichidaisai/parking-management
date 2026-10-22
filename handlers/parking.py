import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from db import get_session
from db.parking import ParkingSpaceCount
from handlers.auth import SESSION_COOKIE
from lib import pk

type SessionDep = Annotated[Session, Depends(get_session)]
type SpaceKind = Literal["general", "accessible"]
type ParkingCounts = dict[str, dict[SpaceKind, int]]

# 駐車場IDと、その駐車場にある区分(一般・身障者用)ごとの最大台数。None は未定で上限なし。
# 画面側の上限は web/mypage/index.html の data-max に同じ値を書く。
PARKING_LOTS: dict[str, dict[SpaceKind, int | None]] = {
    "parking-1": {"general": 65},
    "parking-3": {"general": None},
    "parking-4": {"general": 40, "accessible": 4},
    "track-field": {"general": None},
    "dormitory": {"general": 40},
    "library-rear": {"general": 65, "accessible": 2},
    "campus-road": {"general": None},
}

# 夏時間がないので固定オフセットで扱い、コンテナのタイムゾーンデータに依存しない。
JST = timezone(timedelta(hours=9), "JST")


def parse_public_hours(value: str) -> list[tuple[datetime, datetime]]:
    """`開始/終了` のカンマ区切りを日本時間の期間にする。空なら期間なし(常に公開)。"""
    periods = []
    for item in filter(None, (part.strip() for part in value.split(","))):
        start_text, separator, end_text = item.partition("/")
        if not separator:
            raise ValueError(f"PUBLIC_HOURS must be start/end: {item!r}")
        start, end = (datetime.fromisoformat(text.strip()) for text in (start_text, end_text))
        # オフセットのない時刻は日本時間とみなす。
        start, end = (dt if dt.tzinfo else dt.replace(tzinfo=JST) for dt in (start, end))
        if start >= end:
            raise ValueError(f"PUBLIC_HOURS start must be before end: {item!r}")
        periods.append((start, end))
    return periods


# 外部サイトに台数を見せる期間(大学祭の時間)。設定ミスは起動時に止める。
PUBLIC_HOURS = parse_public_hours(os.getenv("PUBLIC_HOURS", ""))


def require_user(request: Request, session: SessionDep) -> None:
    if pk.get_authenticated_user(session, request.cookies.get(SESSION_COOKIE)) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")


parking = APIRouter(tags=["parking"], dependencies=[Depends(require_user)])


class ParkingLotUpdate(BaseModel):
    """変更する区分だけを送る。その駐車場にない区分は受け付けない。"""

    model_config = ConfigDict(extra="forbid")

    general: int | None = Field(default=None, ge=0)
    accessible: int | None = Field(default=None, ge=0)


def read_counts(session: Session) -> ParkingCounts:
    """全ユーザー共通の台数を返す。未保存の区分は0台とする。"""
    rows = session.exec(
        select(ParkingSpaceCount.parking_id, ParkingSpaceCount.kind, ParkingSpaceCount.count),
    ).all()
    counts = {(parking_id, kind): count for parking_id, kind, count in rows}
    return {
        parking_id: {kind: counts.get((parking_id, kind), 0) for kind in kinds}
        for parking_id, kinds in PARKING_LOTS.items()
    }


@parking.get("/parking-lots")
def get_parking_lots(response: Response, session: SessionDep) -> ParkingCounts:
    response.headers["Cache-Control"] = "no-store"
    return read_counts(session)


@parking.put("/parking-lots/{parking_id}")
def update_parking_lot(
    parking_id: str,
    data: ParkingLotUpdate,
    response: Response,
    session: SessionDep,
) -> dict[SpaceKind, int]:
    """区分ごとに1行だけ持ち、同時に初回保存されても重複しないよう upsert する。"""
    capacities = PARKING_LOTS.get(parking_id)
    if capacities is None:
        raise HTTPException(status_code=404, detail="Parking lot not found")
    updates: dict[SpaceKind, int] = data.model_dump(exclude_none=True)
    if not updates or not updates.keys() <= capacities.keys():
        raise HTTPException(status_code=422, detail="Invalid parking space kind")
    for kind, count in updates.items():
        capacity = capacities[kind]
        if capacity is not None and count > capacity:
            raise HTTPException(status_code=422, detail="Exceeds parking capacity")

    for kind, count in updates.items():
        session.exec(
            insert(ParkingSpaceCount)
            .values(parking_id=parking_id, kind=kind, count=count)
            .on_conflict_do_update(index_elements=["parking_id", "kind"], set_={"count": count}),
        )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return read_counts(session)[parking_id]


def is_public_open(now: datetime) -> bool:
    """開始時刻を含み、終了時刻を含まない。期間が未設定なら常に公開する。"""
    return not PUBLIC_HOURS or any(start <= now < end for start, end in PUBLIC_HOURS)


class PublicParkingLots(BaseModel):
    open: bool
    lots: dict[str, dict[SpaceKind, int | None]]


public_parking = APIRouter(tags=["public"])


@public_parking.get("/public/parking-lots")
def get_public_parking_lots(response: Response, session: SessionDep) -> PublicParkingLots:
    """外部サイト向け。ログイン不要で、駐車場IDごとに区分別の台数だけを返す。公開時間外は台数を null にする。"""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=15"
    if is_public_open(datetime.now(JST)):
        return PublicParkingLots(open=True, lots=read_counts(session))
    hidden = {parking_id: dict.fromkeys(kinds) for parking_id, kinds in PARKING_LOTS.items()}
    return PublicParkingLots(open=False, lots=hidden)
