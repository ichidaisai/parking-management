# ruff: noqa: PT009 - These tests use the standard-library unittest runner.

import hashlib
import json
import secrets
import tempfile
import unittest
from collections.abc import Iterator
from typing import Any, TypedDict
from unittest.mock import patch

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event
from sqlmodel import Session, create_engine, select
from webauthn.helpers import bytes_to_base64url

import main
from db import enable_foreign_keys, get_session
from db.auth import AuthChallenge, AuthenticationChallenge, AuthSession
from db.users import PasskeyCredential, User
from handlers import auth as auth_handlers
from lib import pk


class AuthenticatorOverrides(TypedDict, total=False):
    origin: str
    rp_id: str
    flags: int


class SoftwareAuthenticator:
    """Produces real ES256 WebAuthn responses without mocking verification."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.user_handle = ""

    @staticmethod
    def client_data(options: dict[str, Any], ceremony: str, origin: str) -> bytes:
        return json.dumps(
            {"type": ceremony, "challenge": options["challenge"], "origin": origin},
        ).encode()

    def registration(
        self,
        options: dict[str, Any],
        *,
        origin: str = "http://localhost:8000",
        rp_id: str = "localhost",
        flags: int = 0x45,
    ) -> dict[str, Any]:
        self.user_handle = options["user"]["id"]
        public = self.key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32), -3: public.y.to_bytes(32)},
        )
        authenticator_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + (0).to_bytes(4)
            + bytes(16)
            + len(self.credential_id).to_bytes(2)
            + self.credential_id
            + cose_key
        )
        attestation = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": authenticator_data},
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self.client_data(options, "webauthn.create", origin),
                ),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal"],
            },
        }

    def authentication(
        self,
        options: dict[str, Any],
        *,
        origin: str = "http://localhost:8000",
        rp_id: str = "localhost",
        sign_count: int = 1,
        flags: int = 0x05,
    ) -> dict[str, Any]:
        client_data = self.client_data(options, "webauthn.get", origin)
        authenticator_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + sign_count.to_bytes(4)
        )
        signature = self.key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(authenticator_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": self.user_handle,
            },
        }


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.engine = create_engine(
            f"sqlite:///{directory}/test.sqlite3",
            connect_args={"check_same_thread": False},
        )
        event.listen(self.engine, "connect", enable_foreign_keys)
        self.addCleanup(self.engine.dispose)
        self.enterContext(patch.object(main, "engine", self.engine))
        self.enterContext(patch.object(pk, "RP_ID", "localhost"))
        self.enterContext(patch.object(pk, "RP_ORIGIN", "http://localhost:8000"))
        self.enterContext(patch.object(pk, "COOKIE_SECURE", new=False))
        self.enterContext(patch.object(pk, "INVITATION_CODE", "test-invitation"))

        def test_session() -> Iterator[Session]:
            with Session(self.engine) as session:
                yield session

        main.app.dependency_overrides[get_session] = test_session
        self.addCleanup(main.app.dependency_overrides.clear)
        self.client = self.enterContext(
            TestClient(main.app, headers={"X-Invitation-Code": "test-invitation"}),
        )

    def registration_options(
        self,
        username: str = "alice",
        *,
        client: TestClient | None = None,
    ) -> Response:
        return (client or self.client).post(
            "/auth/user/register",
            json={"username": username, "invitation_code": "test-invitation"},
        )

    def register(self, username: str = "alice") -> SoftwareAuthenticator:
        authenticator = SoftwareAuthenticator()
        options = self.registration_options(username)
        self.assertEqual(options.status_code, 200)
        self.assertEqual(
            options.json()["authenticatorSelection"]["residentKey"], "required",
        )
        response = self.client.post(
            "/auth/user/register/verify",
            json=authenticator.registration(options.json()),
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303, response.text)
        return authenticator

    def login_options(self) -> dict[str, Any]:
        response = self.client.post("/auth/user/login")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_registration_login_and_logout(self) -> None:
        authenticator = self.register()
        profile = self.client.get("/auth/user/me")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["username"], "alice")
        old_token = self.client.cookies[auth_handlers.SESSION_COOKIE]
        options = self.login_options()
        self.assertEqual(options.get("allowCredentials", []), [])
        credential = authenticator.authentication(options)
        response = self.client.post("/auth/user/verify", json=credential)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(
            old_token, self.client.cookies[auth_handlers.SESSION_COOKIE],
        )
        with Session(self.engine) as session:
            user = session.exec(select(User)).one()
            passkey = session.exec(select(PasskeyCredential)).one()
            self.assertIsNone(user.email)
            self.assertEqual(passkey.user_id, user.id)
            self.assertEqual(passkey.sign_count, 1)
            self.assertIsNotNone(passkey.last_used_at)
            self.assertIsNone(session.get(AuthSession, pk.token_hash(old_token)))
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code,
            400,
        )
        token = self.client.cookies[auth_handlers.SESSION_COOKIE]
        self.assertEqual(self.client.post("/auth/user/logout").status_code, 204)
        self.client.cookies.set(auth_handlers.SESSION_COOKIE, token, path="/")
        self.assertEqual(self.client.get("/auth/user/me").status_code, 401)

    def test_parking_lot_values_are_shared_and_require_login(self) -> None:
        self.register()
        initial = self.client.get("/parking-lots")
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["parking-1"], {"general": 0})
        self.assertEqual(initial.json()["parking-4"], {"general": 0, "accessible": 0})
        self.assertEqual(initial.json()["campus-road"], {"general": 0})
        self.assertNotIn("gym-rear", initial.json())
        for body in [{"general": -1}, {}, {"accessible": 1}, {"count": 1}]:
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.put("/parking-lots/parking-1", json=body).status_code,
                    422,
                )
        self.assertEqual(
            self.client.put("/parking-lots/not-a-parking-lot", json={"general": 8}).status_code,
            404,
        )
        updated = self.client.put("/parking-lots/parking-1", json={"general": 47})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json(), {"general": 47})
        updated = self.client.put("/parking-lots/parking-4", json={"general": 5, "accessible": 2})
        self.assertEqual(updated.json(), {"general": 5, "accessible": 2})
        updated = self.client.put("/parking-lots/parking-4", json={"accessible": 1})
        self.assertEqual(updated.json(), {"general": 5, "accessible": 1})
        # 最大台数ちょうどは保存でき、超えると拒否する。最大台数が未定なら上限なし
        self.assertEqual(
            self.client.put("/parking-lots/library-rear", json={"general": 65, "accessible": 2}).status_code,
            200,
        )
        for parking_id, body in [
            ("parking-1", {"general": 66}),
            ("parking-4", {"accessible": 5}),
            ("library-rear", {"general": 0, "accessible": 3}),
        ]:
            with self.subTest(parking_id=parking_id, body=body):
                exceeded = self.client.put(f"/parking-lots/{parking_id}", json=body)
                self.assertEqual(exceeded.status_code, 422)
                self.assertEqual(exceeded.json()["detail"], "Exceeds parking capacity")
        self.assertEqual(self.client.get("/parking-lots").json()["library-rear"], {"general": 65, "accessible": 2})
        self.assertEqual(
            self.client.put("/parking-lots/campus-road", json={"general": 500}).status_code,
            200,
        )

        with TestClient(
            main.app,
            headers={"X-Invitation-Code": "test-invitation"},
        ) as other:
            options = self.registration_options("bob", client=other)
            authenticator = SoftwareAuthenticator()
            registration = other.post(
                "/auth/user/register/verify",
                json=authenticator.registration(options.json()),
                follow_redirects=False,
            )
            self.assertEqual(registration.status_code, 303)
            shared = other.get("/parking-lots")
            self.assertEqual(shared.status_code, 200)
            self.assertEqual(shared.json()["parking-1"], {"general": 47})

        with TestClient(main.app) as guest:
            self.assertEqual(guest.get("/parking-lots").status_code, 401)
            public = guest.get("/public/parking-lots")
            self.assertEqual(public.status_code, 200)
            self.assertEqual(public.json()["parking-1"], {"general": 47})
            self.assertEqual(public.json()["parking-4"], {"general": 5, "accessible": 1})
            self.assertEqual(public.headers["access-control-allow-origin"], "*")
            self.assertEqual(
                guest.put("/parking-lots/parking-1", json={"general": 9}).status_code,
                401,
            )

    def test_username_validation(self) -> None:
        for username in ["alice@example.com", "", "two words", "a" * 101]:
            with self.subTest(username=username):
                response = self.registration_options(username)
                self.assertEqual(response.status_code, 422)
        self.register("ユーザー_01")

    def test_registration_requires_valid_origin_rp_and_user_verification(self) -> None:
        cases: list[AuthenticatorOverrides] = [
            {"origin": "https://evil.example"},
            {"rp_id": "evil.example"},
            {"flags": 0x41},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                options = self.registration_options().json()
                credential = SoftwareAuthenticator().registration(options, **changes)
                self.assertEqual(
                    self.client.post(
                        "/auth/user/register/verify",
                        json=credential,
                    ).status_code,
                    400,
                )
                with Session(self.engine) as session:
                    self.assertEqual(session.exec(select(User)).all(), [])
                    self.assertEqual(session.exec(select(PasskeyCredential)).all(), [])

    def test_challenge_is_bound_to_browser_and_used_once(self) -> None:
        options = self.registration_options().json()
        credential = SoftwareAuthenticator().registration(options)
        with TestClient(
            main.app,
            headers={"X-Invitation-Code": "test-invitation"},
        ) as other:
            response = other.post("/auth/user/register/verify", json=credential)
            self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/auth/user/register/verify",
            json=credential,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/register/complete.html")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertEqual(
            self.client.post("/auth/user/register/verify", json=credential).status_code,
            400,
        )

    def test_expired_challenge(self) -> None:
        options = self.registration_options().json()
        credential = SoftwareAuthenticator().registration(options)
        with Session(self.engine) as session:
            challenge = session.exec(select(AuthChallenge)).one()
            challenge.expires_at = 0
            session.add(challenge)
            session.commit()
        self.assertEqual(
            self.client.post("/auth/user/register/verify", json=credential).status_code,
            400,
        )

    def test_duplicate_passkey_rolls_back_new_user(self) -> None:
        authenticator = self.register()
        options = self.registration_options("bob").json()
        response = self.client.post(
            "/auth/user/register/verify",
            json=authenticator.registration(options),
        )
        self.assertEqual(response.status_code, 409)
        with Session(self.engine) as session:
            self.assertEqual(
                [user.username for user in session.exec(select(User)).all()],
                ["alice"],
            )

    def test_concurrent_pending_username_registration(self) -> None:
        with TestClient(
            main.app,
            headers={"X-Invitation-Code": "test-invitation"},
        ) as other:
            options = self.registration_options().json()
            other_options = self.registration_options(client=other).json()
            response = self.client.post(
                "/auth/user/register/verify",
                json=SoftwareAuthenticator().registration(options),
                follow_redirects=False,
            )
            self.assertEqual(response.status_code, 303)
            response = other.post(
                "/auth/user/register/verify",
                json=SoftwareAuthenticator().registration(other_options),
            )
            self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self.registration_options().status_code,
            409,
        )

    def test_login_identifies_selected_account(self) -> None:
        alice = self.register()
        bob = self.register("bob")
        self.client.post("/auth/user/logout")
        for authenticator, username in [(alice, "alice"), (bob, "bob")]:
            response = self.client.post(
                "/auth/user/verify",
                json=authenticator.authentication(self.login_options()),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["username"], username)
            self.assertEqual(
                self.client.get("/auth/user/me").json()["username"], username,
            )
            self.client.post("/auth/user/logout")

    def test_login_rejects_wrong_or_missing_user_handle(self) -> None:
        alice = self.register()
        bob = self.register("bob")
        for user_handle in [bob.user_handle, None, ""]:
            credential = alice.authentication(self.login_options())
            credential["response"]["userHandle"] = user_handle
            self.assertEqual(
                self.client.post("/auth/user/verify", json=credential).status_code,
                403,
            )

    def test_login_rejects_unknown_credential(self) -> None:
        authenticator = self.register()
        unknown = SoftwareAuthenticator()
        unknown.user_handle = authenticator.user_handle
        response = self.client.post(
            "/auth/user/verify",
            json=unknown.authentication(self.login_options()),
        )
        self.assertEqual(response.status_code, 403)

    def test_login_rejects_invalid_origin_rp_uv_and_signature(self) -> None:
        authenticator = self.register()
        cases: list[AuthenticatorOverrides] = [
            {"origin": "https://evil.example"},
            {"rp_id": "evil.example"},
            {"flags": 0x01},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                credential = authenticator.authentication(
                    self.login_options(),
                    **changes,
                )
                self.assertEqual(
                    self.client.post("/auth/user/verify", json=credential).status_code,
                    403,
                )
        credential = authenticator.authentication(self.login_options())
        credential["response"]["signature"] = bytes_to_base64url(b"invalid signature")
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code,
            403,
        )
        with Session(self.engine) as session:
            self.assertEqual(
                session.exec(select(PasskeyCredential)).one().sign_count,
                0,
            )

    def test_inactive_user_cannot_authenticate(self) -> None:
        authenticator = self.register()
        credential = authenticator.authentication(self.login_options())
        with Session(self.engine) as session:
            user = session.exec(select(User)).one()
            user.is_active = False
            session.add(user)
            session.commit()
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code,
            403,
        )
        self.assertEqual(self.client.get("/auth/user/me").status_code, 401)

    def test_login_options_do_not_reveal_registered_accounts(self) -> None:
        options = self.login_options()
        self.assertNotIn("user", options)
        self.assertEqual(options.get("allowCredentials", []), [])
        self.assertEqual(options["userVerification"], "required")

    def test_login_challenge_expires_and_is_bound_to_browser(self) -> None:
        authenticator = self.register()
        credential = authenticator.authentication(self.login_options())
        with TestClient(main.app) as other:
            self.assertEqual(
                other.post("/auth/user/verify", json=credential).status_code, 400,
            )
        with Session(self.engine) as session:
            challenge = session.exec(select(AuthenticationChallenge)).one()
            challenge.expires_at = 0
            session.add(challenge)
            session.commit()
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code, 400,
        )

    def test_session_expires(self) -> None:
        self.register()
        with Session(self.engine) as session:
            login_session = session.exec(select(AuthSession)).one()
            login_session.expires_at = 0
            session.add(login_session)
            session.commit()
        self.assertEqual(self.client.get("/auth/user/me").status_code, 401)

    def test_missing_and_malformed_credentials(self) -> None:
        self.assertEqual(self.client.get("/auth/user/me").status_code, 401)
        self.assertEqual(
            self.client.post("/auth/user/verify", json={}).status_code,
            400,
        )
        self.register()
        for credential in [{}, {"id": 123}, {"id": "abc", "response": []}]:
            with self.subTest(credential=credential):
                self.login_options()
                self.assertEqual(
                    self.client.post("/auth/user/verify", json=credential).status_code,
                    403,
                )

    def test_signature_counter_must_advance(self) -> None:
        authenticator = self.register()
        credential = authenticator.authentication(self.login_options(), sign_count=1)
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code,
            200,
        )
        credential = authenticator.authentication(self.login_options(), sign_count=1)
        self.assertEqual(
            self.client.post("/auth/user/verify", json=credential).status_code,
            403,
        )

    def test_invitation_is_required_before_issuing_registration_options(self) -> None:
        for data in [
            {"username": "alice"},
            {"username": "alice", "invitation_code": ""},
            {"username": "alice", "invitation_code": "wrong-code"},
        ]:
            with self.subTest(data=data):
                response = self.client.post("/auth/user/register", json=data)
                self.assertIn(response.status_code, [403, 422])
                self.assertNotIn("challenge", response.json())
        self.assertIn(
            self.client.get(
                "/auth/user/register",
                params={"username": "alice"},
            ).status_code,
            [404, 405],
        )
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(AuthChallenge)).all(), [])
            self.assertEqual(session.exec(select(User)).all(), [])
            self.assertEqual(session.exec(select(PasskeyCredential)).all(), [])

    def test_invitation_is_checked_again_before_saving_account(self) -> None:
        options = self.registration_options().json()
        credential = SoftwareAuthenticator().registration(options)
        self.client.headers.pop("X-Invitation-Code")
        for headers in [{}, {"X-Invitation-Code": "wrong-code"}]:
            response = self.client.post(
                "/auth/user/register/verify",
                json=credential,
                headers=headers,
            )
            self.assertEqual(response.status_code, 403)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(User)).all(), [])
            self.assertEqual(session.exec(select(PasskeyCredential)).all(), [])
        response = self.client.post(
            "/auth/user/register/verify",
            json=credential,
            headers={"X-Invitation-Code": "test-invitation"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

    def test_missing_invitation_configuration_disables_registration(self) -> None:
        options = self.registration_options().json()
        credential = SoftwareAuthenticator().registration(options)
        with patch.object(pk, "INVITATION_CODE", ""):
            self.assertEqual(self.registration_options().status_code, 503)
            self.assertEqual(
                self.client.post(
                    "/auth/user/register/verify",
                    json=credential,
                ).status_code,
                503,
            )
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(User)).all(), [])
            self.assertEqual(session.exec(select(PasskeyCredential)).all(), [])

    def test_invitation_rotation_invalidates_old_code(self) -> None:
        options = self.registration_options().json()
        credential = SoftwareAuthenticator().registration(options)
        with patch.object(pk, "INVITATION_CODE", "new-invitation"):
            self.assertEqual(
                self.client.post(
                    "/auth/user/register/verify",
                    json=credential,
                ).status_code,
                403,
            )
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(User)).all(), [])

    def test_invitation_can_be_reused_for_multiple_accounts(self) -> None:
        self.register("alice")
        self.register("bob")
        self.assertEqual(pk.INVITATION_CODE, "test-invitation")
        with Session(self.engine) as session:
            self.assertEqual(
                {user.username for user in session.exec(select(User)).all()},
                {"alice", "bob"},
            )
            self.assertEqual(len(session.exec(select(PasskeyCredential)).all()), 2)

    def test_web_pages_and_api_routing(self) -> None:
        for path in [
            "/",
            "/register/",
            "/register/index.html",
            "/js/auth.mjs",
            "/auth.css",
            "/lib/dads-components-bundle.css",
            "/images/thumb.jpeg",
            "/images/favicon.ico",
            "/images/touch-icon.png",
            "/docs",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        for path in [
            "/mypage",
            "/mypage/",
            "/mypage/index.html",
            "/register/complete.html",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
        self.register()
        for path in [
            "/mypage",
            "/mypage/",
            "/mypage/index.html",
            "/register/complete.html",
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
        self.client.post("/auth/user/logout")
        self.assertEqual(
            self.client.get("/mypage/", follow_redirects=False).status_code,
            303,
        )


if __name__ == "__main__":
    unittest.main()
