from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


class ParkingLotValue(SQLModel, table=True):
    __tablename__ = "parking_lot_values"
    __table_args__ = (CheckConstraint("count >= 0", name="ck_parking_lot_count_nonnegative"),)

    parking_id: str = Field(primary_key=True, max_length=64)
    count: int = Field(default=0)
