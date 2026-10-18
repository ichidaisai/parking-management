from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, LargeBinary, String, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(SQLModel, table=True):
    __tablename__ = "users"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(
        index=True,
        unique=True,
        max_length=100,
    )
    display_name: str | None = Field(
        default=None,
        max_length=100,
    )
    email: str | None = Field(
        default=None,
        index=True,
        max_length=255,
    )
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    passkeys: list["PasskeyCredential"] = Relationship(
        back_populates="user",
    )


class PasskeyCredential(SQLModel, table=True):
    __tablename__ = "passkey_credentials"
    __table_args__ = (
        UniqueConstraint(
            "credential_id",
            name="uq_passkey_credential_id",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id",
        index=True,
        ondelete="CASCADE",
    )
    # WebAuthn credential ID (Base64URL)
    credential_id: str = Field(
        sa_type=String(2048),
    )
    # COSE public key (バイナリ)
    public_key: bytes = Field(
        sa_column=Column(LargeBinary, nullable=False),
    )
    sign_count: int = Field(default=0)
    device_type: str | None = Field(default=None)
    backed_up: bool = Field(default=False)

    # ユーザーが設定する端末名
    name: str | None = Field(
        default=None,
        max_length=100,
    )
    transports: str | None = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = Field(default=None)

    user: User = Relationship(back_populates="passkeys")
