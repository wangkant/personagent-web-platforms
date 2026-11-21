"use strict";
// The model control beside the composer's "+": the saved model's short name, and in its menu the thinking
// level (only for a model measured to reason and to take the setting), the service's other models, and what
// the calls spent with the account's balance, and the chat's context: how much of the model's window its last
// request took, 立即归纳 and 清空上下文. Settings' model page shows the same: the measured abilities and the usage,
// with the monthly limit (settings below). Reads /api/modelbar and /api/usage (panels/modelbar.py) and
// /api/chat/context (welcome.py); the chat (chat.js) makes one control per chat view.
window.paModelBar = (() => {
  // The problems a failed check names (endpoints.PROBLEMS), as Settings words them.
  const LEVELS = ["auto", "off", "standard", "deep"];
  // A list longer than this gets a search box.
  const SEARCH_FROM = 8;
  const POLL_MS = 2500;
  // The usage is read again after a reply at most this often.
  const USAGE_EVERY_MS = 60000;
  const SVG = "http://www.w3.org/2000/svg";
  const I = {
    down: '<path d="m6 9 6 6 6-6"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1.1 2V16h5v-.2c.1-.8.5-1.5 1.1-2A6 6 0 0 0 12 3z"/>',
    chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    gauge: '<path d="M4 19a9 9 0 1 1 16 0"/><path d="m12 14 4-5"/>',
    gear: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    out: '<path d="M14 4.5h5.5V10M19.5 4.5 11 13M18 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10"/>',
    layers: '<path d="m12 3 9 5-9 5-9-5 9-5z"/><path d="m3 13 9 5 9-5"/>',
    fold: '<path d="M4 6h16M4 10h10M4 14h16M4 18h7"/>',
    erase: '<path d="M7 20h11M5.5 15.5l9-9a2 2 0 0 1 2.8 0l1.2 1.2a2 2 0 0 1 0 2.8L12 17H8z"/>',
  };
  // The parts of a request the gauge names, in the order a request carries them (dm.chat_context).
  const PARTS = ["system", "tools", "memory", "summary", "recent"];

  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function svg(paths) {
    const node = document.createElementNS(SVG, "svg");
    node.setAttribute("viewBox", "0 0 24 24");
    node.setAttribute("aria-hidden", "true");
    node.setAttribute("class", "i");
    node.innerHTML = paths;
    return node;
  }
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");

  // ---- Numbers as people read them
  function tokens(n) {
    if (paI18n.lang === "zh") {
      if (n >= 1e8) return `${(n / 1e8).toFixed(n >= 1e9 ? 0 : 1)} 亿`;
      if (n >= 1e4) return `${(n / 1e4).toFixed(n >= 1e5 ? 0 : 1)} 万`;
      return n.toLocaleString("zh-CN");
    }
    if (paI18n.lang !== "en") return new Intl.NumberFormat(paI18n.locale, { notation: "compact", maximumFractionDigits: 1 }).format(n);
    if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(n >= 1e4 ? 0 : 1)}K`;
    return String(n);
  }
  function money(value, currency = "USD") {
    const sign = { USD: "$", CNY: "¥" }[currency] ?? `${currency} `;
    const digits = value === 0 ? 2 : value < 0.01 ? 4 : value < 0.1 ? 3 : 2;
    return `${sign}${value.toFixed(digits)}`;
  }
  // One period's figure: the cost where every token has a price, else tokens.
  function figure(period, w) {
    if (!period.tokens) return { main: w.noUse, sub: "" };
    const count = fill(w.tokens, { n: tokens(period.tokens) });
    return period.cost == null ? { main: count, sub: "" } : { main: money(period.cost), sub: count };
  }
  function ago(at) {
    const s = Math.max(0, Date.now() / 1000 - at);
    if (s < 90) return paI18n.t("modelbar.ago.now");
    return s < 5400 ? paI18n.t("modelbar.ago.m", { count: Math.round(s / 60) }) : paI18n.t("modelbar.ago.h", { count: Math.round(s / 3600) });
  }
  function link(href, text) {
    const a = el("a", "mb-link", text);
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    a.append(svg(I.out));
    return a;
  }

  // ---- The usage, as the popover and Settings both show it
  function usageNode(view, { full = false } = {}) {
    const w = paI18n.words("modelbar");
    const box = el("div", "mb-usage");
    const periods = el("div", "mb-periods");
    for (const key of ["today", "month"]) {
      const shown = figure(view.usage[key], w);
      const cell = el("div", "mb-period");
      cell.append(el("span", "mb-period-name", w[key]), el("span", "mb-period-main", shown.main));
      if (shown.sub) cell.append(el("span", "mb-period-sub", shown.sub));
      periods.append(cell);
    }
    box.append(periods);
    const month = view.usage.month;
    if (month.by.length) {
      box.append(el("p", "mb-split-title", w.byUse));
      const rows = el("div", "mb-split");
      const most = Math.max(...month.by.map((row) => row.tokens));
      for (const row of month.by) {
        const line = el("div", "mb-split-row");
        const bar = el("span", "mb-split-bar");
        bar.style.setProperty("--share", `${Math.max(2, (row.tokens / most) * 100)}%`);
        line.append(
          el("span", "mb-split-name", w.groups[row.group] || row.group),
          bar,
          el("span", "mb-split-value", row.cost == null ? tokens(row.tokens) : money(row.cost)),
        );
        rows.append(line);
      }
      box.append(rows);
      if (month.cost == null) box.append(el("p", "mb-note", w.noPrice));
    }
    const limit = view.limit;
    if (limit) {
      const used = Math.round(limit.share * 100);
      const block = el("div", `mb-limit mb-limit-${limit.state}`);
      const head = el("div", "mb-limit-head");
      const amount = limit.kind === "cost" ? money(limit.value) : fill(w.tokens, { n: tokens(limit.value) });
      head.append(el("span", "", `${w.limit} ${amount}`), el("span", "", fill(w.limitUsed, { p: used })));
      const meter = el("div", "mb-meter");
      meter.style.setProperty("--used", `${Math.min(100, used)}%`);
      block.append(head, meter);
      if (limit.state === "reached") block.append(el("p", "mb-note mb-note-strong", w.limitReached));
      box.append(block);
    }
    const money_ = el("div", "mb-balance");
    const b = view.balance;
    if (view.local) money_.append(el("p", "mb-note", w.local));
    else if (b && b.remaining != null) {
      const row = el("div", "mb-balance-row");
      row.append(el("span", "mb-balance-name", b.kind === "key" ? w.keyLeft : w.balance),
        el("span", "mb-balance-value", money(b.remaining, b.currency)));
      money_.append(row);
      if (full && b.at) money_.append(el("p", "mb-note", fill(w.updated, { t: ago(b.at) })));
    } else if (b && b.usedMonth != null) {
      const row = el("div", "mb-balance-row");
      row.append(el("span", "mb-balance-name", w.usedOnService), el("span", "mb-balance-value", money(b.usedMonth, b.currency)));
      money_.append(row);
    } else money_.append(el("p", "mb-note", view.balanceKnown ? w.balanceFailed : w.noBalance));
    if (b && b.freeLeft != null && b.freeLimit) money_.append(el("p", "mb-note", fill(w.freeLeft, { n: b.freeLeft, of: b.freeLimit })));
    if (view.console && !view.local) money_.append(link(view.console, w.console));
    box.append(money_);
    return box;
  }

  // ---- The composer's control
  function create(env) {
    const { ctx } = env;
    const w = () => paI18n.words("modelbar");
    let info = { configured: false };
    let models = null;
    let usage = null;
    let usageAt = 0;
    let open = false;
    let view = "main";
    let busyWith = "";
    let pollTimer = 0;
    let query = "";
    // The chat's context (dm.chat_context), read each time the menu opens; 立即归纳 under way.
    let held = null;
    let compacting = false;

    const wrap = el("div", "mb-wrap");
    const button = el("button", "mb-pill");
    button.type = "button";
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    const name = el("span", "mb-name");
    const tag = el("span", "mb-tag");
    button.append(name, tag, svg(I.down));
    const menu = el("div", "side-menu mb-menu");
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    wrap.append(button, menu);
    wrap.hidden = true;

    function paint() {
      wrap.hidden = !info.configured;
      if (!info.configured) return;
      const t = w();
      const level = info.effort;
      const offered = info.abilities?.efforts || [];
      name.textContent = busyWith ? t.switching : info.short;
      const showLevel = offered.length && level !== "auto" && offered.includes(level);
      tag.replaceChildren();
      tag.hidden = !showLevel;
      if (showLevel) tag.append(svg(I.bulb), el("span", "", t.levels[level]));
      button.title = `${info.model}${showLevel ? ` · ${t.levels[level]}` : ""}`;
      button.setAttribute("aria-label", `${t.open}: ${button.title}`);
      button.classList.toggle("busy", Boolean(busyWith));
    }

    async function load() {
      try {
        info = await ctx.api("modelbar");
      } catch {
        return;
      }
      paint();
      if (open) draw();
      clearTimeout(pollTimer);
      if (info.checking) pollTimer = setTimeout(load, POLL_MS);
      return info;
    }
    async function readUsage(fresh = false) {
      try {
        usage = await ctx.api(fresh ? "usage?fresh=1" : "usage");
        usageAt = Date.now();
      } catch {
        return usage;
      }
      notices();
      return usage;
    }
    // At 80 percent one quiet line, at the limit one more; each once a month.
    function notices() {
      const limit = usage?.limit;
      if (!limit || limit.state === "ok") return;
      const what = limit.state === "reached" ? "reached" : "near";
      if (limit.told.includes(what)) return;
      limit.told.push(what);
      env.flash(what === "reached" ? w().limitReached : fill(w().limitNear, { p: Math.round(limit.share * 100) }));
      ctx.api("usage/told", { what }).catch(() => {});
    }
    load();
    readUsage();

    // ---- The menu
    function item(icon, label, run, { hint = "", checked = null, more = false, cls = "", disabled = false } = {}) {
      const node = el("button", `side-menu-item mb-item ${cls}`.trim());
      node.type = "button";
      node.disabled = disabled;
      node.setAttribute("role", checked === null ? "menuitem" : "menuitemradio");
      if (checked !== null) node.setAttribute("aria-checked", String(checked));
      const text = el("span", "mb-text");
      text.append(el("span", "mb-label", label));
      if (hint) text.append(el("span", "mb-hint", hint));
      if (icon) node.append(svg(icon));
      node.append(text);
      if (checked) node.append(svg(I.check));
      if (more) node.append(svg(I.right));
      node.classList.toggle("on", Boolean(checked));
      node.classList.toggle("more", more);
      node.addEventListener("click", (event) => {
        event.stopPropagation();
        run();
      });
      return node;
    }
    const head = (text) => el("div", "mb-head", text);
    const rule = () => el("div", "mb-rule");
    function backRow(title) {
      const node = item(I.left, title, () => ((view = "main"), draw(), focusFirst()), { cls: "mb-back" });
      return node;
    }

    function mainView() {
      const t = w();
      const out = [];
      const offered = info.abilities?.efforts || [];
      if (offered.length) {
        const level = info.effort;
        out.push(head(t.thinking));
        const row = el("div", "mb-levels");
        row.setAttribute("role", "radiogroup");
        row.setAttribute("aria-label", t.thinking);
        for (const one of LEVELS.filter((l) => offered.includes(l))) {
          const seg = el("button", "side-menu-item mb-level mb-item", t.levels[one]);
          seg.type = "button";
          seg.setAttribute("role", "radio");
          seg.setAttribute("aria-checked", String(one === level));
          seg.addEventListener("click", (event) => {
            event.stopPropagation();
            setLevel(one);
          });
          row.append(seg);
        }
        out.push(row, el("p", "mb-level-hint", t.levelHints[offered.includes(level) ? level : "auto"]));
        out.push(rule());
      }
      out.push(head(t.model));
      out.push(item(I.chip, info.short, () => close(true), { checked: true, hint: info.checking ? t.checking : "" }));
      const recent = models?.recent || [];
      for (const m of recent.slice(0, 3)) out.push(item(null, short(m), () => choose(m), { cls: "mb-plain" }));
      if (models && models.models.length + recent.length > 3)
        out.push(item(null, t.more, () => ((view = "models"), (query = ""), draw(), focusFirst()), { more: true, cls: "mb-plain" }));
      out.push(item(I.gear, t.manage, () => (close(), ctx.go("setup/model"))));
      out.push(rule());
      const today = usage ? figure(usage.usage.today, t) : null;
      out.push(item(I.gauge, t.usage, () => showUsage(), {
        more: true,
        hint: today ? `${t.today} ${today.main}` : "",
      }));
      if (held) out.push(rule(), ...contextRows(held));
      return out;
    }

    // The gauge, its parts on hover or focus, and what can be done with the chat's context.
    function contextRows(c) {
      const t = w();
      const out = [];
      if (c.known) {
        const row = el("div", "side-menu-item mb-item mb-ctx");
        row.tabIndex = -1;
        row.setAttribute("role", "menuitem");
        const text = el("span", "mb-text");
        const meter = el("span", "mb-meter");
        meter.style.setProperty("--used", `${c.percent}%`);
        text.append(el("span", "mb-label", fill(t.context, { p: c.percent })), meter);
        const tip = el("div", "mb-ctx-tip");
        tip.id = `mb-ctx-${env.chat}`;
        tip.setAttribute("role", "tooltip");
        tip.hidden = true;
        for (const part of PARTS) {
          if (!c.parts[part]) continue;
          const line = el("div", "mb-ctx-part");
          line.append(el("span", "", t.contextParts[part]), el("span", "mb-ctx-n", tokens(c.parts[part])));
          tip.append(line);
        }
        tip.append(el("p", "mb-ctx-total", fill(c.estimated ? t.contextAbout : t.contextOf,
          { n: tokens(c.tokens), of: tokens(c.limit) })));
        row.setAttribute("aria-describedby", tip.id);
        row.append(svg(I.layers), text, tip);
        // Beside the menu, where the menu's own scrolling does not cut it off.
        const showTip = () => {
          tip.hidden = false;
          const at = row.getBoundingClientRect();
          const wide = tip.offsetWidth;
          const left = at.right + 8 + wide <= innerWidth - 8 ? at.right + 8 : Math.max(8, at.left - 8 - wide);
          tip.style.left = `${left}px`;
          tip.style.top = `${Math.max(8, Math.min(at.top, innerHeight - tip.offsetHeight - 8))}px`;
        };
        const hideTip = () => (tip.hidden = true);
        row.addEventListener("mouseenter", showTip);
        row.addEventListener("focus", showTip);
        row.addEventListener("mouseleave", hideTip);
        row.addEventListener("blur", hideTip);
        out.push(row);
      }
      out.push(item(I.fold, compacting ? t.compacting : t.compactNow, compact,
        { disabled: compacting || !c.compactable, hint: c.compactable || compacting ? "" : t.compactNone }));
      out.push(item(I.erase, t.clearContext, clear, { disabled: !c.held }));
      return out;
    }
    async function readContext() {
      held = await ctx.api(`chat/context?chat=${encodeURIComponent(env.chat)}`).catch(() => held);
    }
    async function compact() {
      compacting = true;
      draw();
      const done = await ctx.api("chat/compact", { chat: env.chat }).catch(() => null);
      compacting = false;
      if (!done || done.failed) env.flash(w().compactFailed);
      else if (done.compacted) await env.changed();
      await readContext();
      if (open && view === "main") draw();
    }
    async function clear() {
      close(true);
      const t = w();
      if (!(await ctx.confirm({ title: t.clearTitle, text: t.clearText, ok: t.clearOk }))) return;
      try {
        await ctx.api("chat/clear", { chat: env.chat });
      } catch {
        env.flash(t.clearFailed);
        return;
      }
      await env.changed();
    }

    function modelsView() {
      const t = w();
      const out = [backRow(t.more)];
      const all = [...(models?.recent || []), ...(models?.models || [])];
      if (all.length > SEARCH_FROM) {
        const field = el("label", "mb-search");
        const input = el("input", "mb-search-input");
        input.type = "text";
        input.placeholder = t.search;
        input.value = query;
        input.setAttribute("aria-label", t.search);
        input.addEventListener("input", () => {
          query = input.value;
          paintList();
        });
        input.addEventListener("keydown", (event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            list.querySelector(".mb-item")?.focus();
          } else if (event.key === "Enter") list.querySelector(".mb-item")?.click();
        });
        field.append(svg(I.search), input);
        out.push(field);
      }
      const list = el("div", "mb-list");
      const free = new Set(models?.free || []);
      function paintList() {
        const q = query.trim().toLowerCase();
        const match = (m) => !q || m.toLowerCase().includes(q);
        const rows = [];
        const recent = (models?.recent || []).filter(match);
        const rest = (models?.models || []).filter((m) => match(m) && !recent.includes(m));
        if (recent.length) rows.push(head(t.recent), ...recent.map(row));
        if (rest.length) rows.push(...(recent.length ? [head(t.all)] : []), ...rest.slice(0, 200).map(row));
        if (!rows.length) rows.push(el("p", "mb-empty", t.noMatch));
        list.replaceChildren(...rows);
        if (list.isConnected) place();
      }
      const row = (m) => item(null, short(m), () => choose(m), { hint: free.has(m) ? t.free : "", cls: "mb-plain" });
      paintList();
      out.push(list);
      return out;
    }

    function usageView() {
      const t = w();
      const out = [backRow(t.usage)];
      const body = el("div", "mb-usage-wrap");
      body.append(usage ? usageNode(usage) : el("div", "mb-loading pa-shimmer"));
      out.push(body, rule(), item(I.gear, t.settings, () => (close(), ctx.go("setup/model"))));
      return out;
    }

    function draw() {
      const t = view === "models" ? modelsView() : view === "usage" ? usageView() : mainView();
      menu.replaceChildren(...t);
      menu.classList.toggle("mb-wide", view !== "main");
      place();
    }
    // Upward from the button, as the "+" menu opens; where the chat leaves too little room above, beside it.
    function place() {
      const room = (wrap.closest(".chat") || document.body).getBoundingClientRect();
      const at = button.getBoundingClientRect();
      menu.style.maxHeight = menu.style.bottom = menu.style.left = "";
      const tall = menu.scrollHeight;
      if (tall <= at.top - room.top - 20) return;
      const height = room.height - 24;
      const bottom = Math.min(room.bottom - 12, room.top + 12 + Math.min(tall, height));
      menu.style.maxHeight = `${height}px`;
      menu.style.bottom = `${wrap.getBoundingClientRect().bottom - bottom}px`;
      menu.style.left = `${at.width + 8}px`;
    }
    const items = () => [...menu.querySelectorAll(".mb-item:not(:disabled), .mb-search-input")];
    const focusFirst = () => items()[view === "main" ? 0 : 1]?.focus();

    async function show(fromKeys = false) {
      if (open || busyWith) return;
      open = true;
      view = "main";
      button.setAttribute("aria-expanded", "true");
      wrap.classList.add("open");
      menu.hidden = false;
      draw();
      if (fromKeys) focusFirst();
      const [list] = await Promise.all([
        ctx.api("modelbar/models").catch(() => models),
        Date.now() - usageAt > USAGE_EVERY_MS ? readUsage() : null,
        readContext(),
      ]);
      models = list;
      if (open && view === "main") draw();
    }
    function close(focusBack = false) {
      if (!open) return;
      open = false;
      menu.hidden = true;
      wrap.classList.remove("open");
      button.setAttribute("aria-expanded", "false");
      if (focusBack) button.focus();
    }
    async function showUsage() {
      view = "usage";
      draw();
      focusFirst();
      await readUsage(true);
      if (open && view === "usage") draw();
    }

    async function setLevel(level) {
      try {
        info = await ctx.api("modelbar/effort", { level });
      } catch {
        env.flash(w().errors.generic);
      }
      paint();
      close(true);
    }

    async function choose(model) {
      close();
      const before = info.abilities;
      busyWith = model;
      paint();
      try {
        info = await ctx.api("modelbar/model", { model });
        models = null;
      } catch (error) {
        busyWith = "";
        paint();
        env.flash(w().errors[error.message] || w().errors.generic);
        return;
      }
      busyWith = "";
      paint();
      // What changed, said only for an ability the person was using.
      while (info.checking) {
        await new Promise((done) => setTimeout(done, POLL_MS));
        if (!(await load())) return;
      }
      const after = info.abilities;
      if (!before || !after) return;
      const lost = (key) => before[key] === "yes" && after[key] !== "yes" && after[key] !== "";
      const lines = [];
      if (lost("pictures") && env.pictures()) lines.push(w().lostPictures);
      if (lost("tools") && env.tools()) lines.push(w().lostTools);
      if (before.efforts.length && !after.efforts.length && info.effort !== "auto") lines.push(w().lostThinking);
      if (lines.length) env.flash(lines.join(" "));
    }

    button.addEventListener("click", (event) => (open ? close() : show(event.detail === 0)));
    button.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowUp" || event.key === "ArrowDown") && !open) {
        event.preventDefault();
        show(true);
      }
    });
    menu.addEventListener("keydown", (event) => {
      const list = items();
      const at = list.indexOf(document.activeElement);
      const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
      if (step && !(document.activeElement?.classList.contains("mb-search-input") && step === -1)) {
        event.preventDefault();
        list[(at + step + list.length) % list.length]?.focus();
      } else if (event.key === "Tab") close();
    });
    const outside = (event) => {
      if (open && !wrap.contains(event.target)) close();
    };
    const keys = (event) => {
      if (event.key === "Escape" && open) {
        event.stopPropagation();
        if (view !== "main") {
          view = "main";
          draw();
          focusFirst();
        } else close(true);
      }
    };
    const resized = () => close();
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", keys, true);
    addEventListener("resize", resized);

    return {
      node: wrap,
      render() {
        paint();
        if (open) draw();
      },
      // Read again: Settings may have changed the model, a reply may have spent.
      refresh() {
        load();
        if (Date.now() - usageAt > USAGE_EVERY_MS) readUsage();
      },
      // At the limit a message asks once a month before it goes; `retry` sends it after a yes.
      allow(retry) {
        const limit = usage?.limit;
        if (!limit || limit.state !== "reached" || limit.told.includes("confirmed")) return true;
        const t = w();
        ctx.confirm({ title: t.confirmTitle, text: t.confirmText, ok: t.confirmOk }).then((answer) => {
          if (!answer) return;
          limit.told.push("confirmed");
          ctx.api("usage/told", { what: "confirmed" }).catch(() => {});
          retry();
        });
        return false;
      },
      // A reply that failed for the account's balance: the sentence and the console's link.
      problem(code) {
        if (code !== "model_no_balance") return null;
        const node = el("p", "mb-problem", w().noBalanceChat);
        if (info.console) node.append(" ", link(info.console, w().topUp));
        return node;
      },
      drop() {
        clearTimeout(pollTimer);
        document.removeEventListener("pointerdown", outside, true);
        document.removeEventListener("keydown", keys, true);
        removeEventListener("resize", resized);
      },
    };
  }
  // The name without the vendor's prefix, unless the rest says nothing alone (openrouter/free).
  const short = (model) => {
    const name = model.split("/").pop().replace(/:free$/, "");
    return !name || ["free", "auto"].includes(name) ? model : name;
  };

  // ---- Settings › Model: the abilities and the usage, under what is connected.
  function settings(kit, section) {
    const { el: make, button, busy, ctx } = kit;
    const t = paI18n.words("modelbar");
    let info = null;
    let timer = 0;

    const abilityCard = kit.card(t.abilities);
    abilityCard.classList.add("mb-abilities");
    const recheck = button("setup-button-secondary setup-button-sm", t.recheck, async () => {
      busy(recheck, true, t.rechecking);
      try {
        info = await ctx.api("modelbar/check", {});
      } catch {
        // The rows say what is known.
      }
      busy(recheck, false, t.recheck);
      paintAbilities();
    });
    const abilityHead = make("div", "mb-card-head");
    abilityHead.append(abilityCard.firstChild, recheck);
    const rows = make("div", "mb-ability-rows");
    const foot = make("p", "setup-help mb-measured");
    abilityCard.append(abilityHead, rows, foot);

    const usageCard = kit.card(t.usageTitle);
    usageCard.classList.add("mb-usage-card");
    const usageBody = make("div", "mb-usage-body");
    const limit = kit.switchRow("usage-limit", t.limitSwitch, t.limitText, () => {
      amountRow.hidden = !limit.box.checked;
      if (!limit.box.checked) saveLimit(null);
      else amount.focus();
    });
    const amountRow = make("form", "mb-limit-form");
    amountRow.noValidate = true;
    const amount = kit.input("mb-limit-input");
    amount.inputMode = "decimal";
    amount.setAttribute("aria-label", t.limitAmount);
    const kind = make("select", "setup-input mb-limit-kind");
    for (const [value, label] of Object.entries(t.limitKinds)) {
      const option = make("option", "", label);
      option.value = value;
      kind.append(option);
    }
    kind.setAttribute("aria-label", t.limitAmount);
    const save = button("setup-button-secondary setup-button-sm", t.save);
    save.type = "submit";
    const note = make("p", "setup-foot-note");
    note.setAttribute("aria-live", "polite");
    amountRow.append(amount, kind, save, note);
    amountRow.addEventListener("submit", (event) => {
      event.preventDefault();
      const value = Number(amount.value.replace(",", "."));
      if (!(value > 0)) {
        note.textContent = t.invalidLimit;
        return;
      }
      saveLimit({ kind: kind.value, value: kind.value === "tokens" ? value * 1e4 : value });
    });
    usageCard.append(usageBody, limit.row, amountRow);
    section.append(abilityCard, usageCard);

    async function saveLimit(value) {
      note.textContent = "";
      try {
        paintUsage(await ctx.api("usage/limit", { limit: value }));
        if (value) note.textContent = t.saved;
      } catch (error) {
        note.textContent = kit.say(error);
      }
    }

    function paintAbilities() {
      clearTimeout(timer);
      abilityCard.hidden = !info?.configured;
      if (!info?.configured) return;
      const rec = info.abilities;
      rows.replaceChildren();
      for (const key of ["tools", "pictures", "thinking"]) {
        const value = info.checking ? "checking" : rec?.[key] || "";
        const row = make("div", "mb-ability");
        const words = make("div", "mb-ability-words");
        words.append(make("p", "mb-ability-name", t.names[key]));
        let why = "";
        if (value === "no") why = t.instead[key];
        else if (value === "unstable") why = t.unstable;
        else if (key === "thinking" && value === "yes" && rec.efforts.length && !rec.efforts.includes("off")) why = t.alwaysThinks;
        if (why) words.append(make("p", "mb-ability-why", why));
        const pill = make("span", `mb-state mb-state-${value || "none"}`,
          value === "checking" ? t.rechecking : t.states[value]);
        row.append(words, pill);
        rows.append(row);
      }
      const problem = rec?.error ? t.problems[rec.error] || t.problems.unreachable : "";
      foot.textContent = info.checking ? "" : problem ? `${t.checkFailed}${problem}` : rec?.at ? fill(t.measuredAt, { t: ago(rec.at) }) : "";
      foot.classList.toggle("setup-error", Boolean(problem));
      busy(recheck, info.checking, info.checking ? t.rechecking : t.recheck);
      if (info.checking) timer = setTimeout(load, POLL_MS);
    }

    function paintUsage(view) {
      usageCard.hidden = !kit.state.configured;
      usageBody.replaceChildren(usageNode(view, { full: true }));
      const set = view.limit;
      limit.box.checked = Boolean(set);
      amountRow.hidden = !set;
      if (set && document.activeElement !== amount) {
        kind.value = set.kind;
        amount.value = String(set.kind === "tokens" ? set.value / 1e4 : set.value);
      }
      if (!set && document.activeElement !== amount) kind.value = view.usage.month.cost == null && view.usage.month.tokens ? "tokens" : "cost";
    }

    async function load() {
      try {
        info = await ctx.api("modelbar");
      } catch {
        return;
      }
      paintAbilities();
    }
    return {
      async show() {
        await load();
        try {
          paintUsage(await ctx.api("usage?fresh=1"));
        } catch {
          usageCard.hidden = true;
        }
      },
    };
  }

  return { create, settings };
})();
