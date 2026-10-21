// 台数は全ユーザー共通なので、定期的に取り直して他の人の変更を反映する

const POLL_INTERVAL_MS = 20_000;

export function initParking({ api, showError }) {
  const rows = document.querySelectorAll("[data-parking-id]");
  const dialog = document.querySelector("#parking-count-dialog");
  const form = document.querySelector("#parking-count-form");
  const counters = dialog.querySelectorAll(".parking-counter");
  let activeRow;
  let counts = {};

  const showCounts = (row, values) => {
    row.querySelectorAll(".parking-count").forEach((line) => {
      const value = values[line.dataset.kind] ?? 0;
      line.querySelector(".parking-count-value").textContent = value;
      line.toggleAttribute("data-empty", value === 0);
    });
  };

  let refreshing = false;
  const refresh = async () => {
    if (refreshing) return;
    refreshing = true;
    try {
      const lots = await (await api("/parking-lots")).json();
      rows.forEach((row) => showCounts(row, lots[row.dataset.parkingId] ?? {}));
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

  const setCount = (kind, value) => {
    const counter = dialog.querySelector(`.parking-counter[data-kind="${kind}"]`);
    const max = Number(counter.dataset.max);
    counts[kind] = Math.min(Math.max(0, value), max);
    counter.querySelector(".parking-counter-value").textContent = counts[kind];
    counter.querySelector('[data-step="-1"]').disabled = counts[kind] === 0;
    counter.querySelector('[data-step="1"]').disabled = counts[kind] === max;
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
      counts = {};
      counters.forEach((counter) => {
        const line = activeRow.querySelector(`.parking-count[data-kind="${counter.dataset.kind}"]`);
        counter.hidden = !line;
        if (line) {
          // data-max がない区分は上限なし
          counter.dataset.max = line.dataset.max ?? Infinity;
          setCount(counter.dataset.kind, Number(line.querySelector(".parking-count-value").textContent) || 0);
        }
      });
      dialog.showModal();
    });
  });

  counters.forEach((counter) => {
    counter.querySelectorAll("[data-step]").forEach((button) => {
      button.addEventListener("click", () => {
        const { kind } = counter.dataset;
        setCount(kind, counts[kind] + Number(button.dataset.step));
      });
    });
  });

  document.querySelector("#parking-count-cancel").addEventListener("click", closeDialog);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const saveButton = form.querySelector('button[type="submit"]');
    saveButton.disabled = true;
    try {
      const response = await api(`/parking-lots/${activeRow.dataset.parkingId}`, {
        method: "PUT",
        body: JSON.stringify(counts),
      });
      showCounts(activeRow, await response.json());
      closeDialog();
    } catch (error) {
      showError(error);
    } finally {
      saveButton.disabled = false;
    }
  });
}
