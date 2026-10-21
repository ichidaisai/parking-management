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
