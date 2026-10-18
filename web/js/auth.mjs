const messages = {
  "Invalid invitation code": "招待コードが一致しません。確認してもう一度入力してください。",
  "Registration is disabled": "現在、新規登録を受け付けていません。管理者にお問い合わせください。",
  "Username already exists": "このニックネームは使用されています。別の名前を入力してください。",
  "Username or passkey already exists": "このニックネームまたはパスキーは登録済みです。",
  "User or passkey unavailable": "利用できるパスキーがありません。",
  "Invalid authentication credential": "パスキーを確認できませんでした。もう一度ログインしてください。",
  "Invalid registration credential": "パスキーを登録できませんでした。最初からやり直してください。",
  "Invalid or expired challenge": "操作の有効期限が切れました。もう一度お試しください。",
  "Missing challenge": "登録またはログインを最初からやり直してください。",
  "Not authenticated": "ログインの有効期限が切れました。もう一度ログインしてください。",
};

export function decodeBase64URL(value) {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

export function encodeBase64URL(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function creationOptions(options) {
  return {
    ...options,
    challenge: decodeBase64URL(options.challenge),
    user: { ...options.user, id: decodeBase64URL(options.user.id) },
    excludeCredentials: (options.excludeCredentials || []).map((credential) => ({
      ...credential,
      id: decodeBase64URL(credential.id),
    })),
  };
}

export function authenticationOptions(options) {
  return {
    ...options,
    challenge: decodeBase64URL(options.challenge),
    allowCredentials: (options.allowCredentials || []).map((credential) => ({
      ...credential,
      id: decodeBase64URL(credential.id),
    })),
  };
}

export function credentialJSON(credential) {
  if (!credential) throw new Error("パスキーの操作がキャンセルされました。");
  const response = {
    clientDataJSON: encodeBase64URL(credential.response.clientDataJSON),
  };
  if (credential.response.attestationObject) {
    response.attestationObject = encodeBase64URL(credential.response.attestationObject);
    response.transports = credential.response.getTransports?.() || [];
  } else {
    response.authenticatorData = encodeBase64URL(credential.response.authenticatorData);
    response.signature = encodeBase64URL(credential.response.signature);
    response.userHandle = credential.response.userHandle
      ? encodeBase64URL(credential.response.userHandle)
      : null;
  }
  const result = {
    id: credential.id,
    rawId: encodeBase64URL(credential.rawId),
    type: credential.type,
    response,
    clientExtensionResults: credential.getClientExtensionResults(),
  };
  if (credential.authenticatorAttachment) {
    result.authenticatorAttachment = credential.authenticatorAttachment;
  }
  return result;
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const message = typeof error.detail === "string" ? messages[error.detail] : null;
    throw new Error(message || (response.status === 422
      ? "入力内容を確認してください。ニックネームは100文字以内の文字・数字・_・.・-で指定してください。"
      : "処理に失敗しました。しばらくしてからもう一度お試しください。"));
  }
  return response;
}

function ensureWebAuthn() {
  if (!globalThis.isSecureContext || !globalThis.PublicKeyCredential || !navigator.credentials) {
    throw new Error("パスキー対応ブラウザーで、HTTPSまたはlocalhostからアクセスしてください。");
  }
}

export async function registerAccount(username, invitationCode) {
  ensureWebAuthn();
  // The server checks the invitation before the browser can create a passkey.
  const optionsResponse = await api("/auth/user/register", {
    method: "POST",
    body: JSON.stringify({ username, invitation_code: invitationCode }),
  });
  const options = creationOptions(await optionsResponse.json());
  const credential = await navigator.credentials.create({ publicKey: options });
  await api("/auth/user/register/verify", {
    method: "POST",
    headers: { "X-Invitation-Code": invitationCode },
    body: JSON.stringify(credentialJSON(credential)),
  });
  window.location.assign("/register/complete.html");
}

export async function login() {
  ensureWebAuthn();
  const optionsResponse = await api("/auth/user/login", {
    method: "POST",
  });
  const options = authenticationOptions(await optionsResponse.json());
  const credential = await navigator.credentials.get({ publicKey: options });
  await api("/auth/user/verify", {
    method: "POST",
    body: JSON.stringify(credentialJSON(credential)),
  });
  window.location.assign("/mypage/");
}

function errorMessage(error) {
  if (error.name === "NotAllowedError" || error.name === "AbortError") {
    return "パスキーの操作がキャンセルされたか、時間切れになりました。もう一度お試しください。";
  }
  if (error.name === "InvalidStateError") return "この端末にはすでにパスキーが登録されています。";
  if (error instanceof TypeError) return "通信に失敗しました。接続を確認してもう一度お試しください。";
  return error.message || "操作に失敗しました。もう一度お試しください。";
}

function showError(error) {
  const element = document.querySelector("#auth-error");
  element.textContent = errorMessage(error);
  element.hidden = false;
  element.focus();
}

function bindForm(form, operation) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (form.getAttribute("aria-busy") === "true") return;
    const button = form.querySelector("button[type=submit]");
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "確認しています…";
    form.setAttribute("aria-busy", "true");
    document.querySelector("#auth-error").hidden = true;
    try {
      await operation(new FormData(form));
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
      button.textContent = label;
      form.removeAttribute("aria-busy");
    }
  });
}

async function loadProfile() {
  const response = await fetch("/auth/user/me", { credentials: "same-origin", cache: "no-store" });
  if (response.status === 401) {
    window.location.replace("/");
    return;
  }
  if (!response.ok) throw new Error("ユーザー情報を取得できませんでした。再読み込みしてください。");
  const user = await response.json();
  document.querySelector("#username").textContent = user.display_name || user.username;
  document.querySelector("#account-content").hidden = false;
}

if (typeof document !== "undefined") {
  const registration = document.querySelector("#register-form");
  if (registration) {
    bindForm(registration, (data) => registerAccount(
      data.get("username").trim(), data.get("invitation_code"),
    ));
  }
  const loginForm = document.querySelector("#login-form");
  if (loginForm) bindForm(loginForm, () => login());
  if (document.body.dataset.page === "mypage") loadProfile().catch(showError);
  const logout = document.querySelector("#logout-button");
  logout?.addEventListener("click", async () => {
    logout.disabled = true;
    try {
      await api("/auth/user/logout", { method: "POST" });
      window.location.replace("/");
    } catch (error) {
      showError(error);
      logout.disabled = false;
    }
  });
}
