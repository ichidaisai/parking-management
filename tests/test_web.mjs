import assert from "node:assert/strict";
import { test } from "node:test";
import {
  authenticationOptions, creationOptions, credentialJSON,
  decodeBase64URL, encodeBase64URL, login, registerAccount,
} from "../web/js/auth.mjs";

const options = {
  challenge: "AP_-AQ",
  rp: { id: "localhost", name: "Test" },
  user: { id: "AAEC", name: "alice", displayName: "alice" },
  pubKeyCredParams: [{ alg: -7, type: "public-key" }],
  allowCredentials: [{ id: "AP_-AQ", type: "public-key" }],
};

function credential() {
  return {
    id: "AAEC", rawId: new Uint8Array([0, 1, 2]).buffer, type: "public-key",
    response: {
      clientDataJSON: new Uint8Array([1, 2]).buffer,
      attestationObject: new Uint8Array([3, 4]).buffer,
      getTransports: () => ["internal"],
    },
    getClientExtensionResults: () => ({}),
  };
}

test("Base64URL and WebAuthn options preserve binary values", () => {
  assert.equal(encodeBase64URL(decodeBase64URL("AP_-AQ")), "AP_-AQ");
  assert.deepEqual([...creationOptions(options).user.id], [0, 1, 2]);
  assert.deepEqual([...authenticationOptions(options).allowCredentials[0].id], [0, 255, 254, 1]);
  assert.equal(options.user.id, "AAEC");
});

test("registration and assertion credentials serialize binary responses", () => {
  assert.deepEqual(credentialJSON(credential()).response, {
    clientDataJSON: "AQI", attestationObject: "AwQ", transports: ["internal"],
  });
  const assertion = credential();
  assertion.response = {
    clientDataJSON: new Uint8Array([1]).buffer,
    authenticatorData: new Uint8Array([2]).buffer,
    signature: new Uint8Array([3]).buffer,
    userHandle: null,
  };
  assert.equal(credentialJSON(assertion).response.signature, "Aw");
  assert.equal(credentialJSON(assertion).response.userHandle, null);
  assert.throws(() => credentialJSON(null));
});

function browser(t) {
  function global(name, value) {
    const original = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, value });
    t.after(() => {
      if (original) Object.defineProperty(globalThis, name, original);
      else delete globalThis[name];
    });
  }
  global("isSecureContext", true);
  global("PublicKeyCredential", class {});
  const calls = [];
  const credentials = {
    create: async () => { calls.push("create"); return credential(); },
    get: async () => { calls.push("get"); return credential(); },
  };
  global("navigator", { credentials });
  global("window", { location: { assign: (path) => calls.push(path) } });
  return calls;
}

test("wrong invitation never opens passkey creation or verification", async (t) => {
  const calls = browser(t);
  t.mock.method(globalThis, "fetch", async (path) => {
    calls.push(path);
    return new Response(JSON.stringify({ detail: "Invalid invitation code" }), { status: 403 });
  });
  await assert.rejects(registerAccount("alice", "wrong"), /招待コードが一致しません/);
  assert.deepEqual(calls, ["/auth/user/register"]);
});

test("registration sends invitation in POST and header then opens completion", async (t) => {
  const calls = browser(t);
  t.mock.method(globalThis, "fetch", async (path, request) => {
    calls.push(path);
    assert.equal(request.method, "POST");
    assert.equal(request.credentials, "same-origin");
    if (path === "/auth/user/register") {
      assert.deepEqual(JSON.parse(request.body), { username: "alice", invitation_code: "invite" });
      return Response.json(options);
    }
    assert.equal(request.headers["X-Invitation-Code"], "invite");
    assert.equal(JSON.parse(request.body).response.attestationObject, "AwQ");
    return new Response("complete");
  });
  await registerAccount("alice", "invite");
  assert.deepEqual(calls, ["/auth/user/register", "create", "/auth/user/register/verify", "/register/complete.html"]);
});

test("failed server verification prevents completion navigation", async (t) => {
  const calls = browser(t);
  t.mock.method(globalThis, "fetch", async (path) => {
    if (path === "/auth/user/register") return Response.json(options);
    return new Response(JSON.stringify({ detail: "Invalid invitation code" }), { status: 403 });
  });
  await assert.rejects(registerAccount("alice", "invite"));
  assert.deepEqual(calls, ["create"]);
});

test("login needs no nickname and verifies the assertion before opening mypage", async (t) => {
  const calls = browser(t);
  t.mock.method(globalThis, "fetch", async (path, request) => {
    calls.push(path);
    if (path === "/auth/user/login") {
      assert.equal(request.method, "POST");
      assert.equal(request.body, undefined);
      return Response.json({ challenge: options.challenge, rpId: "localhost", allowCredentials: [] });
    }
    return Response.json({ username: "alice" });
  });
  await login();
  assert.deepEqual(calls, ["/auth/user/login", "get", "/auth/user/verify", "/mypage/"]);
});
