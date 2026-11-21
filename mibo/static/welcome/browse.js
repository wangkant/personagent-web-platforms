"use strict";
// The browser inside the app window: its one bar, in the window's title bar over the page (as compact browsers
// lay it: the tabs, the tab in front being the address, and the buttons), and the space under it, where
// window.py lays the site's own WebView (browser.py). The site never runs in this page and never sees its token.
// The conversation stays beside the page: the chat on screen, as it is (chat.js), in a column at the page's
// right, so the person talks to the character and watches it work in the page. A window too narrow for both
// lays that column over the page instead, opened and closed from the bar. While the page is in the chat's
// context (the chip in its box) what is sent is done in the page (turns "page" / "page_on").
// 最小化 or Settings put the page behind a chip: it goes on, with its sound and its history, and the chip
// brings it back.
// The bar's ··· menu (zoom, find, print, copy link, the system browser, a private tab, 最小化, downloads) is
// browsemenu.js.
// While the character operates the page for the person (web/operate.py), the chat's box carries the run: each
// step as it happens, a card when something needs their yes, and how it ended; a border round the page says
// whose it is.
(() => {
  const ICONS = {
    back: '<path d="M15 18l-6-6 6-6" />',
    forward: '<path d="M9 18l6-6-6-6" />',
    reload: '<path d="M20 12a8 8 0 1 1-2.34-5.66L20 8.5" /><path d="M20 3.5v5h-5" />',
    stop: '<rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />',
    close: '<path d="M6 6l12 12M18 6 6 18" />',
    minimize: '<path d="M6 18h12" />',
    // The chat beside the page: a window with its right column marked.
    beside: '<rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M14.5 4v16" /><path d="M17 9h1.5M17 12h1.5" />',
    // The page behind the chip, and its sound: playing, or muted.
    page: '<rect x="3.5" y="5" width="17" height="14" rx="2" /><path d="M3.5 9h17" />',
    sound: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" />',
    muted: '<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" /><path d="M16 9.5l5 5M21 9.5l-5 5" />',
    // A new private tab: the struck eye.
    private: '<path d="M3 3l18 18" /><path d="M10.6 5.1A9.8 9.8 0 0 1 12 5c5 0 8.5 4.5 9.5 7a13 13 0 0 1-2.6 3.8M6.6 6.6C4.4 8 3 10.2 2.5 12c1 2.5 4.5 7 9.5 7 1.9 0 3.6-.6 5-1.5" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />',
    // A step's mark: done, failed, stopped or cancelled.
    done: '<path d="M5 12.5l4.5 4.5L19 7.5" />',
    failed: '<path d="M7 7l10 10M17 7 7 17" />',
    stopped: '<path d="M7 12h10" />',
    plus: '<path d="M12 6v12M6 12h12" />',
    // The address field: a search, a site, a site reached without encryption.
    search: '<circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" />',
    globe: '<circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.5 2.6 3.5 5.4 3.5 8.5s-1 5.9-3.5 8.5c-2.5-2.6-3.5-5.4-3.5-8.5s1-5.9 3.5-8.5z" />',
    warn: '<path d="M12 4 21 19.5H3z" /><path d="M12 10v4.5M12 17.2v.3" />',
  };
  const say = (key) => paI18n.words("browse")[key];
  const col = () => paI18n.words("browse").col;
  const svg = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;
  // app.js has kept these from the fragment by now.
  const token = sessionStorage.getItem("mibo-setup-token") || "";
  const frameKey = sessionStorage.getItem("mibo-frame") || "";
  const stage = document.querySelector(".stage");
  const chatView = document.getElementById("chat-view");
  if (!frameKey || !stage) return;
  // The chat beside the page, CSS px (browse.css); what a site laid out for a desktop wants, which the window
  // grows to give it (browser.py); and the least a page keeps beside the chat before the chat lies over it instead.
  const BESIDE = parseFloat(getComputedStyle(document.body).getPropertyValue("--pa-beside"));
  const PAGE_WIDE = 1024;
  const PAGE_LEAST = 720;
  // The ends after which the page is the person's until they say to go on (browser.THEIRS).
  const THEIRS = ["handover", "asked", "paused", "cap", "waiting"];

  const HEADERS = { "Content-Type": "application/json", "X-Mibo-Setup-Token": token,
    "X-Mibo-Window": frameKey };
  function post(path, body) {
    return fetch(`/api/${path}`, {
      method: "POST",
      headers: HEADERS,
      body: JSON.stringify(body),
      credentials: "same-origin",
    }).catch(() => {});
  }
  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  const pane = el("section", "browse");
  pane.hidden = true;
  const bar = el("div", "browse-bar");
  // The page's frame is the border round it while the character works in it.
  const body = el("div", "browse-body");
  const frame = el("div", "browse-frame");
  const area = el("div", "browse-area");
  frame.append(area);
  // The address field. What the person types opens there: the site it names, else a search for the words
  // (panels/browser.py); the pages they read that match are offered as they type (browsed.py). Not focused, it
  // shows the site and the page's title, and marks a site reached without encryption.
  const field = el("div", "browse-field");
  const mark = el("span", "browse-mark");
  mark.hidden = true;
  const address = el("input", "browse-address");
  address.spellcheck = false;
  address.autocomplete = "off";
  address.setAttribute("role", "combobox");
  address.setAttribute("aria-autocomplete", "list");
  address.setAttribute("aria-controls", "browse-suggest");
  address.setAttribute("aria-expanded", "false");
  const where = el("span", "browse-where");
  const whereHost = el("span", "browse-where-host");
  const whereTitle = el("span", "browse-where-title");
  where.append(whereHost, whereTitle);
  field.append(mark, address, where);
  field.addEventListener("mousedown", (event) => {
    if (event.target === address) return;
    event.preventDefault();
    address.focus();
  });
  // The whole address selected once the field is focused, as browsers do (a click's mouseup would undo it).
  let picked = false;
  address.addEventListener("focus", () => {
    picked = true;
    address.select();
  });
  address.addEventListener("mouseup", (event) => {
    if (picked) event.preventDefault();
    picked = false;
  });
  address.addEventListener("blur", () => {
    close();
    address.value = page.url;
  });
  address.addEventListener("input", offer);
  address.addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && rows.length) {
      event.preventDefault();
      active = (active + (event.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
      drawSuggest();
    } else if (event.key === "Enter" && address.value.trim()) {
      const row = rows[active];
      go(row && !row.go ? row.url : address.value.trim());
    } else if (event.key === "Escape") {
      // Not a stop of the run (the page's own Esc): the first puts the address back, the second leaves the field.
      event.stopPropagation();
      if (address.value !== page.url) {
        close();
        address.value = page.url;
        address.select();
      } else address.blur();
    }
  });
  // Under the field: what Enter opens (a search for the words, or the site), then the pages read that match.
  const suggest = el("div", "browse-suggest browse-over");
  suggest.id = "browse-suggest";
  suggest.hidden = true;
  suggest.setAttribute("role", "listbox");
  let rows = [];
  let active = 0;
  let asked = 0;
  const bare = (url) => url.replace(/^https?:\/\/(www\.)?/, "");
  async function offer() {
    const text = address.value.trim();
    const mine = ++asked;
    // Enter before the answer comes opens what is typed.
    active = 0;
    if (!text) return close();
    const answer = await fetch(`/api/browser/suggest?q=${encodeURIComponent(text)}`, { headers: HEADERS, credentials: "same-origin" })
      .then((response) => (response.ok ? response.json() : null)).catch(() => null);
    if (mine !== asked || !answer || document.activeElement !== address) return;
    // In private mode nothing read before is shown.
    const pages = pane.classList.contains("private") ? [] : answer.pages.filter((row) => row.url !== answer.go.url);
    rows = [{ ...answer.go, go: true, text }, ...pages];
    active = 0;
    drawSuggest();
  }
  function option(row, n) {
    const node = el("div", "browse-option");
    node.id = `browse-option-${n}`;
    node.setAttribute("role", "option");
    node.innerHTML = svg(row.go ? (row.search ? "search" : "globe") : "page");
    const words = el("span", "browse-option-words");
    const main = row.go ? (row.search ? row.text : bare(row.url)) : row.title || bare(row.url);
    words.append(el("span", "browse-option-main", main),
      el("span", "browse-option-tail", row.go ? say(row.search ? "searchWeb" : "openAddress") : bare(row.url)));
    node.append(words);
    node.addEventListener("mousedown", (event) => {
      event.preventDefault();
      go(row.go ? row.text : row.url);
    });
    node.addEventListener("mousemove", () => {
      if (active === n) return;
      active = n;
      drawSuggest();
    });
    return node;
  }
  function drawSuggest() {
    suggest.replaceChildren(...rows.map(option));
    suggest.children[active]?.classList.add("active");
    suggest.children[active]?.setAttribute("aria-selected", "true");
    suggest.setAttribute("aria-label", say("suggestions"));
    address.setAttribute("aria-activedescendant", rows.length ? `browse-option-${active}` : "");
    address.setAttribute("aria-expanded", String(rows.length > 0));
    // Under the field, as wide as it or wide enough to read, within the pane.
    const box = field.getBoundingClientRect();
    const room = pane.getBoundingClientRect();
    const stageBox = stage.getBoundingClientRect();
    const wide = Math.min(room.width - 16, Math.max(box.width, 420));
    suggest.style.width = `${wide}px`;
    suggest.style.left = `${Math.max(room.left + 8, Math.min(box.left, room.right - 8 - wide)) - stageBox.left}px`;
    suggest.style.top = `${box.bottom + 6 - stageBox.top}px`;
    suggest.hidden = !rows.length;
    place();
  }
  function close() {
    asked++;
    rows = [];
    suggest.hidden = true;
    address.setAttribute("aria-expanded", "false");
    address.removeAttribute("aria-activedescendant");
    place();
  }
  function go(text) {
    post("browser/go", { text });
    address.blur();
  }
  // The site and the page's title, with the mark of a site reached without encryption (this computer's own
  // addresses are not marked, as in browsers).
  const LOCAL = /^(localhost|127(\.\d+){3}|\[::1\])$/;
  function drawWhere() {
    let url = null;
    try {
      url = new URL(page.url);
    } catch {
      // No page yet: the field shows its hint.
    }
    const host = url ? url.hostname.replace(/^www\./, "") : "";
    whereHost.textContent = host;
    whereTitle.textContent = page.title && page.title !== host ? page.title : "";
    field.title = page.title ? `${page.title}\n${page.url}` : page.url;
    mark.hidden = !(url?.protocol === "http:" && !LOCAL.test(url.hostname));
    mark.innerHTML = svg("warn");
    mark.append(el("span", "", say("notSecure")));
    mark.title = say("notSecureTitle");
  }
  const buttons = {};
  function button(name) {
    const node = el("button", `browse-btn browse-${name}`);
    node.type = "button";
    node.addEventListener("click", () => {
      const action = name === "reload" && pane.classList.contains("loading") ? "stop" : name;
      if (action === "minimize") return hide();
      if (action === "beside") return toggleBeside();
      if (action === "close") shut();
      post("browser/do", { action });
    });
    buttons[name] = node;
    return node;
  }
  function icon(node, name) {
    node.innerHTML = svg(name);
    node.title = say(name);
    node.setAttribute("aria-label", say(name));
  }
  // The menu's private tab opens beside the others (browsetabs.js marks it); while one is in front the address
  // field is tinted.
  function drawPrivate(on) {
    pane.classList.toggle("private", on);
  }
  // The site's zoom, shown a moment as it changes, at the field's end (the ··· menu changes it, and fits the site
  // to the pane's width: browsemenu.js).
  const zoomTag = el("span", "browse-zoom");
  zoomTag.hidden = true;
  field.append(zoomTag);
  let zoomTimer = null;
  // One row: back, forward, reload, the tabs (the one in front is the field), the run, the sound, the chat, ···,
  // 最小化 and close. It lies in the window's title bar, which shows nothing else over the page.
  bar.hidden = true;
  bar.append(button("back"), button("forward"), button("reload"));
  const drawTabs = window.paBrowseTabs({ pane, bar, field, post });
  // With the chat out of sight, a run under way is said in the bar: its state and the step it is on (a press
  // brings the chat), and 停止.
  const pill = el("div", "browse-run-pill");
  pill.hidden = true;
  const pillOpen = el("button", "browse-run-open");
  pillOpen.type = "button";
  const pillMode = el("span", "browse-run-mode");
  const pillStep = el("span", "browse-run-step");
  pillOpen.append(el("span", "browse-run-dot"), pillMode, pillStep);
  pillOpen.addEventListener("click", () => setBeside(true));
  const pillStop = el("button", "browse-btn browse-run-stop");
  pillStop.type = "button";
  pillStop.innerHTML = svg("stop");
  pillStop.addEventListener("click", () => post("browser/do", { action: "op_stop" }));
  pill.append(pillOpen, pillStop);
  bar.append(pill, button("mute"), button("beside"), button("minimize"), button("close"));
  document.getElementById("titlebar").prepend(bar);
  body.append(frame);
  pane.append(body);
  const drawMenu = window.paBrowseMenu({ pane, bar, stage, post, place, page: () => page, beside: setBeside, chat: () => chatNow(), hide });
  const drawStrip = window.paBrowseStrip({ pane, area, stage, post, place });
  // The suggestions lie over the page and over a chat laid on it.
  stage.append(pane, suggest);

  // ---- The run, in the chat's box: its state, a card waiting on the person, the steps, how it ended.
  const panel = el("div", "browse-run");
  const cardBox = el("div", "browse-run-card");
  const runBox = el("div", "browse-run-box");
  const head = el("div", "browse-run-head");
  const mode = el("span", "browse-run-mode");
  head.append(el("span", "browse-run-dot"), mode);
  const scroller = el("div", "browse-run-scroll");
  const list = el("ol", "browse-steps");
  scroller.append(list);
  // The foot: how the run ended and what the person may press.
  const foot = el("div", "browse-run-foot");
  const endLine = el("p", "browse-run-end");
  const actions = el("div", "browse-run-actions");
  foot.append(endLine, actions);
  runBox.append(head, scroller, foot);
  panel.append(cardBox, runBox);
  // The page in the chat's context: a chip in its box, taken out with its cross and put back with the button
  // that stands there then.
  const contextRow = el("div", "browse-context");
  // How much the character may do in the page by itself (acting.py), beside the chip: read again at each step.
  const modeSelect = el("select", "pa-select browse-mode");
  for (const value of ["off", "read", "auto"]) modeSelect.append(new Option("", value));
  modeSelect.addEventListener("change", () => post("browser/acting", { area: "browser", mode: modeSelect.value }));
  fetch("/api/browser/acting", { headers: HEADERS, credentials: "same-origin" })
    .then((response) => response.json())
    .then((data) => {
      modeSelect.value = data.modes.browser;
    })
    .catch(() => {});

  // The run as browser.py last told it ({task, held, end, why, note, steps, card}).
  let op = null;
  let shownCard = "";
  // The chat beside the page: the person's own choice ("open" / "closed", kept in this browser), what shows now
  // (null: beside in a window wide enough, out of the way in a narrow one), whether the window is too narrow
  // for both, and whether the page is in the chat's context.
  let choice = "";
  try {
    choice = localStorage.getItem("mibo-beside") || "";
  } catch {
    // Storage refused: it holds for this page only.
  }
  let wanted = choice ? choice === "open" : null;
  let narrow = false;
  let beside = false;
  let context = true;
  const running = () => Boolean(op && !op.end);
  // The run ended waiting on the person: what they type or press next goes on with it.
  const waits = () => Boolean(op) && THEIRS.includes(op.end);
  // The chat on screen, when one is (Settings has the stage otherwise).
  const chatNow = () => (chatView.hidden ? null : chatView.querySelector(":scope > .chat:not([hidden])"));
  function modeOf() {
    if (!op.end) {
      if (op.held === "stopped") return "stopping";
      if (op.wait) return "theirs";
      if (op.held) return "theirs";
      return op.card ? "waiting" : "acting";
    }
    if (op.end === "done") return "done";
    if (op.end === "failed") return "failed";
    if (THEIRS.includes(op.end)) return "theirs";
    return "stopped";
  }
  // The steps as the list shows them: those taken for one purpose (the loop's note, kept with each step by
  // browser.py) stand together under it; a step with no note stands by its own words.
  function stepRows(steps) {
    const rows = [];
    for (const step of steps) {
      const last = rows.at(-1);
      if (step.note && last?.acts && last.note === step.note) last.acts.push(step);
      else rows.push(step.note ? { note: step.note, acts: [step] } : step);
    }
    return rows;
  }
  // One row: what it is for as its title, each action beneath in the person's words, and under an action that did
  // not work, why. Its mark is how its last action stands.
  function stepNode(row) {
    const acts = row.acts || [row];
    const state = acts.at(-1).state;
    const node = el("li", `browse-step is-${state}`);
    const mark = el("span", "browse-step-mark");
    if (ICONS[state]) mark.innerHTML = svg(state);
    if (state === "cancelled") mark.innerHTML = svg("stopped");
    const words = el("div", "browse-step-words");
    if (row.acts) words.append(el("span", "", row.note));
    for (const act of acts) {
      words.append(el("span", row.acts ? "browse-step-act" : "", act.text + (act.n > 1 ? col().times.replace("{n}", act.n) : "")));
      if (act.why) words.append(el("span", `browse-step-why${act.state === "failed" ? " is-failed" : ""}`, act.why));
    }
    node.append(mark, words);
    return node;
  }
  // The asking cards' one look (chat.js cardHead): an icon, the question, one faint line.
  const CARD_ICONS = {
    out: '<path d="M4 12l16-8-6 16-2.5-6.5z" /><path d="M11.5 13.5 20 4" />',
    like: '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z" />',
    ask: '<circle cx="12" cy="12" r="8.5" /><path d="M12 7.5v5.5M12 16.2v.3" />',
    key: '<circle cx="8" cy="15" r="4" /><path d="M10.8 12.2 20 3M16 7l3 3M14 9l2 2" />',
  };
  function cardHead(icon, title, meta) {
    const head = el("div", "op-card-head");
    const tile = el("span", "op-card-icon");
    tile.innerHTML = `<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${CARD_ICONS[icon]}</svg>`;
    const text = el("div", "op-card-words");
    text.append(el("p", "op-card-title", title));
    if (meta) text.append(el("p", "op-card-meta", meta));
    head.append(tile, text);
    return head;
  }
  // The card for something with an effect outside (web/gate.py): its act as the question, then only what
  // matters, each once; the control it presses is ringed on the page itself. A send, a comment, a like or follow
  // and a post answer 本次允许 / 始终允许 / 不允许; paying, deleting and account changes only 确认 / 取消, every
  // time. Drawn once per card.
  function drawCard(card) {
    if ((card?.id || "") === shownCard) return;
    shownCard = card?.id || "";
    cardBox.replaceChildren();
    if (!card) return;
    if (card.kind === "auto_sign_in") return drawAutoCard(card);
    if (card.kind === "wait") return drawWaitCard(card);
    const words = col().card;
    const node = el("div", "op-card browse-card");
    const where = [card.to ? (card.kind === "send" ? words.to.replace("{to}", card.to) : card.to) : card.kind === "send" ? words.noTo : "", card.site]
      .filter(Boolean).join(" · ");
    const icon = card.skippable ? (card.kind === "like" ? "like" : "out") : "ask";
    node.append(cardHead(icon, words.titles[card.kind] || words.titles.submit, where));
    // The step's own line of what it does (surface.py's act), as the character wrote it.
    if (card.what) node.append(el("p", "op-card-why", card.what));
    if (card.text) node.append(el("blockquote", "browse-card-quote", card.text));
    if (card.button && card.ringed) node.append(el("p", "browse-card-ring", words.ring.replace("{button}", card.button)));
    else if (card.button) node.append(el("p", "browse-card-ring", card.button));
    const row = el("div", "op-card-buttons");
    // A file the page asks for: the one the person named (its path quoted), or one they choose in the system's
    // dialog, which the app opens (pick).
    const named = card.kind === "upload" && Boolean(card.text);
    const choices = card.kind === "upload"
      ? [...(named ? [["upload", "pa-btn-primary", true, false]] : []),
        [named ? "chooseOther" : "choose", named ? "pa-btn-secondary" : "pa-btn-primary", true, false, true],
        ["cancel", named ? "pa-btn-ghost" : "pa-btn-secondary", false, false]]
      : card.skippable
        ? [["once", "pa-btn-primary", true, false], ["always", "pa-btn-secondary", true, true], ["no", "pa-btn-ghost", false, false]]
        : [["confirm", "pa-btn-primary", true, false], ["cancel", "pa-btn-secondary", false, false]];
    for (const [name, look, yes, never, pick = false] of choices) {
      const b = el("button", `pa-btn ${look} pa-btn-sm`, words[name]);
      b.type = "button";
      if (never) b.title = words.alwaysTitle;
      b.addEventListener("click", async () => {
        for (const other of row.querySelectorAll("button")) other.disabled = true;
        const response = await post("browser/confirm", { id: card.id, yes, never, pick });
        const ok = response && (await response.json().catch(() => ({}))).ok;
        if (!ok && shownCard === card.id) node.append(el("p", "op-card-told", words.gone));
        // The card an act ended waiting on: the answer is kept for its act, for the next turn to find.
        if (ok && op?.end === "waiting" && op.card?.id === card.id) {
          op = { ...op, card: null, end: yes ? op.end : "declined" };
          drawRun();
          if (yes) goOn(col().goOnPaused);
        }
      });
      row.append(b);
    }
    node.append(row);
    cardBox.append(node);
  }
  // The 自动登录 card: the same look, with the three answers.
  function drawAutoCard(card) {
    const words = col().signin;
    const node = el("div", "op-card browse-card");
    const head = cardHead("key", words.autoTitle.replace("{site}", card.site), words.autoMeta);
    const row = el("div", "op-card-buttons");
    const choices = [["once", "pa-btn-primary"], ["always", "pa-btn-secondary"], ["no", "pa-btn-ghost"]];
    for (const [said, look] of choices) {
      const b = el("button", `pa-btn ${look} pa-btn-sm`, words[said]);
      b.type = "button";
      b.addEventListener("click", async () => {
        for (const other of row.querySelectorAll("button")) other.disabled = true;
        const response = await post("signins/card", { id: card.id, answer: said });
        const ok = response && (await response.json().catch(() => ({}))).ok;
        if (!ok && shownCard === card.id) node.append(el("p", "op-card-told", words.gone));
      });
      row.append(b);
    }
    node.append(head, row);
    cardBox.append(node);
  }
  // The wait for the person's sign-in or check: what the site asks, in one sentence, the site's own sign-in (where
  // the page is not one already) and going on without the site. Its words come with it (panels/signins.py).
  function drawWaitCard(card) {
    const node = el("div", "op-card browse-card");
    const row = el("div", "op-card-buttons");
    const choices = [...(card.to ? [["go", "pa-btn-primary"]] : []), ["skip", card.to ? "pa-btn-secondary" : "pa-btn-primary"]];
    for (const [action, look] of choices) {
      const b = el("button", `pa-btn ${look} pa-btn-sm`, card[action === "go" ? "go" : "pass"]);
      b.type = "button";
      b.addEventListener("click", () => {
        if (action === "skip") for (const other of row.querySelectorAll("button")) other.disabled = true;
        post("signins/wait", { action });
      });
      row.append(b);
    }
    node.append(cardHead(card.wall === "verification" ? "ask" : "key", card.say, card.site), row);
    cardBox.append(node);
  }
  function endWords() {
    const words = col();
    if (op.end === "handover") {
      const [kind, field] = (op.why || "stuck").split(/:(.*)/);
      const said = words.handover[kind === "sign_in_overlay" ? "sign_in" : kind] || words.handover.stuck;
      return said.replace("{field}", field || "");
    }
    // Waiting on a card: the act it waits on, as its step says it.
    if (op.end === "waiting") return op.steps.findLast((step) => step.state === "waiting")?.text || words.modes.waiting;
    const said = words.ends[op.end] || words.ends.failed;
    // The character's own one sentence of how it went, where it wrote one for the person; at the step limit,
    // what is left.
    const note = String(op.note || "").trim();
    if (["asked", "cap"].includes(op.end) || (["done", "failed"].includes(op.end) && note && !note.startsWith("["))) {
      return ["asked", "cap"].includes(op.end) ? `${said}${note}` : note;
    }
    return said;
  }
  function footButton(className, text, run, title = "") {
    const node = el("button", className, text);
    node.type = "button";
    if (title) node.title = title;
    node.addEventListener("click", run);
    return node;
  }
  // 继续: sent as the person's own message in the chat beside the page (what they typed there, else the
  // button's word), and it goes on with the run that waits (paBeside.take).
  function goOn(text) {
    const input = chatNow()?.querySelector(".chat-input");
    if (!input) return;
    context = true;
    drawContext();
    if (!input.value.trim()) {
      input.value = text;
      input.dispatchEvent(new Event("input"));
    }
    // Sent as Enter in the box sends: the form's own submit would stop a reply on its way instead.
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  }
  function drawFoot() {
    const words = col();
    const buttons = [];
    const stop = () => {
      const node = footButton("pa-btn pa-btn-secondary pa-btn-sm", words.stop, () => post("browser/do", { action: "op_stop" }));
      node.append(el("kbd", "browse-key", "Esc"));
      return node;
    };
    foot.classList.toggle("ended", !running() || Boolean(op.wait));
    endLine.hidden = false;
    if (running() && op.wait) {
      // Signing in is theirs, in the page: the card above says what the site asks; here only Stop.
      endLine.hidden = true;
      buttons.push(stop());
    } else if (running()) {
      endLine.hidden = true;
      buttons.push(footButton("pa-btn pa-btn-ghost pa-btn-sm", words.take,
        () => post("browser/do", { action: "op_take" }), words.takeTitle), stop());
    } else {
      endLine.textContent = endWords();
      // 继续: the page is theirs for a moment and they say when they are done (not where the page cannot be
      // worked, nor where it asked something or a card waits: their answer is typed in the chat's box or pressed
      // on the card).
      if (!["asked", "waiting"].includes(op.end) && op.why !== "drawn" && op.why !== "empty") {
        const text = ["paused", "cap"].includes(op.end) ? words.goOnPaused : words.goOn;
        buttons.push(footButton("pa-btn pa-btn-primary pa-btn-sm", text, () => goOn(text)));
      }
    }
    actions.replaceChildren(...buttons);
  }
  // The run where the chat is: in its box while the run is under way or waits on the person (once it has ended
  // otherwise, the chat's reply carries its steps), and in the bar while the chat is out of sight.
  function drawRun() {
    const live = !pane.hidden && Boolean(op) && (running() || waits());
    const now = live ? modeOf() : "";
    for (const name of ["acting", "waiting", "theirs", "stopping"]) {
      for (const node of [pane, panel, pill]) node.classList.toggle(`op-${name}`, now === name);
    }
    pane.classList.toggle("op-live", live);
    const words = col();
    const said = !live ? "" : running() && op.wait
      ? words.signin.modes[op.wait.wall === "verification" ? "verification" : "sign_in"] : words.modes[now];
    pill.hidden = !live || beside;
    if (!pill.hidden) {
      pillMode.textContent = said;
      // What the step under way is for (the loop's note), else its own words.
      const step = op.steps.at(-1);
      pillStep.textContent = running() ? step?.note || step?.text || words.thinking : endWords();
      pillOpen.title = `${said}\n${pillStep.textContent}\n${words.open}`;
      pillStop.hidden = !running();
      pillStop.title = `${words.stop} (Esc)`;
      pillStop.setAttribute("aria-label", words.stop);
    }
    const form = live && beside ? chatNow()?.querySelector(".chat-composer") : null;
    if (!form) return panel.remove();
    if (panel.nextElementSibling !== form) form.before(panel);
    mode.textContent = said;
    // Kept at the end as it grows, unless the person scrolled back to read.
    const atEnd = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 12;
    list.replaceChildren(...stepRows(op.steps).map(stepNode));
    const last = op.steps.at(-1);
    if (running() && !op.card && !op.held && !op.wait && last?.state !== "doing") {
      list.append(stepNode({ text: words.thinking, state: "doing" }));
    }
    drawCard(running() ? op.card || (op.wait && { ...op.wait, id: `wait:${op.wait.site}:${op.wait.say}`, kind: "wait" })
      : op.end === "waiting" ? op.card : null);
    drawFoot();
    if (atEnd && !op.card) scroller.scrollTop = scroller.scrollHeight;
  }
  function showOp(next) {
    const before = op;
    op = next;
    const needs = (run) => Boolean(run) && Boolean(run.card || run.wait || THEIRS.includes(run.end));
    // A run begins: in a window wide enough the chat shows it beside the page; in a narrow one the chat gets out
    // of the page's way while it acts, and comes back when the run needs the person or has ended.
    if (next && !next.end && (!before || before.end)) wanted = !narrow;
    if (narrow && next && !next.end && needs(before) && !needs(next)) wanted = false;
    if ((needs(next) && !needs(before)) || (narrow && next?.end && before && !before.end)) wanted = true;
    layout();
  }
  // Esc in the app page stops a run (in the page itself, browser.py hears it). Ctrl L goes to the address, as in
  // any browser.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && running() && !pane.hidden) post("browser/do", { action: "op_stop" });
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === "l" && !pane.hidden) {
      event.preventDefault();
      address.focus();
    }
  });

  // ---- The chat beside the page
  function drawContext() {
    const form = beside ? chatNow()?.querySelector(".chat-composer") : null;
    if (!form) return contextRow.remove();
    if (contextRow.parentElement !== form) form.prepend(contextRow);
    const modes = say("modes");
    modeSelect.title = modes.title;
    modeSelect.setAttribute("aria-label", modes.title);
    for (const option of modeSelect.options) option.textContent = modes[option.value];
    if (!context) {
      const add = footButton("browse-context-add", say("contextAdd"), () => {
        context = true;
        drawContext();
      }, say("contextAddTitle"));
      add.insertAdjacentHTML("afterbegin", svg("plus"));
      return contextRow.replaceChildren(add, modeSelect);
    }
    const chip = el("span", "browse-context-chip");
    chip.title = `${say("context")}\n${page.url}`;
    chip.innerHTML = svg("page");
    chip.append(el("span", "", page.title || (page.url ? new URL(page.url).hostname : "")));
    const out = footButton("browse-context-x", "", () => {
      context = false;
      drawContext();
    }, say("contextOff"));
    out.innerHTML = svg("close");
    out.setAttribute("aria-label", say("contextOff"));
    chip.append(out);
    contextRow.replaceChildren(chip, modeSelect);
  }
  // Where everything is: the chat beside the page or over it, the chip and the run in its box, the WebView.
  function layout() {
    narrow = stage.clientWidth - BESIDE < PAGE_LEAST;
    beside = !pane.hidden && Boolean(chatNow()) && (wanted ?? !narrow);
    document.body.classList.toggle("beside", beside);
    document.body.classList.toggle("beside-over", beside && narrow);
    buttons.beside.classList.toggle("on", beside);
    buttons.beside.setAttribute("aria-pressed", String(beside));
    buttons.beside.title = say(beside ? "besideOn" : "beside");
    buttons.beside.setAttribute("aria-label", buttons.beside.title);
    drawContext();
    drawRun();
    if (rows.length) drawSuggest();
    place();
  }
  function setBeside(on) {
    wanted = on;
    // Settings has the stage: the chat comes to it first (app.js).
    if (on && !chatNow()) window.paAsk?.("");
    layout();
    if (on) chatNow()?.querySelector(".chat-input")?.focus();
  }
  // The bar's button: the person's own choice, kept.
  function toggleBeside() {
    setBeside(!beside);
    choice = wanted ? "open" : "closed";
    try {
      localStorage.setItem("mibo-beside", choice);
    } catch {
      // Storage refused: it holds for this page only.
    }
  }
  // What a message sent from the chat beside the page carries (chat.js): done in the page while it is in the
  // chat's context, going on with the run that waits on the person.
  window.paBeside = { take: () => (beside && context ? [waits() ? "page_on" : "page"] : []) };
  // Another chat or a new one, Settings and back: the chip and the run follow the chat on screen.
  new MutationObserver((records) => {
    if (records.some((record) => record.target === chatView || record.target.parentElement === chatView)) layout();
  }).observe(chatView, { childList: true, attributes: true, attributeFilter: ["hidden"], subtree: true });

  // 最小化 or Settings: the page goes on behind this chip, on the right. Its title brings the page back as it
  // was, the speaker mutes it while it plays, the cross closes it. After a restart, or a reload with no page
  // open, it offers the page open last.
  const chip = el("div", "browse-return");
  chip.hidden = true;
  const chipMute = el("button", "browse-btn browse-sound");
  chipMute.type = "button";
  chipMute.addEventListener("click", () => post("browser/do", { action: "mute" }));
  const chipGo = el("button", "browse-return-go");
  chipGo.type = "button";
  chipGo.addEventListener("click", () => (kept ? post("browser/go", { text: kept.url }) : show()));
  const chipClose = el("button", "browse-btn browse-return-close");
  chipClose.type = "button";
  chipClose.addEventListener("click", () => {
    shut();
    post("browser/do", { action: "close" });
  });
  chip.append(chipMute, chipGo, chipClose);
  stage.append(chip);
  // Whether a page is open (in front or behind the chip), its address and title, its sound, and the page open last.
  let holding = false;
  let page = { url: "", title: "" };
  let sound = { playing: false, muted: false };
  let kept = null;
  function drawChip() {
    chip.hidden = !(kept || (holding && pane.hidden));
    const shown = kept || page;
    const name = shown.title || (shown.url ? new URL(shown.url).hostname : "");
    chipGo.innerHTML = svg("page");
    chipGo.append(el("span", "", kept ? say("reopen").replace("{title}", name) : name));
    chipGo.title = `${say(kept ? "reopenTitle" : "return")}\n${shown.url}`;
    chipMute.hidden = Boolean(kept) || !(sound.playing || sound.muted);
    icon(chipClose, "close");
  }
  // The speaker, in the bar and on the chip, while the page plays sound or is muted.
  function drawSound() {
    for (const node of [buttons.mute, chipMute]) {
      node.innerHTML = svg(sound.muted ? "muted" : "sound");
      node.title = say(sound.muted ? "unmute" : "mute");
      node.setAttribute("aria-label", node.title);
      node.setAttribute("aria-pressed", String(sound.muted));
    }
    buttons.mute.hidden = !(sound.playing || sound.muted);
  }
  // The pane comes to the front, or leaves it: the window makes room for the site, or takes back the size it
  // had for the chat.
  function front(on) {
    if (pane.hidden === !on) return;
    if (!on && document.activeElement === address) address.blur();
    pane.hidden = !on;
    bar.hidden = !on;
    placed = "";
    fold(on);
    drawChip();
    layout();
    room(on);
  }
  // The sidebar folds to its rail while the page is in front, and unfolds when it leaves (app.js decides, by the
  // person's setting): told here whether the screen has no room for the sidebar, a site laid out for a desktop
  // and the chat beside it.
  function fold(on) {
    const side = parseFloat(getComputedStyle(document.body).getPropertyValue("--pa-side-width"));
    window.paSideFold?.(on, side + PAGE_WIDE + (choice === "closed" ? 0 : BESIDE) > screen.availWidth);
  }
  function show() {
    post("browser/do", { action: "return" });
    // Back from Settings: the chat comes to the stage with the page (app.js).
    if (!chatNow()) window.paAsk?.("");
    front(true);
  }
  function hide() {
    post("browser/do", { action: "hide" });
    front(false);
  }

  function words() {
    for (const name of ["back", "forward", "minimize", "close"]) icon(buttons[name], name);
    buttons.beside.innerHTML = svg("beside");
    drawPrivate(pane.classList.contains("private"));
    icon(buttons.reload, pane.classList.contains("loading") ? "stop" : "reload");
    address.setAttribute("aria-label", say("address"));
    address.placeholder = say("typeAddress");
    drawWhere();
    drawSound();
    drawChip();
    layout();
  }

  // Where the WebView goes: the page's space inside its frame, in CSS px, what of this page lies over it (the chat
  // in a narrow window, the suggestions and the ··· menu with their shadow: the WebView is cut there, not made
  // smaller), and the
  // page's device pixels per px. Told again only when it changed.
  let placed = "";
  function place() {
    if (pane.hidden) return;
    const box = area.getBoundingClientRect();
    const cuts = [];
    if (beside && narrow) {
      const left = Math.max(box.left, Math.round(chatView.getBoundingClientRect().left));
      if (left < box.right) cuts.push([left - box.left, 0, box.right - left, box.height]);
    }
    for (const over of stage.querySelectorAll(".browse-over")) {
      if (over.hidden) continue;
      const r = over.getBoundingClientRect();
      const [x, y] = [Math.max(box.left, r.left - 12), Math.max(box.top, r.top)];
      const [x2, y2] = [Math.min(box.right, r.right + 12), Math.min(box.bottom, r.bottom + 16)];
      if (x2 > x && y2 > y) cuts.push([x - box.left, y - box.top, x2 - x, y2 - y]);
    }
    const told = JSON.stringify({ box: [box.left, box.top, box.width, box.height], cuts, scale: devicePixelRatio });
    if (told === placed) return;
    placed = told;
    post("browser/place", JSON.parse(told));
  }
  new ResizeObserver(layout).observe(stage);
  new ResizeObserver(place).observe(area);
  // The window as wide as the site and the chat beside it want, where the screen allows (browser.py grows it
  // once, and keeps the size the person gives it after); 0 when the page leaves the front.
  function room(on) {
    const besideIt = beside && !narrow;
    const wide = innerWidth - area.clientWidth + PAGE_WIDE + (besideIt || choice === "closed" ? 0 : BESIDE);
    post("browser/room", { wide: on ? wide : 0, scale: devicePixelRatio });
  }

  function shut() {
    holding = false;
    kept = null;
    sound = { playing: false, muted: false };
    front(false);
    drawChip();
  }

  // window.py: what the bar shows, and whether the pane is open.
  window.paBrowse = (state) => {
    if (state.open === false) return shut();
    if ("url" in state) page = { url: state.url, title: state.title || "" };
    if ("private" in state) {
      // A private page stays out of the chat until the person puts it in.
      if (state.open || state.private !== pane.classList.contains("private")) context = !state.private;
      drawPrivate(state.private);
    }
    if (state.open) {
      holding = true;
      kept = null;
      wanted = choice ? choice === "open" : null;
      front(true);
    }
    if ("hidden" in state) {
      holding = true;
      front(!state.hidden);
    }
    if ("kept" in state && !holding) kept = state.kept;
    if ("sound" in state) sound = { playing: state.sound, muted: state.muted };
    if ("url" in state && document.activeElement !== address) address.value = state.url;
    // Ctrl L in the site (browser.py): to the address.
    if (state.focus === "address") address.focus();
    if ("back" in state) buttons.back.disabled = !state.back;
    if ("forward" in state) buttons.forward.disabled = !state.forward;
    if ("loading" in state) pane.classList.toggle("loading", state.loading);
    if ("zoom" in state) {
      zoomTag.textContent = `${state.zoom}%`;
      if (state.zoom_show) {
        zoomTag.hidden = false;
        clearTimeout(zoomTimer);
        zoomTimer = setTimeout(() => (zoomTag.hidden = true), 1500);
      }
    }
    if ("operating" in state) showOp(state.operating);
    drawTabs(state);
    drawMenu(state);
    drawStrip(state);
    words();
  };
  // The sidebar while a page is in front: Settings needs the stage, so the page goes behind its chip; a chat,
  // or a new one, shows beside the page (not the sidebar's own fold button).
  document.getElementById("side")?.addEventListener("click", (event) => {
    if (!event.isTrusted || pane.hidden || !event.target.closest("button") || event.target.closest("#side-toggle")) return;
    if (event.target.closest("#side-settings")) return hide();
    wanted = true;
    setTimeout(layout);
  }, true);
  // A player full screen (window.py makes it the whole monitor) whose card is hidden, another chat
  // or page shown, gives full screen up: the window comes back and nothing is left over the page.
  document.addEventListener("fullscreenchange", () => {
    const full = document.fullscreenElement;
    if (!full) return;
    const watch = new ResizeObserver(() => {
      if (document.fullscreenElement !== full) watch.disconnect();
      else if (!full.getClientRects().length) document.exitFullscreen().catch(() => {});
    });
    watch.observe(full);
  });
  words();
  // This page loaded again: a page still open comes back behind its chip, or the page open last is offered.
  post("browser/do", { action: "loaded" });
})();
