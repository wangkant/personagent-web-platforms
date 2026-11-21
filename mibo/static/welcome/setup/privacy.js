"use strict";
// Settings › Privacy: whether the character notices what is in front (/api/remarks/settings)
// and the programs it never looks at, hiding in full screen (/api/quiet/settings),
// and what the app's own browser read (/api/browser {shared}, its history at /api/browser/history, cleared by a
// time range at /api/browser/clear), the site permissions decided there (camera, microphone, location ...: listed
// and taken back at /api/browser/permissions), and per site what it may
// do there (/api/browser/sites, /api/browser/allowed): kinds let through without asking, the person's feed
// read in the background, the ways kept from confirmed tasks.
(() => {
  // As many programs as the server keeps.
  const IGNORE_MAX = 50;

  window.paSetup.part("privacy", (kit) => {
    const { el } = kit;
    const t = kit.words("setup_privacy");
    const ctx = kit.ctx;
    const remarks = { on: true, ignore: [], saving: false, known: false };
    const quiet = { saving: false };
    const browsing = {};
    const history = { next: null, day: "", turn: 0 };
    const places = {};
    const permits = {};
    const node = kit.section("privacy");
    const box = kit.card();
    box.classList.add("setup-options");
    box.append(buildRemarks(), buildQuiet(), ...buildBrowsing(), buildPermits(), buildSites());
    node.append(box);

    // ---- Remarks on what is in front, and the programs it never looks at.
    function buildRemarks() {
      const { row, box: on, about } = kit.switchRow("remarks", t.remarksTitle, t.remarksText,
        (node) => saveRemarks({ on: node.checked }));
      const error = kit.errorLine();
      about.after(error);
      // Folded under the switch, across the whole row; shown only while the switch is on.
      const fold = kit.fold(paI18n.t("setup_privacy.ignoreFold", { count: 0 }));
      fold.node.classList.add("setup-ignore");
      const list = el("div", "setup-words");
      const help = el("p", "setup-help", t.ignoreHelp);
      help.id = "setup-ignore-help";
      const add = kit.input("setup-word-input");
      add.placeholder = t.ignoreAdd;
      add.maxLength = 64;
      add.setAttribute("aria-label", t.ignoreAdd);
      add.setAttribute("aria-describedby", help.id);
      add.addEventListener("keydown", (event) => {
        const name = add.value.trim().replace(/\s+/g, " ");
        if (event.key !== "Enter" || event.isComposing || !name) return;
        event.preventDefault();
        // One already there takes no second place.
        if (remarks.ignore.some((n) => n.toLowerCase() === name.toLowerCase())) {
          add.value = "";
          return;
        }
        saveRemarks({ ignore: [...remarks.ignore, name] }).then((done) => {
          if (done) add.value = "";
          add.focus();
        });
      });
      fold.body.append(list, help);
      row.append(fold.node);
      Object.assign(remarks, { box: on, error, fold, list, add });
      return row;
    }

    function renderRemarks() {
      const { box: on, fold, list, add, ignore, saving, known } = remarks;
      on.checked = remarks.on;
      on.disabled = saving || !known;
      fold.node.hidden = !remarks.on;
      fold.summary.lastChild.textContent = paI18n.t("setup_privacy.ignoreFold", { count: ignore.length });
      list.replaceChildren(
        ...ignore.map((name) => {
          const chip = el("span", "setup-word");
          const off = kit.iconButton("setup-word-off", "cross", paI18n.t("setup_privacy.ignoreRemove", { name }), () => unignore(name));
          off.disabled = saving;
          chip.append(el("span", "", name), off);
          return chip;
        }),
      );
      if (ignore.length < IGNORE_MAX) list.append(add);
      add.disabled = saving || !known;
    }

    async function unignore(name) {
      const sure = await ctx.confirm({ title: paI18n.t("setup_privacy.ignoreAsk", { name }), text: t.ignoreAskText, ok: t.remove });
      if (!sure) return;
      await saveRemarks({ ignore: remarks.ignore.filter((n) => n !== name) });
      remarks.add.focus();
    }

    async function loadRemarks() {
      if (remarks.saving) return;
      try {
        const value = await ctx.api("remarks/settings");
        if (remarks.saving) return;
        Object.assign(remarks, { on: value.on, ignore: value.ignore, known: true });
        remarks.error.hidden = true;
      } catch (error) {
        kit.failed(remarks.error, error, t);
      }
      renderRemarks();
    }

    // The server answers with the cleaned whole: names trimmed, no doubles.
    async function saveRemarks(change) {
      remarks.saving = true;
      remarks.error.hidden = true;
      renderRemarks();
      try {
        const value = await ctx.api("remarks/settings", change);
        Object.assign(remarks, { on: value.on, ignore: value.ignore });
        return true;
      } catch (error) {
        kit.failed(remarks.error, error, t);
        return false;
      } finally {
        remarks.saving = false;
        renderRemarks();
      }
    }

    // ---- Hiding in full screen and games.
    function buildQuiet() {
      const { row, box: on, about } = kit.switchRow("quiet", t.quietTitle, t.quietText, (node) => setQuiet(node.checked));
      const now = el("p", "setup-option-now");
      now.hidden = true;
      const error = kit.errorLine();
      about.after(now, error);
      on.disabled = true;
      Object.assign(quiet, { box: on, now, error });
      return row;
    }

    // Hidden right now is only ever seen from another device: this window in front never counts.
    async function loadQuiet() {
      if (quiet.saving) return;
      try {
        const [value, now] = await Promise.all([ctx.api("quiet/settings"), ctx.api("quiet")]);
        if (quiet.saving) return;
        quiet.box.checked = value.on;
        quiet.box.disabled = false;
        quiet.now.textContent = t.quietNow[now.why] || "";
        quiet.now.hidden = !(value.on && now.quiet);
        quiet.error.hidden = true;
      } catch (error) {
        kit.failed(quiet.error, error, t);
      }
    }

    async function setQuiet(on) {
      quiet.saving = true;
      quiet.box.disabled = true;
      quiet.error.hidden = true;
      try {
        quiet.box.checked = (await ctx.api("quiet/settings", { on })).on;
        if (!on) quiet.now.hidden = true;
      } catch (error) {
        quiet.box.checked = !on;
        kit.failed(quiet.error, error, t);
      } finally {
        quiet.saving = false;
        quiet.box.disabled = false;
      }
      // Back on: whether it is hiding right now is read again.
      if (quiet.box.checked) loadQuiet();
    }

    // ---- The app's own browser: whether the character may use what was read there, the history, and clearing it
    // by a time range (sign-ins and saved passwords are in Settings › 连接).
    function buildBrowsing() {
      const { row, box: on, about } = kit.switchRow("browsing", t.browsingTitle, t.browsingText,
        (node) => saveBrowsing(node.checked));
      const error = kit.errorLine();
      about.after(error);
      on.disabled = true;
      const range = el("select", "setup-input setup-select setup-input-sm setup-history-range");
      range.setAttribute("aria-label", t.clearRange);
      for (const key of ["hour", "day", "all"]) {
        const option = el("option", "", t.ranges[key]);
        option.value = key;
        range.append(option);
      }
      const clear = kit.button("setup-button-secondary setup-button-sm setup-history-clear", t.clear, clearBrowsing);
      const tools = el("div", "setup-history-tools");
      tools.append(range, clear);
      const wipe = kit.optionRow(t.historyTitle, t.historyText, tools);
      const done = el("p", "setup-option-now", t.cleared);
      done.hidden = true;
      wipe.about.after(done);
      wipe.row.append(buildHistory());
      row.hidden = wipe.row.hidden = true;
      Object.assign(browsing, { row, box: on, error, range, clear, wipeRow: wipe.row, done });
      return [row, wipe.row];
    }

    // The history, folded under its row: searched, a page opened again (a link opens in the app's browser), an
    // entry deleted; read a page at a time from /api/browser/history once unfolded.
    function buildHistory() {
      const fold = kit.fold(t.historyFold);
      fold.node.classList.add("setup-history");
      const search = kit.input("setup-history-search");
      search.type = "search";
      search.maxLength = 200;
      search.placeholder = t.historySearch;
      search.setAttribute("aria-label", t.historySearch);
      let timer = 0;
      search.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(() => loadHistory(true), 250);
      });
      const list = el("div", "setup-history-list");
      const more = kit.button("setup-button-secondary setup-button-sm setup-history-more", t.historyMore, () => loadHistory(false));
      more.hidden = true;
      const error = kit.errorLine();
      fold.body.append(search, list, more, error);
      fold.node.addEventListener("toggle", () => fold.node.open && loadHistory(true));
      Object.assign(history, { fold: fold.node, search, list, more, error });
      return fold.node;
    }

    async function loadHistory(first) {
      // Each fresh load counts up, so a page asked for before the search changed is dropped when it comes.
      const mine = first ? ++history.turn : history.turn;
      const query = new URLSearchParams({ q: history.search.value.trim() });
      if (!first) query.set("before", history.next);
      history.more.disabled = true;
      try {
        const value = await ctx.api(`browser/history?${query}`);
        if (mine !== history.turn) return;
        if (first) {
          history.list.replaceChildren();
          history.day = "";
          if (!value.pages.length) {
            history.list.append(el("p", "setup-help", query.get("q") ? t.historyNoMatch : t.historyNone));
          }
        }
        for (const page of value.pages) history.list.append(...entry(page));
        history.next = value.next;
        history.more.hidden = value.next === null;
        history.error.hidden = true;
      } catch (error) {
        kit.failed(history.error, error, t);
      } finally {
        history.more.disabled = false;
      }
    }

    // One entry: its time, its title and site as a link, and its delete; a line for its day before the first of it.
    function entry(page) {
      const nodes = [];
      if (page.day !== history.day) {
        history.day = page.day;
        nodes.push(el("p", "setup-history-day", dayLabel(page.at)));
      }
      const row = el("div", "setup-history-row");
      const time = new Date(page.at * 1000).toLocaleTimeString(paI18n.locale, { hour: "2-digit", minute: "2-digit" });
      const link = el("a", "setup-history-link");
      link.href = page.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.title = page.url;
      link.append(el("span", "setup-history-title", page.title || page.host), el("span", "setup-history-host", page.host));
      const off = kit.iconButton("setup-word-off setup-history-off", "cross",
        paI18n.t("setup_privacy.historyRemove", { title: page.title || page.host }), () => removeEntry(page, row, off));
      row.append(el("span", "setup-history-time", time), link, off);
      nodes.push(row);
      return nodes;
    }

    async function removeEntry(page, row, off) {
      off.disabled = true;
      try {
        await ctx.api("browser/history", { url: page.url, day: page.day });
      } catch (error) {
        off.disabled = false;
        return kit.failed(history.error, error, t);
      }
      // A day left with no entry loses its line.
      const before = row.previousElementSibling;
      const after = row.nextElementSibling;
      row.remove();
      if (before?.classList.contains("setup-history-day") && (!after || after.classList.contains("setup-history-day"))) {
        before.remove();
      }
      if (!history.list.querySelector(".setup-history-row")) loadHistory(true);
    }

    // 今天 · 11月20日周五, as the 推荐记录 list's day lines read.
    function dayLabel(at) {
      const date = new Date(at * 1000);
      const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      const back = Math.round((start(new Date()) - start(date)) / 86400000);
      const year = date.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {};
      const day = date.toLocaleDateString(paI18n.locale, { weekday: "short", month: "short", day: "numeric", ...year });
      const near = back === 0 ? paI18n.t("finds.today") : back === 1 ? paI18n.t("finds.yesterday") : "";
      return near ? `${near} · ${day}` : day;
    }

    async function loadBrowsing() {
      try {
        const value = await ctx.api("browser");
        browsing.row.hidden = browsing.wipeRow.hidden = permits.fold.node.hidden = !value.builtin;
        browsing.box.checked = value.shared;
        browsing.box.disabled = false;
        browsing.error.hidden = true;
      } catch (error) {
        kit.failed(browsing.error, error, t);
      }
    }

    async function saveBrowsing(on) {
      browsing.box.disabled = true;
      browsing.error.hidden = true;
      try {
        browsing.box.checked = (await ctx.api("browser", { shared: on })).shared;
      } catch (error) {
        browsing.box.checked = !on;
        kit.failed(browsing.error, error, t);
      } finally {
        browsing.box.disabled = false;
      }
    }

    async function clearBrowsing() {
      const range = browsing.range.value;
      const sure = await ctx.confirm({ title: t.clearAsk[range], text: t.clearAskText, ok: t.clear });
      if (!sure) return;
      kit.busy(browsing.clear, true);
      browsing.done.hidden = true;
      try {
        await ctx.api("browser/clear", { range });
        browsing.done.hidden = false;
        if (history.fold.open) loadHistory(true);
      } catch (error) {
        kit.failed(browsing.error, error, t);
      } finally {
        kit.busy(browsing.clear, false);
      }
    }

    // ---- The site permissions decided in the app's browser, folded: each site's kinds, allowed always or refused,
    // each taken back with its cross (the site is asked again next time). Read once unfolded.
    function buildPermits() {
      const fold = kit.fold(t.permitsFold);
      fold.node.classList.add("setup-permits");
      const list = el("div", "setup-sites-list");
      const error = kit.errorLine();
      fold.body.append(list, error);
      fold.node.hidden = true;
      fold.node.addEventListener("toggle", () => fold.node.open && changePermits());
      Object.assign(permits, { fold, list, error });
      return fold.node;
    }

    function renderPermits(sites) {
      const kinds = paI18n.words("browse").prompt.kinds;
      if (!sites.length) return permits.list.replaceChildren(el("p", "setup-help", t.permitsNone));
      permits.list.replaceChildren(...sites.map((site) => {
        const item = el("div", "setup-site");
        const chips = el("div", "setup-words");
        item.append(el("p", "setup-option-name", site.host), chips);
        for (const { kind, state } of site.kinds) {
          // As a list's first word: capitalized where the language has capitals.
          const what = kinds[kind].charAt(0).toLocaleUpperCase(paI18n.locale) + kinds[kind].slice(1);
          const chip = el("span", "setup-word setup-permit");
          chip.append(el("span", "", `${what} · ${t.permitStates[state]}`),
            kit.iconButton("setup-word-off setup-permit-off", "cross", paI18n.t("setup_privacy.allowedOff", { what }),
              () => changePermits({ origin: site.origin, kind })));
          chips.append(chip);
        }
        return item;
      }));
    }

    // The list as it stands, after taking one back when `change` names it.
    async function changePermits(change) {
      permits.error.hidden = true;
      try {
        renderPermits((await ctx.api("browser/permissions", change)).sites);
      } catch (error) {
        kit.failed(permits.error, error, t);
      }
    }

    // ---- Per site: what the character may do there without asking, the feed read in the background, the ways kept.
    function buildSites() {
      const fold = kit.fold(paI18n.t("setup_privacy.sitesFold", { count: 0 }));
      fold.node.classList.add("setup-sites");
      const list = el("div", "setup-sites-list");
      const error = kit.errorLine();
      fold.body.append(list, error);
      fold.node.hidden = true;
      Object.assign(places, { fold, list, error });
      return fold.node;
    }

    function renderSites(rows) {
      places.fold.summary.lastChild.textContent = paI18n.t("setup_privacy.sitesFold", { count: rows.length });
      if (!rows.length) return places.list.replaceChildren(el("p", "setup-help", t.sitesNone));
      places.list.replaceChildren(...rows.map((row) => {
        const item = el("div", "setup-site");
        item.append(el("p", "setup-option-name", row.site));
        for (const { site, kind } of row.allowed) {
          const what = t.allowedKind[kind] || kind;
          const chip = el("span", "setup-word");
          chip.append(el("span", "", what), kit.iconButton("setup-word-off", "cross", paI18n.t("setup_privacy.allowedOff", { what }),
            () => changeSites("browser/allowed", { site, kind, on: false })));
          item.append(chip);
        }
        const id = `feed-${row.site.replace(/[^a-z0-9]/gi, "-")}`;
        const feed = kit.switchRow(id, t.feedTitle, row.feed.stopped ? `${t.feedWarn} ${t.feedStopped}` : t.feedWarn,
          async (node) => {
            if (node.checked) {
              node.checked = false;
              const sure = await ctx.confirm({ title: t.feedAsk, text: t.feedWarn, ok: t.feedYes });
              if (!sure) return;
            }
            changeSites("browser/sites", { site: row.site, feed: !row.feed.on });
          });
        feed.box.checked = row.feed.on;
        item.append(feed.row);
        const tools = el("div", "setup-site-tools");
        if (row.learned || row.allowed.length || row.feed.on) {
          tools.append(kit.button("setup-button-secondary setup-button-sm", t.forget, async () => {
            const sure = await ctx.confirm({ title: paI18n.t("setup_privacy.forgetAsk", { site: row.site }), text: t.forgetText, ok: t.forget });
            if (sure) changeSites("browser/sites", { site: row.site, forget: "all" });
          }));
        }
        if (tools.childElementCount) item.append(tools);
        return item;
      }));
    }

    async function loadSites() {
      try {
        const value = await ctx.api("browser/sites");
        places.fold.node.hidden = browsing.row.hidden;
        places.error.hidden = true;
        renderSites(value.sites);
      } catch (error) {
        kit.failed(places.error, error, t);
      }
    }

    async function changeSites(path, change) {
      places.error.hidden = true;
      try {
        await ctx.api(path, change);
      } catch (error) {
        kit.failed(places.error, error, t);
      }
      loadSites();
    }

    renderRemarks();

    function show() {
      loadRemarks();
      loadQuiet();
      loadBrowsing().then(loadSites);
      if (permits.fold.node.open) changePermits();
    }

    return { node, show };
  });
})();
