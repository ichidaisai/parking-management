// 台数は全ユーザー共通なので、定期的に取り直して他の人の変更を反映する

const POLL_INTERVAL_MS = 20_000;

const signed = (value) => (value > 0 ? `+${value}` : String(value));

const pick = (values, kinds) => Object.fromEntries(kinds.map((kind) => [kind, values[kind]]));

export function initParking({ api, showError, errorMessage }) {
  const rows = document.querySelectorAll("[data-parking-id]");
  const dialog = document.querySelector("#parking-count-dialog");
  const form = document.querySelector("#parking-count-form");
  const counters = dialog.querySelectorAll(".parking-counter");
  const message = document.querySelector("#parking-count-message");
  const saveButton = form.querySelector('button[type="submit"]');
  let activeRow;
  // 保存はダイアログを開いたときの台数(base)からの増減で送り、他の人の変更を上書きしない
  let base = {};
  let changes = {};
  let maxes = {};
  let requestId;

  const showCounts = (row, values) => {
    row.querySelectorAll(".parking-count").forEach((line) => {
      const value = values[line.dataset.kind] ?? 0;
      line.querySelector(".parking-count-value").textContent = value;
      line.toggleAttribute("data-empty", value === 0);
    });
  };

  const loadLots = async () => {
    const lots = await (await api("/parking-lots")).json();
    rows.forEach((row) => showCounts(row, lots[row.dataset.parkingId] ?? {}));
    return lots;
  };

  let refreshing = false;
  const refresh = async () => {
    if (refreshing) return;
    refreshing = true;
    try {
      await loadLots();
    } finally {
      refreshing = false;
    }
  };

  refresh().catch(showError);
  setInterval(() => {
    if (!document.hidden) refresh().catch(() => {});
  }, POLL_INTERVAL_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh().catch(() => {});
  });

  const outOfRange = (kind) => {
    const after = base[kind] + changes[kind];
    return after < 0 || after > maxes[kind];
  };

  const render = () => {
    const kinds = Object.keys(changes);
    kinds.forEach((kind) => {
      const counter = dialog.querySelector(`.parking-counter[data-kind="${kind}"]`);
      const after = base[kind] + changes[kind];
      counter.querySelector(".parking-counter-value").textContent = signed(changes[kind]);
      counter.querySelector(".parking-counter-detail").textContent = `${base[kind]} → ${after}`;
      counter.querySelector('[data-step="-1"]').disabled = after <= 0;
      counter.querySelector('[data-step="1"]').disabled = after >= maxes[kind];
    });
    saveButton.disabled = kinds.every((kind) => changes[kind] === 0) || kinds.some(outOfRange);
  };

  const showMessage = (text) => {
    message.textContent = text;
    message.hidden = false;
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
    event.preventDefault();
    closeDialog();
  });

  document.querySelectorAll(".parking-change-button").forEach((button) => {
    button.addEventListener("click", () => {
      activeRow = button.closest("[data-parking-id]");
      document.querySelector("#parking-count-title").textContent =
        activeRow.querySelector(".parking-name").textContent;
      base = {};
      changes = {};
      maxes = {};
      // 送り直しても同じ変更だとサーバーがわかるよう、ダイアログを開くたびに作る
      requestId = crypto.randomUUID();
      counters.forEach((counter) => {
        const { kind } = counter.dataset;
        const line = activeRow.querySelector(`.parking-count[data-kind="${kind}"]`);
        counter.hidden = !line;
        if (line) {
          // data-max がない区分は上限なし
          maxes[kind] = Number(line.dataset.max ?? Infinity);
          base[kind] = Number(line.querySelector(".parking-count-value").textContent) || 0;
          changes[kind] = 0;
        }
      });
      message.hidden = true;
      saveButton.textContent = "保存";
      render();
      dialog.showModal();
    });
  });

  counters.forEach((counter) => {
    counter.querySelectorAll("[data-step]").forEach((button) => {
      button.addEventListener("click", () => {
        changes[counter.dataset.kind] += Number(button.dataset.step);
        render();
      });
    });
  });

  document.querySelector("#parking-count-cancel").addEventListener("click", closeDialog);

  // 開いている間に他の人が変えていたら、同じ車の記録かもしれないので、最新の台数を見せて確認する
  const confirmConflict = async () => {
    const latest = (await loadLots())[activeRow.dataset.parkingId] ?? {};
    const changed = Object.keys(base)
      .filter((kind) => (latest[kind] ?? 0) !== base[kind])
      .map((kind) => {
        const label = dialog.querySelector(`.parking-counter[data-kind="${kind}"] .parking-counter-label`);
        return `${label.textContent} ${base[kind]} → ${latest[kind] ?? 0}`;
      });
    Object.keys(base).forEach((kind) => {
      base[kind] = latest[kind] ?? 0;
    });
    saveButton.textContent = "加えて保存";
    showMessage(`他の人が先に変更しました${changed.length ? `（${changed.join("、")}）` : ""}。あなたの変更も加えますか？`);
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    message.hidden = true;
    const kinds = Object.keys(changes).filter((kind) => changes[kind] !== 0);
    try {
      const response = await api(`/parking-lots/${activeRow.dataset.parkingId}/changes`, {
        method: "POST",
        body: JSON.stringify({ request_id: requestId, expected: pick(base, kinds), changes: pick(changes, kinds) }),
      });
      showCounts(activeRow, await response.json());
      closeDialog();
    } catch (error) {
      try {
        if (error.detail === "Changed by another user") {
          await confirmConflict();
        } else if (error.detail === "Change already applied") {
          await loadLots();
          closeDialog();
          showError(error);
        } else {
          showMessage(errorMessage(error));
        }
      } catch (reloadError) {
        showMessage(errorMessage(reloadError));
      }
    } finally {
      render();
    }
  });
}
