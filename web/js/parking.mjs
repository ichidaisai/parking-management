// マイページの駐車場一覧と台数変更ダイアログ。
// 台数は全ユーザー共通なので、定期的に取り直して他の人の変更を反映する。

const POLL_INTERVAL_MS = 20_000;

export function initParking({ api, showError }) {
  const rows = document.querySelectorAll("[data-parking-id]");
  const dialog = document.querySelector("#parking-count-dialog");
  const form = document.querySelector("#parking-count-form");
  const display = document.querySelector("#parking-count-value");
  let activeParkingId;
  let count = 0;

  const showCount = (row, value) => {
    row.querySelector(".parking-count-value").textContent = value;
  };

  let refreshing = false;
  const refresh = async () => {
    if (refreshing) return;
    refreshing = true;
    try {
      const counts = await (await api("/parking-lots")).json();
      rows.forEach((row) => showCount(row, counts[row.dataset.parkingId] ?? 0));
    } finally {
      refreshing = false;
    }
  };

  // 初回の失敗だけ表示し、定期更新の一時的な失敗は次回に任せる
  refresh().catch(showError);
  setInterval(() => {
    if (!document.hidden) refresh().catch(() => {});
  }, POLL_INTERVAL_MS);
  // バックグラウンドから戻ったときはすぐ最新にする
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh().catch(() => {});
  });

  const setCount = (value) => {
    count = Math.max(0, value);
    display.textContent = count;
  };

  // close() 直後は :modal が外れてレイアウトが変わるため、フェードし終えてから閉じる
  const closeDialog = async () => {
    if (dialog.classList.contains("is-closing")) return;
    dialog.classList.add("is-closing");
    await Promise.allSettled(dialog.getAnimations().map((animation) => animation.finished));
    dialog.close();
    dialog.classList.remove("is-closing");
  };

  dialog.addEventListener("cancel", (event) => {
    event.preventDefault(); // Esc でもフェードさせる
    closeDialog();
  });

  document.querySelectorAll(".parking-change-button").forEach((button) => {
    button.addEventListener("click", () => {
      const row = button.closest("[data-parking-id]");
      activeParkingId = row.dataset.parkingId;
      document.querySelector("#parking-count-title").textContent =
        row.querySelector(".parking-name").textContent;
      setCount(Number(row.querySelector(".parking-count-value").textContent) || 0);
      dialog.showModal();
    });
  });

  document.querySelector("#parking-count-minus").addEventListener("click", () => {
    setCount(count - 1);
  });

  document.querySelector("#parking-count-plus").addEventListener("click", () => {
    setCount(count + 1);
  });

  document.querySelector("#parking-count-cancel").addEventListener("click", closeDialog);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const saveButton = form.querySelector('button[type="submit"]');
    saveButton.disabled = true;
    try {
      const response = await api(`/parking-lots/${activeParkingId}`, {
        method: "PUT",
        body: JSON.stringify({ count }),
      });
      const result = await response.json();
      showCount(document.querySelector(`[data-parking-id="${activeParkingId}"]`), result.count);
      closeDialog();
    } catch (error) {
      showError(error);
    } finally {
      saveButton.disabled = false;
    }
  });
}
