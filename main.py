import hashlib
import json
import os
import secrets
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, col, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from db import engine, get_session
from db.auth import AuthChallenge, AuthSession
from db.users import PasskeyCredential, User
from lib.pk import save_passkey, verify_passkey_login

RP_ID = os.getenv("RP_ID", "localhost")
RP_NAME = os.getenv("RP_NAME", "FastAPI Passkey")
RP_ORIGIN = os.getenv("RP_ORIGIN", "http://localhost:8000")
COOKIE_SECURE = RP_ORIGIN.startswith("https://")
CHALLENGE_COOKIE = "passkey_challenge"
SESSION_COOKIE = "passkey_session"
CHALLENGE_TTL = 300
SESSION_TTL = 86400

type SessionDep = Annotated[Session, Depends(get_session)]


class UsernameRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: str = Field(min_length=1, max_length=100, pattern=r"^[\w.-]+$")


class RegistrationRequest(UsernameRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    SQLModel.metadata.create_all(engine)
    yield


auth = APIRouter(prefix="/auth", tags=["auth"])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_cookie(response: Response, name: str, token: str, max_age: int) -> None:
    response.set_cookie(
        name,
        token,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/auth",
    )
    response.headers["Cache-Control"] = "no-store"


def save_challenge(  # noqa: PLR0913 - Flow metadata and HTTP/DB contexts are required.
    session: Session,
    request: Request,
    response: Response,
    *,
    purpose: str,
    challenge: bytes,
    user: User,
) -> None:
    now = int(time.time())
    session.exec(delete(AuthChallenge).where(col(AuthChallenge.expires_at) <= now))
    session.exec(delete(AuthSession).where(col(AuthSession.expires_at) <= now))
    old_token = request.cookies.get(CHALLENGE_COOKIE)
    if old_token:
        session.exec(
            delete(AuthChallenge).where(
                col(AuthChallenge.token_hash) == token_hash(old_token),
            ),
        )
    token = secrets.token_urlsafe(32)
    session.add(
        AuthChallenge(
            token_hash=token_hash(token),
            purpose=purpose,
            challenge=challenge,
            user_id=user.id,
            username=user.username,
            display_name=user.display_name,
            expires_at=now + CHALLENGE_TTL,
        ),
    )
    session.commit()
    set_cookie(response, CHALLENGE_COOKIE, token, CHALLENGE_TTL)


def consume_challenge(
    session: Session,
    request: Request,
    purpose: str,
) -> AuthChallenge:
    token = request.cookies.get(CHALLENGE_COOKIE)
    if not token:
        raise HTTPException(status_code=400, detail="Missing challenge")
    # DELETE RETURNING consumes the challenge atomically, even across workers.
    challenge = session.exec(
        delete(AuthChallenge)
        .where(col(AuthChallenge.token_hash) == token_hash(token))
        .returning(AuthChallenge),
    ).scalar_one_or_none()
    if challenge is not None:
        # Preserve the returned data after committing the deletion.
        session.expunge(challenge)
    session.commit()
    if (
        challenge is None
        or challenge.purpose != purpose
        or challenge.expires_at <= int(time.time())
    ):
        raise HTTPException(status_code=400, detail="Invalid or expired challenge")
    return challenge


def create_login_session(
    session: Session,
    request: Request,
    response: Response,
    user: User,
) -> None:
    old_token = request.cookies.get(SESSION_COOKIE)
    if old_token:
        session.exec(
            delete(AuthSession).where(
                col(AuthSession.token_hash) == token_hash(old_token),
            ),
        )
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=int(time.time()) + SESSION_TTL,
        ),
    )
    session.commit()
    set_cookie(response, SESSION_COOKIE, token, SESSION_TTL)
    response.delete_cookie(CHALLENGE_COOKIE, path="/auth")


@auth.get("/user/register")
def create_user(
    data: Annotated[RegistrationRequest, Query()],
    request: Request,
    response: Response,
    session: SessionDep,
) -> dict[str, Any]:
    """ユーザー登録用のパスキーオプションを生成する。"""
    if session.exec(select(User).where(User.username == data.username)).first():
        raise HTTPException(status_code=409, detail="Username already exists")
    user = User(id=uuid4(), username=data.username, display_name=data.display_name)
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user.id.bytes,
        user_name=user.username,
        user_display_name=user.display_name or user.username,
        timeout=CHALLENGE_TTL * 1000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    save_challenge(
        session,
        request,
        response,
        purpose="registration",
        challenge=options.challenge,
        user=user,
    )
    return json.loads(options_to_json(options))


@auth.post("/user/register/verify", response_class=RedirectResponse, status_code=303)
def create_verify_user(
    credential: dict[str, Any],
    request: Request,
    session: SessionDep,
) -> RedirectResponse:
    """パスキー検証成功後にユーザーを作成し、ログインしてリダイレクトする。"""
    challenge = consume_challenge(session, request, "registration")
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=RP_ID,
            expected_origin=RP_ORIGIN,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid registration credential",
        ) from exc

    user = User(
        id=challenge.user_id,
        username=challenge.username,
        display_name=challenge.display_name,
    )
    try:
        session.add(user)
        session.flush()
        # The helper commits both the user and its first passkey in one transaction.
        save_passkey(session, user, verification, credential)
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Username or passkey already exists",
        ) from exc

    response = RedirectResponse(url="/auth/user/me", status_code=303)
    create_login_session(session, request, response, user)
    return response


@auth.post("/user/login")
def login_user(
    data: UsernameRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> dict[str, Any]:
    """ユーザー名に対応するパスキーの認証オプションを生成する。"""
    user = session.exec(select(User).where(User.username == data.username)).first()
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="User or passkey unavailable")
    passkeys = session.exec(
        select(PasskeyCredential).where(PasskeyCredential.user_id == user.id),
    ).all()
    if not passkeys:
        raise HTTPException(status_code=403, detail="User or passkey unavailable")
    options = generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
            for passkey in passkeys
        ],
        timeout=CHALLENGE_TTL * 1000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    save_challenge(
        session,
        request,
        response,
        purpose="authentication",
        challenge=options.challenge,
        user=user,
    )
    return json.loads(options_to_json(options))


@auth.post("/user/verify")
def login_verify_user(
    credential: dict[str, Any],
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserResponse:
    """署名・チャレンジ・ユーザーを検証し、ログインセッションを発行する。"""
    challenge = consume_challenge(session, request, "authentication")
    try:
        user = verify_passkey_login(
            session,
            credential,
            challenge.challenge,
            expected_rp_id=RP_ID,
            expected_origin=RP_ORIGIN,
            expected_user_id=challenge.user_id,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=403,
            detail="Invalid authentication credential",
        ) from exc
    create_login_session(session, request, response, user)
    return UserResponse.model_validate(user)


@auth.get("/user/me")
def current_user(
    request: Request,
    response: Response,
    session: SessionDep,
) -> UserResponse:
    token = request.cookies.get(SESSION_COOKIE)
    login_session = session.get(AuthSession, token_hash(token)) if token else None
    if login_session is None or login_session.expires_at <= int(time.time()):
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = session.get(User, login_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    response.headers["Cache-Control"] = "no-store"
    return UserResponse.model_validate(user)


@auth.post("/user/logout", status_code=204)
def logout_user(request: Request, response: Response, session: SessionDep) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session.exec(
            delete(AuthSession).where(col(AuthSession.token_hash) == token_hash(token)),
        )
        session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/auth")
    response.headers["Cache-Control"] = "no-store"


app = FastAPI(lifespan=lifespan)
app.include_router(auth)
