"use strict";
// Settings › This computer: start at sign-in (/api/desk/shortcuts), where links open
// (GET/POST /api/browser {links: "app" | "system"}), closing the window keeps it running
// (/api/runtime/background), whether a new release is out (/api/runtime, /api/runtime/check), and
// backing up, restoring and resetting the home (/api/desk/data, backup, open, restore, reset).
(() => {

  window.paSetup.part("device", (kit) => {
    const { el } = kit;
    const t = kit.words("setup_device");
    const ctx = kit.ctx;
    // Shown only where the computer has it; "other" is another copy's link.
    const start = { login: "off", saving: false, known: false };
    const links = { value: "", saving: false };
    const runtime = { info: null, checking: false, background: false };
    const node = kit.section("device");
    const box = kit.card();
    box.classList.add("setup-options");
    const pet = { saving: false };
    box.append(buildStart(), buildLinks(), buildSideFold(), buildBackground(), buildPet(), buildUpdates());
    const data = { place: "", targets: [], backups: [], working: false };
    node.append(box, buildData());

    // ---- Start at sign-in.
    function buildStart() {
      const { row, box: on, about } = kit.switchRow("start", t.startTitle, t.startText, (node) => setStart(node.checked));
      row.hidden = true;
      const other = el("div", "setup-option-other");
      other.hidden = true;
      const mine = kit.button("setup-button-secondary setup-button-sm", t.startMine, () => setStart(true));
      other.append(el("span", "", t.startOther), mine);
      const error = kit.errorLine();
      about.after(other, error);
      Object.assign(start, { row, other, mine, error, box: on });
      return row;
    }

    function renderStart() {
      const { other, mine, box: on, login, saving, known } = start;
      on.checked = login === "on";
      // Not read yet, it cannot be changed.
      on.disabled = saving || !known;
      other.hidden = login !== "other";
      mine.disabled = saving;
    }

    // Read each time Settings opens: the first run may have changed it since.
    async function loadStart() {
      if (start.saving) return;
      try {
        const value = await ctx.api("desk/shortcuts");
        if (start.saving) return;
        start.row.hidden = !value.login_supported;
        if (!value.login_supported) return;
        Object.assign(start, { login: value.login, known: true });
        start.error.hidden = true;
      } catch (error) {
        // Read before: what was read stays. Never read: shown, switched off, with what went wrong.
        if (start.known) return;
        start.row.hidden = false;
        start.error.textContent = error?.message === "desk_shortcut_failed" ? t.startUnread : kit.say(error, t);
        start.error.hidden = false;
      }
      renderStart();
    }

    async function setStart(on) {
      start.saving = true;
      start.error.hidden = true;
      renderStart();
      try {
        const value = await ctx.api("desk/shortcut", { which: "login", on });
        Object.assign(start, { login: value.login });
      } catch (error) {
        kit.failed(start.error, error, t);
      } finally {
        start.saving = false;
        renderStart();
      }
    }

    // ---- Where links open: in the app's own browser or the system's.
    function buildLinks() {
      const choice = kit.choices(t.linksTitle, t.links, setLinks);
      const { row, words, about } = kit.optionRow(t.linksTitle, t.linksOnlyWindow, choice.group);
      const error = kit.errorLine();
      words.append(error);
      // Shown once read.
      row.hidden = true;
      Object.assign(links, { row, about, choice, error });
      return row;
    }

    async function loadLinks() {
      if (links.saving) return;
      try {
        const value = await ctx.api("browser");
        links.value = value.links;
        links.about.hidden = value.builtin;
        links.error.hidden = true;
      } catch (error) {
        kit.failed(links.error, error, t);
      }
      links.row.hidden = false;
      links.choice.set(links.value);
    }

    async function setLinks(value) {
      if (links.saving || value === links.value) return;
      links.saving = true;
      links.choice.set(value, true);
      links.error.hidden = true;
      try {
        links.value = (await ctx.api("browser", { links: value })).links;
      } catch (error) {
        kit.failed(links.error, error, t);
      }
      links.saving = false;
      links.choice.set(links.value);
    }

    // ---- A page in front folds the whole sidebar to its rail (app.js paSideFold): kept in this window's own
    // storage as the sidebar's fold is, on unless turned off here.
    function buildSideFold() {
      const { row, box: on } = kit.switchRow("side-fold", t.sideFoldTitle, t.sideFoldText, (node) => {
        if (node.checked) localStorage.removeItem("mibo-side-page");
        else localStorage.setItem("mibo-side-page", "keep");
      });
      on.checked = localStorage.getItem("mibo-side-page") !== "keep";
      return row;
    }

    // ---- Keep running when the window closes.
    function buildBackground() {
      const { row, box: on, about } = kit.switchRow("background", t.bgTitle, t.bgText, (node) => setBackground(node.checked));
      row.hidden = true;
      const error = kit.errorLine();
      about.after(error);
      Object.assign(runtime, { bgRow: row, bgBox: on, bgAbout: about, bgError: error });
      return row;
    }

    function renderBackground() {
      const info = runtime.info;
      runtime.bgBox.checked = info.background;
      runtime.bgBox.disabled = runtime.background || (!info.canHide && !info.background);
      runtime.bgAbout.textContent = !info.canHide && !info.background ? t.bgOnlyWindow : t.bgText;
    }

    async function setBackground(on) {
      runtime.background = true;
      runtime.bgError.hidden = true;
      renderBackground();
      try {
        runtime.info = await ctx.api("runtime/background", { on });
      } catch (error) {
        kit.failed(runtime.bgError, error, t);
      } finally {
        runtime.background = false;
        renderAll();
      }
    }

    // ---- 显示桌宠 (/api/pet, petoff.py): shown where the app window has the pet, on its own computer.
    function buildPet() {
      const { row, box: on, about } = kit.switchRow("pet-shown", t.petTitle, t.petText, (node) => setPet(node.checked));
      row.hidden = true;
      const error = kit.errorLine();
      about.after(error);
      Object.assign(pet, { row, box: on, error });
      return row;
    }

    function renderPet(value) {
      pet.row.hidden = !value.available;
      pet.box.checked = value.shown;
      pet.box.disabled = pet.saving;
    }

    async function loadPet() {
      if (pet.saving) return;
      try {
        renderPet(await ctx.api("pet"));
      } catch {
        pet.row.hidden = true;
      }
    }

    async function setPet(shown) {
      pet.saving = true;
      pet.error.hidden = true;
      pet.box.disabled = true;
      try {
        pet.saving = false;
        renderPet(await ctx.api("pet", { shown }));
      } catch (error) {
        pet.saving = false;
        pet.box.checked = !shown;
        pet.box.disabled = false;
        kit.failed(pet.error, error, t);
      }
    }

    // ---- Updates.
    function buildUpdates() {
      const check = kit.button("setup-button-secondary setup-button-sm", t.check, checkUpdates);
      const buttons = el("div", "setup-option-actions");
      buttons.append(check);
      const { row, about } = kit.optionRow(t.updateTitle, "", buttons);
      row.hidden = true;
      const release = el("a", "setup-link");
      release.target = "_blank";
      release.rel = "noopener noreferrer";
      release.append(el("span", "", t.release), kit.icon("out", "setup-icon setup-icon-sm"));
      release.hidden = true;
      const error = kit.errorLine();
      about.after(release, error);
      Object.assign(runtime, { upRow: row, upAbout: about, upCheck: check, upRelease: release, upError: error });
      return row;
    }

    function renderUpdates() {
      const info = runtime.info;
      const found = info.update;
      const lines = [paI18n.t("setup_device.version", { v: info.version })];
      if (found) lines.push(paI18n.t("setup_device.newer", { v: found.version }));
      else if (info.checkFailed) lines.push(t.unknown);
      else lines.push(info.checked ? t.latest : t.never);
      runtime.upAbout.textContent = lines.join(t.join);
      runtime.upAbout.hidden = false;
      if (found) runtime.upRelease.href = found.url;
      runtime.upRelease.hidden = !found;
      kit.busy(runtime.upCheck, runtime.checking, runtime.checking ? t.checking : t.check);
      runtime.upRow.hidden = false;
    }

    async function checkUpdates() {
      runtime.checking = true;
      runtime.upError.hidden = true;
      renderUpdates();
      try {
        runtime.info = await ctx.api("runtime/check", {});
      } catch (error) {
        kit.failed(runtime.upError, error, t);
      } finally {
        runtime.checking = false;
        renderAll();
      }
    }

    function renderAll() {
      renderBackground();
      renderUpdates();
    }

    // ---- 备份与恢复 (/api/desk/data, backup, open, restore, reset): a restore or a reset starts Mibo again.
    function tick(text) {
      const label = el("label", "setup-tick");
      const box = el("input");
      box.type = "checkbox";
      label.append(box, el("span", "", text));
      return { label, box };
    }

    function buildData() {
      const card = kit.card(t.dataTitle);
      card.classList.add("setup-options");
      // Where the settings and the keys are kept, and names in settings.json that change nothing (desk.Desk.settings).
      const keptRow = kit.optionRow(t.keptTitle, "");
      // Its choices are the places this computer has, once read.
      const placeRow = kit.optionRow(t.placeTitle, "");

      const backup = kit.button("setup-button-secondary setup-button-sm", t.backup, makeBackup);
      const backupRow = kit.optionRow(t.backupTitle, t.backupText, backup);
      const secrets = tick(t.secrets);
      const savedLine = el("p", "setup-option-text");
      savedLine.hidden = true;
      const open = el("button", "setup-link setup-data-open", t.openFolder);
      open.type = "button";
      open.hidden = true;
      open.addEventListener("click", () => ctx.api("desk/open", { place: data.savedTo }).catch((error) => kit.failed(data.error, error, t)));
      backupRow.words.append(secrets.label, savedLine, open);

      const chosen = el("select", "setup-input setup-select setup-input-sm");
      chosen.setAttribute("aria-label", t.restoreTitle);
      const restore = kit.button("setup-button-secondary setup-button-sm", t.restore, askRestore);
      const restoreActions = el("div", "setup-option-actions");
      restoreActions.append(chosen, restore);
      const restoreRow = kit.optionRow(t.restoreTitle, t.restoreText, restoreActions);

      const reset = kit.button("setup-button-secondary setup-button-sm", t.reset, askReset);
      const resetRow = kit.optionRow(t.resetTitle, t.resetText, reset);
      const first = tick(t.resetBackup);
      first.box.checked = true;
      resetRow.words.append(first.label);

      const note = el("p", "setup-option-text setup-data-note");
      note.setAttribute("aria-live", "polite");
      note.hidden = true;
      const error = kit.errorLine();
      card.append(keptRow.row, placeRow.row, backupRow.row, restoreRow.row, resetRow.row, note, error);
      Object.assign(data, { keptRow, placeRow: placeRow.row, backup, secrets: secrets.box, savedLine, open, chosen, restore,
        restoreRow, reset, first: first.box, note, error });
      return card;
    }

    function renderData() {
      const d = data;
      const keys = d.targets.map((one) => one.id).join();
      if (d.placeKeys !== keys) {
        d.placeChoice?.group.remove();
        d.placeChoice = kit.choices(t.placeTitle, d.targets.map((one) => [one.id, t.places[one.id]]), (value) => {
          d.place = value;
          d.placeChoice.set(value, d.working);
        });
        d.placeRow.append(d.placeChoice.group);
        d.placeKeys = keys;
      }
      d.placeChoice.set(d.place, d.working);
      d.chosen.replaceChildren(
        ...d.backups.map((one) => {
          const option = el("option", "", `${one.name} · ${t.places[one.place]}`);
          option.value = `${one.place}/${one.name}`;
          return option;
        }),
      );
      d.chosen.hidden = !d.backups.length;
      d.restore.hidden = !d.backups.length;
      d.restoreRow.about.textContent = d.backups.length ? t.restoreText : t.restoreNone;
      const s = d.settings;
      if (s) {
        const lines = [paI18n.t("setup_device.keptFile", { path: s.file }),
          s.keys === "store" ? paI18n.t("setup_device.keptStore", { store: t.stores[s.store] || t.stores.other })
            : paI18n.t("setup_device.keptPlain", { path: s.store })];
        if (s.unknown.length) lines.push(paI18n.t("setup_device.keptUnknown", { names: s.unknown.join(", ") }));
        if (s.invalid.length) lines.push(paI18n.t("setup_device.keptInvalid", { names: s.invalid.join(", ") }));
        d.keptRow.about.replaceChildren(...lines.flatMap((line, i) => (i ? [el("br"), line] : [line])));
        d.keptRow.about.hidden = false;
      }
      for (const control of [d.backup, d.secrets, d.chosen, d.restore, d.reset, d.first]) control.disabled = d.working;
    }

    async function loadData() {
      if (data.working) return;
      try {
        const value = await ctx.api("desk/data");
        data.targets = value.targets;
        data.backups = value.backups;
        data.settings = value.settings;
        if (!data.targets.some((one) => one.id === data.place)) data.place = data.targets[0]?.id || "";
        if (value.restored) {
          data.note.textContent = value.restored.error ? t.errors[value.restored.error] : t.restored;
          data.note.hidden = false;
        }
        data.error.hidden = true;
      } catch (error) {
        kit.failed(data.error, error, t);
      }
      renderData();
    }

    async function makeBackup() {
      data.working = true;
      data.error.hidden = true;
      kit.busy(data.backup, true, t.backingUp);
      renderData();
      try {
        const value = await ctx.api("desk/backup", { to: data.place, secrets: data.secrets.checked });
        data.backups = value.backups;
        data.savedTo = data.place;
        data.savedLine.textContent = paI18n.t("setup_device.saved", { path: value.path });
        data.savedLine.hidden = false;
        data.open.hidden = false;
      } catch (error) {
        kit.failed(data.error, error, t);
      } finally {
        data.working = false;
        kit.busy(data.backup, false, t.backup);
        renderData();
      }
    }

    // Both end with Mibo closing and opening again; until then the card says so and takes no clicks.
    async function restartWith(path, payload) {
      data.working = true;
      data.error.hidden = true;
      renderData();
      try {
        await ctx.api(path, payload);
        data.note.textContent = t.restarting;
        data.note.hidden = false;
      } catch (error) {
        data.working = false;
        kit.failed(data.error, error, t);
        renderData();
      }
    }

    async function askRestore() {
      const [place, name] = data.chosen.value.split("/");
      if (!name || !(await ctx.confirm({ title: t.restoreAsk, text: paI18n.t("setup_device.restoreAskText", { name }), ok: t.restoreOk }))) return;
      restartWith("desk/restore", { place, name });
    }

    async function askReset() {
      const first = data.first.checked && data.place;
      const text = first ? `${t.resetAskText}${t.join}${paI18n.t("setup_device.resetAskBackup", { place: t.places[data.place] })}` : t.resetAskText;
      if (!(await ctx.confirm({ title: t.resetAsk, text, ok: t.resetOk }))) return;
      restartWith("desk/reset", { backupTo: first ? data.place : "", secrets: data.secrets.checked });
    }

    async function loadRuntime() {
      if (runtime.background || runtime.checking) return;
      try {
        runtime.info = await ctx.api("runtime");
        runtime.upError.hidden = true;
      } catch (error) {
        runtime.upRow.hidden = false;
        kit.failed(runtime.upError, error, t);
        return;
      }
      renderAll();
    }

    function show() {
      loadStart();
      loadLinks();
      loadPet();
      loadRuntime();
      loadData();
    }

    return { node, show };
  });
})();
