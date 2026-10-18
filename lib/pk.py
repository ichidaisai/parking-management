import hashlib
import json
import os
import secrets
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import (
    bytes_to_base64url,
    options_to_json,
    parse_authentication_credential_json,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from db.auth import AuthChallenge, AuthenticationChallenge, AuthSession
from db.users import PasskeyCredential, User, utcnow

RP_ID = os.getenv("RP_ID", "localhost")
RP_NAME = os.getenv("RP_NAME", "FastAPI Passkey")
RP_ORIGIN = os.getenv("RP_ORIGIN", "http://localhost:8000")
INVITATION_CODE = os.getenv("INVITATION_CODE", "")
COOKIE_SECURE = RP_ORIGIN.startswith("https://")
CHALLENGE_TTL = 300
SESSION_TTL = 86400


class AuthError(Exception):
    """An authentication failure for the HTTP handler to report."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_invitation_code(code: str | None) -> None:
    if not INVITATION_CODE:
        raise AuthError(503, "Registration is disabled")
    if not code or not secrets.compare_digest(
        token_hash(code), token_hash(INVITATION_CODE),
    ):
        raise AuthError(403, "Invalid invitation code")


def save_challenge(
    session: Session,
    challenge: bytes,
    *,
    user: User | None = None,
    old_token: str | None = None,
) -> str:
    now = int(time.time())
    session.exec(delete(AuthChallenge).where(col(AuthChallenge.expires_at) <= now))
    session.exec(
        delete(AuthenticationChallenge).where(
            col(AuthenticationChallenge.expires_at) <= now,
        ),
    )
    session.exec(delete(AuthSession).where(col(AuthSession.expires_at) <= now))
    if old_token:
        session.exec(
            delete(AuthChallenge).where(
                col(AuthChallenge.token_hash) == token_hash(old_token),
            ),
        )
        session.exec(
            delete(AuthenticationChallenge).where(
                col(AuthenticationChallenge.token_hash) == token_hash(old_token),
            ),
        )
    token = secrets.token_urlsafe(32)
    if user is None:
        session.add(
            AuthenticationChallenge(
                token_hash=token_hash(token),
                challenge=challenge,
                expires_at=now + CHALLENGE_TTL,
            ),
        )
    else:
        session.add(
            AuthChallenge(
                token_hash=token_hash(token),
                purpose="registration",
                challenge=challenge,
                user_id=user.id,
                username=user.username,
                display_name=user.display_name,
                expires_at=now + CHALLENGE_TTL,
            ),
        )
    session.commit()
    return token


def consume_challenge[T: AuthChallenge | AuthenticationChallenge](
    session: Session,
    token: str | None,
    model: type[T],
) -> T:
    if not token:
        raise AuthError(400, "Missing challenge")
    # DELETE RETURNING consumes the challenge atomically, even across workers.
    challenge = session.exec(
        delete(model)
        .where(col(model.token_hash) == token_hash(token))
        .returning(model),
    ).scalar_one_or_none()
    if challenge is not None:
        session.expunge(challenge)
    session.commit()
    if (
        challenge is None
        or (
            isinstance(challenge, AuthChallenge) and challenge.purpose != "registration"
        )
        or challenge.expires_at <= int(time.time())
    ):
        raise AuthError(400, "Invalid or expired challenge")
    return challenge


def create_login_session(
    session: Session, user: User, old_token: str | None = None,
) -> str:
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
    return token


def get_authenticated_user(session: Session, token: str | None) -> User | None:
    login_session = session.get(AuthSession, token_hash(token)) if token else None
    if login_session is None or login_session.expires_at <= int(time.time()):
        return None
    user = session.get(User, login_session.user_id)
    if user is None or not user.is_active:
        return None
    return user


def revoke_login_session(session: Session, token: str | None) -> None:
    if token:
        session.exec(
            delete(AuthSession).where(col(AuthSession.token_hash) == token_hash(token)),
        )
        session.commit()


def begin_registration(
    session: Session,
    username: str,
    invitation_code: str,
    *,
    display_name: str | None = None,
    old_token: str | None = None,
) -> tuple[dict[str, Any], str]:
    check_invitation_code(invitation_code)
    if session.exec(select(User).where(User.username == username)).first():
        raise AuthError(409, "Username already exists")
    user = User(id=uuid4(), username=username, display_name=display_name)
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
    token = save_challenge(session, options.challenge, user=user, old_token=old_token)
    return json.loads(options_to_json(options)), token


def complete_registration(
    session: Session,
    credential: dict[str, Any],
    *,
    invitation_code: str | None,
    challenge_token: str | None,
) -> User:
    check_invitation_code(invitation_code)
    challenge = consume_challenge(session, challenge_token, AuthChallenge)
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge.challenge,
            expected_rp_id=RP_ID,
            expected_origin=RP_ORIGIN,
            require_user_verification=True,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise AuthError(400, "Invalid registration credential") from exc
    user = User(
        id=challenge.user_id,
        username=challenge.username,
        display_name=challenge.display_name,
    )
    try:
        session.add(user)
        session.flush()
        # Commit the user and its first passkey in one transaction.
        save_passkey(session, user, verification, credential)
    except IntegrityError as exc:
        session.rollback()
        raise AuthError(409, "Username or passkey already exists") from exc
    return user


def begin_authentication(
    session: Session,
    old_token: str | None = None,
) -> tuple[dict[str, Any], str]:
    options = generate_authentication_options(
        rp_id=RP_ID,
        timeout=CHALLENGE_TTL * 1000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    token = save_challenge(session, options.challenge, old_token=old_token)
    return json.loads(options_to_json(options)), token


def complete_authentication(
    session: Session,
    credential: dict[str, Any],
    challenge_token: str | None,
) -> User:
    challenge = consume_challenge(session, challenge_token, AuthenticationChallenge)
    try:
        return verify_passkey_login(
            session,
            credential,
            challenge.challenge,
            expected_rp_id=RP_ID,
            expected_origin=RP_ORIGIN,
        )
    except (WebAuthnException, ValueError, KeyError, TypeError) as exc:
        session.rollback()
        raise AuthError(403, "Invalid authentication credential") from exc


def save_passkey(
    session: Session,
    user: User,
    verification: VerifiedRegistration,
    credential: dict[str, Any],
) -> PasskeyCredential:
    passkey = PasskeyCredential(
        user_id=user.id,
        credential_id=bytes_to_base64url(verification.credential_id),
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        device_type=verification.credential_device_type.value,
        backed_up=verification.credential_backed_up,
        transports=json.dumps(credential.get("response", {}).get("transports", [])),
    )

    session.add(passkey)
    session.commit()
    session.refresh(passkey)

    return passkey


def verify_passkey_login(
    session: Session,
    credential: dict[str, Any],
    challenge: bytes,
    *,
    expected_rp_id: str,
    expected_origin: str,
) -> User:
    parsed_credential = parse_authentication_credential_json(credential)
    credential_id = bytes_to_base64url(parsed_credential.raw_id)

    passkey = session.exec(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == credential_id,
        ),
    ).first()

    if passkey is None:
        msg = "Unknown passkey"
        raise ValueError(msg)

    user = session.get(User, passkey.user_id)
    if user is None or not user.is_active:
        msg = "User is inactive or missing"
        raise ValueError(msg)

    user_handle = parsed_credential.response.user_handle
    # Discoverable credentials must return the user handle for the selected account.
    if user_handle != user.id.bytes:
        msg = "User handle does not match the account"
        raise ValueError(msg)

    verification = verify_authentication_response(
        credential=parsed_credential,
        expected_challenge=challenge,
        expected_rp_id=expected_rp_id,
        expected_origin=expected_origin,
        credential_public_key=passkey.public_key,
        credential_current_sign_count=passkey.sign_count,
        require_user_verification=True,
    )

    passkey.sign_count = verification.new_sign_count
    passkey.device_type = verification.credential_device_type.value
    passkey.backed_up = verification.credential_backed_up
    passkey.last_used_at = utcnow()

    session.add(passkey)
    session.commit()

    return user
