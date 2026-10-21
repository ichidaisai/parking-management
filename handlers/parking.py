from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.sqlite import insert
from sqlmodel import Session, select

from db import get_session
from db.parking import ParkingLotValue
from handlers.auth import SESSION_COOKIE
from lib import pk

type SessionDep = Annotated[Session, Depends(get_session)]

PARKING_LOT_IDS = (
    "parking-1",
    "parking-3",
    "parking-4",
    "track-field",
    "dormitory",
    "library-rear",
    "campus-road",
)


def require_user(request: Request, session: SessionDep) -> None:
    if pk.get_authenticated_user(session, request.cookies.get(SESSION_COOKIE)) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")


parking = APIRouter(tags=["parking"], dependencies=[Depends(require_user)])


class ParkingLotUpdate(BaseModel):
    count: int = Field(ge=0)


def read_counts(session: Session) -> dict[str, int]:
    """全ユーザー共通の台数を返す。未保存の駐車場は0台とする。"""
    counts = dict(session.exec(select(ParkingLotValue.parking_id, ParkingLotValue.count)).all())
    return {parking_id: counts.get(parking_id, 0) for parking_id in PARKING_LOT_IDS}


@parking.get("/parking-lots")
def get_parking_lots(response: Response, session: SessionDep) -> dict[str, int]:
    response.headers["Cache-Control"] = "no-store"
    return read_counts(session)


@parking.put("/parking-lots/{parking_id}")
def update_parking_lot(
    parking_id: str,
    data: ParkingLotUpdate,
    response: Response,
    session: SessionDep,
) -> dict[str, int | str]:
    """駐車場ごとに1行だけ持ち、同時に初回保存されても重複しないよう upsert する。"""
    if parking_id not in PARKING_LOT_IDS:
        raise HTTPException(status_code=404, detail="Parking lot not found")

    session.exec(
        insert(ParkingLotValue)
        .values(parking_id=parking_id, count=data.count)
        .on_conflict_do_update(index_elements=["parking_id"], set_={"count": data.count}),
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"parking_id": parking_id, "count": data.count}


public_parking = APIRouter(tags=["public"])


@public_parking.get("/public/parking-lots")
def get_public_parking_lots(response: Response, session: SessionDep) -> dict[str, int]:
    """外部サイト向け。ログイン不要で、駐車場IDごとの台数だけを返す。"""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=15"
    return read_counts(session)
