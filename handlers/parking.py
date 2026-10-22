import os
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from db import get_session
from db.parking import ParkingCountChange, ParkingSpaceCount
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

# 変更IDを覚えておく秒数。これより古い再送は届かない前提で消す。
CHANGE_ID_TTL = 86400

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


class ParkingLotChange(BaseModel):
    """変更する区分の増減と、変更を始めたときに見ていた台数を送る。

    request_id は変更ごとに作り、同じ変更を送り直すときは同じ値を使う。
    """

    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    expected: dict[SpaceKind, int]
    changes: dict[SpaceKind, int]


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


@parking.post("/parking-lots/{parking_id}/changes")
def change_parking_lot(
    parking_id: str,
    data: ParkingLotChange,
    response: Response,
    session: SessionDep,
) -> dict[SpaceKind, int]:
    """今の台数に増減を足す。

    見ていた台数から他の人が変えていたら保存しない。同じ車を2人で記録したのか、
    別々の車なのかはシステムでは区別できないため、画面で確認してから送り直してもらう。
    変更IDを先に書き込むと SQLite の書き込みロックが取れるため、
    台数を読んでから書き込むまでの間に、他の変更は割り込めない。
    """
    capacities = PARKING_LOTS.get(parking_id)
    if capacities is None:
        raise HTTPException(status_code=404, detail="Parking lot not found")
    changes = data.changes
    if not changes or not changes.keys() <= capacities.keys() or data.expected.keys() != changes.keys():
        raise HTTPException(status_code=422, detail="Invalid parking space kind")

    now = int(time.time())
    session.add(ParkingCountChange(request_id=data.request_id, created_at=now))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Change already applied") from None
    session.exec(
        delete(ParkingCountChange).where(col(ParkingCountChange.created_at) < now - CHANGE_ID_TTL),
    )

    current = read_counts(session)[parking_id]
    if any(current[kind] != data.expected[kind] for kind in changes):
        session.rollback()
        raise HTTPException(status_code=409, detail="Changed by another user")
    for kind, change in changes.items():
        count = current[kind] + change
        capacity = capacities[kind]
        if count < 0 or (capacity is not None and count > capacity):
            session.rollback()
            raise HTTPException(status_code=409, detail="Parking count out of range")
        # 区分ごとに1行だけ持ち、同時に初回保存されても重複しないよう upsert する
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
