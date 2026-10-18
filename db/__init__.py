import os
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import event
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import Session, create_engine

database_url = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{Path(__file__).resolve().parent.parent / 'db.sqlite3'}",
)
if not database_url.startswith("sqlite:"):
    msg = "DATABASE_URL must use SQLite"
    raise ValueError(msg)

engine = create_engine(database_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def enable_foreign_keys(connection: Connection, _record: ConnectionPoolEntry) -> None:
    connection.execute("PRAGMA foreign_keys=ON")


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
