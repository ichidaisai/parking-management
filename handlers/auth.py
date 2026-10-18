from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlmodel import Session

from db import get_session
from db.users import User
from lib import pk

CHALLENGE_COOKIE = "passkey_challenge"
SESSION_COOKIE = "passkey_session"

type SessionDep = Annotated[Session, Depends(get_session)]


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    invitation_code: SecretStr = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None


auth = APIRouter(prefix="/auth", tags=["auth"])


def set_cookie(response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        httponly=True,
        secure=pk.COOKIE_SECURE,
        samesite="strict",
        path="/" if name == SESSION_COOKIE else "/auth",
    )
    response.headers["Cache-Control"] = "no-store"


def set_login_session(
    session: Session,
    request: Request,
    response: Response,
    user: User,
) -> None:
    token = pk.create_login_session(session, user, request.cookies.get(SESSION_COOKIE))
    set_cookie(response, SESSION_COOKIE, token, pk.SESSION_TTL)
    response.delete_cookie(SESSION_COOKIE, path="/auth")
    response.delete_cookie(CHALLENGE_COOKIE, path="/auth")


@auth.post("/user/register")
def create_user(
    data: RegistrationRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> dict[str, Any]:
    """招待コードを照合し、パスキー登録を開始する。"""
    try:
        options, token = pk.begin_registration(
            session,
            data.username,
            data.invitation_code.get_secret_value(),
            display_name=data.display_name,
            old_token=request.cookies.get(CHALLENGE_COOKIE),
        )
    except pk.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    set_cookie(response, CHALLENGE_COOKIE, token, pk.CHALLENGE_TTL)
    return options


@auth.post("/user/register/verify", response_class=RedirectResponse, status_code=303)
def create_verify_user(
    credential: dict[str, Any],
    request: Request,
    session: SessionDep,
    invitation_code: Annotated[str | None, Header(alias="X-Invitation-Code")] = None,
) -> RedirectResponse:
    """登録を検証し、ログインして完了画面へ進む。"""
    try:
        user = pk.complete_registration(
            session,
            credential,
            invitation_code=invitation_code,
            challenge_token=request.cookies.get(CHALLENGE_COOKIE),
        )
    except pk.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    response = RedirectResponse(url="/register/complete.html", status_code=303)
    set_login_session(session, request, response, user)
    return response


@auth.post("/user/login")
def login_user(
    request: Request,
    response: Response,
    session: SessionDep,
) -> dict[str, Any]:
    """ニックネームの入力なしでパスキー認証を開始する。"""
    options, token = pk.begin_authentication(
        session, request.cookies.get(CHALLENGE_COOKIE),
    )
    set_cookie(response, CHALLENGE_COOKIE, token, pk.CHALLENGE_TTL)
    return options


@auth.post("/user/verify")
def login_verify_user(
    credential: dict[str, Any],
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserResponse:
    """パスキー認証を検証し、ログインCookieを発行する。"""
    try:
        user = pk.complete_authentication(
            session, credential, request.cookies.get(CHALLENGE_COOKIE),
        )
    except pk.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    set_login_session(session, request, response, user)
    return UserResponse.model_validate(user)


@auth.get("/user/me")
def current_user(
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserResponse:
    user = pk.get_authenticated_user(session, request.cookies.get(SESSION_COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


@auth.post("/user/logout", status_code=204)
def logout_user(request: Request, response: Response, session: SessionDep) -> None:
    pk.revoke_login_session(session, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(SESSION_COOKIE, path="/auth")
    response.headers["Cache-Control"] = "no-store"
