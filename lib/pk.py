import json
from typing import Any

from sqlmodel import Session, select
from webauthn import verify_authentication_response
from webauthn.helpers import bytes_to_base64url, parse_authentication_credential_json
from webauthn.registration.verify_registration_response import VerifiedRegistration

from db.users import PasskeyCredential, User, utcnow


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
