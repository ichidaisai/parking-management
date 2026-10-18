from uuid import UUID

from sqlmodel import Field, SQLModel


class AuthChallenge(SQLModel, table=True):
    __tablename__ = "auth_challenges"

    token_hash: str = Field(primary_key=True, max_length=64)
    purpose: str
    challenge: bytes
    user_id: UUID
    username: str
    display_name: str | None = None
    expires_at: int = Field(index=True)


class AuthSession(SQLModel, table=True):
    __tablename__ = "auth_sessions"

    token_hash: str = Field(primary_key=True, max_length=64)
    user_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    expires_at: int = Field(index=True)


class AuthenticationChallenge(SQLModel, table=True):
    """A login challenge issued before the account is known."""

    __tablename__ = "authentication_challenges"

    token_hash: str = Field(primary_key=True, max_length=64)
    challenge: bytes
    expires_at: int = Field(index=True)
