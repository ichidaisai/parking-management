from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, SQLModel

# Load project settings before importing db, which creates the engine on import.
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

from db import engine, get_session  # noqa: E402
from handlers.auth import SESSION_COOKIE, auth  # noqa: E402
from lib import pk  # noqa: E402

WEB_DIR = Path(__file__).resolve().parent / "web"

type SessionDep = Annotated[Session, Depends(get_session)]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    SQLModel.metadata.create_all(engine)
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth)


@app.get("/mypage", include_in_schema=False, response_model=None)
@app.get("/mypage/", include_in_schema=False, response_model=None)
@app.get("/mypage/index.html", include_in_schema=False, response_model=None)
def mypage(request: Request, session: SessionDep) -> FileResponse | RedirectResponse:
    if pk.get_authenticated_user(session, request.cookies.get(SESSION_COOKIE)) is None:
        return RedirectResponse(
            url="/",
            status_code=303,
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(
        WEB_DIR / "mypage/index.html",
        headers={"Cache-Control": "no-store"},
    )


# API and protected page routes must precede the catch-all static mount.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="static")
