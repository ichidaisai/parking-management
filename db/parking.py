from uuid import UUID

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


class ParkingSpaceCount(SQLModel, table=True):
    """駐車場と区分(一般・身障者用)の組ごとに、空き台数を1行で持つ。"""

    __tablename__ = "parking_space_counts"
    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_parking_space_count_nonnegative"),
        CheckConstraint("kind IN ('general', 'accessible')", name="ck_parking_space_kind"),
    )

    parking_id: str = Field(primary_key=True, max_length=64)
    kind: str = Field(primary_key=True, max_length=16)
    count: int = Field(default=0)


class ParkingCountChange(SQLModel, table=True):
    """適用済みの変更ID。再送された同じ変更を2回足さないために使う。"""

    __tablename__ = "parking_count_changes"

    request_id: UUID = Field(primary_key=True)
    created_at: int = Field(index=True)
