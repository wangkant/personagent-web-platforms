"use strict";
// The app behind the pet: a sidebar as chat apps have one, the chats it opens, Settings,
// and the window's own bar. At the top of the sidebar is the character's main chat, where
// it also brings what it found; under it the plain chats, newest first.
// chat.js draws the chats, setup.js Settings; onboard.js, when present, takes the whole
// window on the first run.
(() => {
  const $ = (id) => document.getElementById(id);
  // As welcome.PET_FIGURES: the four characters' (one's own is Mibo too).
  const FIGURES = ["mibo", "chatgpt", "deepseek", "claude"];
  const MAIN = "main";
  // The list of chats, and whether the main chat has something new, are asked for this often; in
  // its own window the page is told when a chat changes (paChats, window.py) and asks only now and then.
  const CHATS_MS = 5000;
  const TOLD_MS = 60000;
  // What the character is doing now is asked for this often.
  const NOW_MS = 30000;
  const TITLE_MAX = 60;
  // Below this width the sidebar is a rail of icons; opened, it lies over the page.
  const NARROW = matchMedia("(max-width: 999.98px)");
  // A figure's frame is 320 by 380; its head sits in this square, as parts of the frame's width,
  // and its head and shoulders in this larger one.
  const FRAME_TALL = 380 / 320;
  const HEAD = { size: 0.62, left: 0.19, top: 0.12 };
  const BUST = { size: 0.8, left: 0.1, top: 0.07 };
  const DAY_MS = 86400000;
  // A painted figure is a sheet of four frames: in a row, two by two, or in a column.
  const SHEETS = [4, 2, 1];

  // The token and the window's key come once in the fragment.
  const fragment = new URLSearchParams(location.hash.slice(1));
  const token = fragment.get("token") || sessionStorage.getItem("mibo-setup-token") || "";
  const frameKey = fragment.get("frame") || sessionStorage.getItem("mibo-frame") || "";
  if (fragment.has("frame")) sessionStorage.setItem("mibo-frame", frameKey);
  if (fragment.has("token")) sessionStorage.setItem("mibo-setup-token", token);
  if (fragment.has("token") || fragment.has("frame")) history.replaceState(null, "", location.pathname);
  // A cross-site request cannot add this header.
  const authHeaders = () => ({ "X-Mibo-Setup-Token": token });

  // `keepalive`: the request still goes out while the page closes.
  // A Blob body (a picture) goes as it is.
  // `signal`: an AbortSignal that ends the wait (a chat reply's stop button).
  async function api(path, body, { keepalive = false, signal } = {}) {
    const raw = body instanceof Blob;
    const response = await fetch(`/api/${path}`, {
      keepalive,
      signal,
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": raw ? "application/octet-stream" : "application/json",
        ...authHeaders(),
        // Settings › Other programs grants a permission only from this window's own page.
        ...(frameKey && (path === "window" || path === "programs") ? { "X-Mibo-Window": frameKey } : {}),
      },
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
      credentials: "same-origin",
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok || value.error) throw new Error(value.error || value.detail || "generic");
    return value;
  }

  // A picture behind the API needs the page's token, which an <img> cannot send: it comes as a blob.
  async function picture(path) {
    const response = await fetch(path, { headers: authHeaders(), credentials: "same-origin" });
    if (!response.ok) throw new Error("picture_gone");
    return URL.createObjectURL(await response.blob());
  }
  // A text file attached to a message, the same way.
  async function attachedText(path) {
    const response = await fetch(path, { headers: authHeaders(), credentials: "same-origin" });
    if (!response.ok) throw new Error("file_gone");
    return response.text();
  }

  // Remembered in this browser only: a lost value costs a dot or an open sidebar, nothing more.
  function stored(key) {
    try {
      return localStorage.getItem(key) || "";
    } catch {
      return "";
    }
  }
  function store(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch {
      // Storage refused: it holds for this page only.
    }
  }

  let state = null;
  let frame = { frameless: false, maximized: false, figure: "", goto: "" };
  // Pages are shown once the language is known and the first run is over; a goto that comes sooner waits.
  let ready = false,
    waiting = "";
  // What is open: {page: "chat", chat: id}, {page: "finds"} or {page: "setup"}.
  let current = null;
  let setupMounted = false;
  const views = new Map();
  // 推荐记录 (finds.js), made when first opened.
  let finds = null;
  // The main chat as the list says it, the plain chats newest first, and the newest main message seen here.
  let main = { id: MAIN, name: "", last: 0 };
  let chats = [];
  let listed = false;
  let read = Number(stored("mibo-main-read")) || 0;
  const say = (key) => paI18n.t(`app.${key}`);

  // The figure: as just picked on this page, else as the window says, else the character's own, else the first.
  let picked = "";
  function figure() {
    if (FIGURES.includes(picked)) return picked;
    if (FIGURES.includes(frame.figure)) return frame.figure;
    return FIGURES.includes(state?.avatar) ? state.avatar : FIGURES[0];
  }

  // A figure is a painted sheet (<key>.webp) when there is one, else its drawing (<key>.svg).
  const sheets = new Map();
  function sheet(key) {
    if (!sheets.has(key))
      sheets.set(
        key,
        new Promise((resolve) => {
          const probe = new Image();
          probe.addEventListener("load", () => {
            // The grid whose shape is nearest the sheet's: four frames across, two by two, or one across.
            const shape = Math.log(probe.naturalWidth / probe.naturalHeight / (1 / FRAME_TALL));
            const cols = SHEETS.reduce((best, n) =>
              Math.abs(shape - Math.log(n / (4 / n))) < Math.abs(shape - Math.log(best / (4 / best))) ? n : best,
            );
            resolve({ src: probe.src, cols });
          });
          const drawing = () =>
            resolve({ src: `/assets/figures/${key}.svg${key === "mibo" && paint ? `?c=${paint}` : ""}`, cols: 1 });
          probe.addEventListener("error", drawing);
          // Mibo is only ever a drawing: no sheet is looked for.
          if (key === "mibo") drawing();
          else probe.src = `/assets/figures/${key}.webp`;
        }),
      );
    return sheets.get(key);
  }
  // Mibo is drawn in the colours chosen for it (panels/colors.py): asked for again when they change.
  let paint = "";
  async function repaint() {
    try {
      const value = await api("look/colors");
      const now = `${value.accent}${value.shell}`.replaceAll("#", "");
      const changed = paint && now !== paint;
      paint = now;
      if (!changed) return;
      sheets.delete("mibo");
      for (const box of document.querySelectorAll('[data-figure="mibo"]')) draw(box);
    } catch {
      // Not let in yet: it stays as it was drawn.
    }
  }
  // A character with a picture of its own (PERSONA_AVATAR "own") has it for a face; the whole figure stays.
  const owned = new Map();
  function wanted(box) {
    // A box drawn for one named figure (a choice on the first run) keeps it.
    if (box.dataset.fixed) return box.dataset.fixed;
    const own = state?.avatar === "own" && state.ownAvatar;
    return own && !box.classList.contains("pa-figure") ? `own:${state.ownAvatar}` : figure();
  }
  function ownSheet(key) {
    if (!owned.has(key))
      owned.set(
        key,
        picture("/api/avatar").then(
          (src) => ({ src, cols: 1, own: true }),
          () => sheet(figure()),
        ),
      );
    return owned.get(key);
  }
  // The figure's first frame in `box`: whole, or cropped to the head in a square.
  async function draw(box) {
    const key = wanted(box);
    box.dataset.figure = key;
    const { src, cols, own } = key.startsWith("own:") ? await ownSheet(key) : await sheet(key);
    if (box.dataset.figure !== key) return;
    const crop = own ? null : box.classList.contains("pa-face") ? HEAD : box.classList.contains("pa-bust") ? BUST : null;
    const width = crop ? 100 / crop.size : 100;
    let img = box.querySelector("img");
    if (!img) {
      img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      img.addEventListener("load", () => img.classList.add("ready"));
      box.append(img);
    }
    img.style.width = `${cols * width}%`;
    img.style.height = own ? "100%" : "";
    img.style.objectFit = own ? "cover" : "";
    img.style.left = crop ? `${-crop.left * width}%` : "0";
    img.style.top = crop ? `${-crop.top * width * FRAME_TALL}%` : "0";
    if (img.getAttribute("src") !== src) {
      img.classList.remove("ready");
      img.src = src;
    }
  }
  function figureNode(kind, className = "", fixed = "") {
    const box = document.createElement("span");
    box.className = `${kind} ${className}`.trim();
    if (fixed) box.dataset.fixed = fixed;
    box.setAttribute("aria-hidden", "true");
    draw(box);
    return box;
  }
  // The window's figure changed: every face on the page follows, and the page takes its colour.
  function redraw() {
    document.documentElement.dataset.figure = figure();
    for (const box of document.querySelectorAll(".pa-face, .pa-figure, .pa-bust"))
      if (box.dataset.figure !== wanted(box)) draw(box);
  }

  const ctx = {
    api,
    confirm: confirmBox,
    // A new chat, or with `private` a 无痕对话, opened in place of the one on screen.
    newChat: (isPrivate) => newChat(isPrivate),
    picture,
    attachedText,
    // In its own window: told when a chat changes ("pa-chats"), so the chat asks only now and then.
    told: Boolean(frameKey),
    get lang() {
      return paI18n.lang;
    },
    get figure() {
      return figure();
    },
    get name() {
      return state?.name || "";
    },
    // The character's head in a circle, and its whole figure; sized by the caller's class.
    face: (className) => figureNode("pa-face", className),
    body: (className) => figureNode("pa-figure", className),
    bust: (className) => figureNode("pa-bust", className),
    // One named figure, whatever the character is now.
    bodyOf: (key, className) => figureNode("pa-figure", className, key),
    figures: FIGURES,
    setFigure(key) {
      picked = key;
      redraw();
    },
    // The first run asks the language before anything else; the page follows at once.
    async setLang(code) {
      await paI18n.use(code);
      renderText();
      renderFrame();
    },
    // Settings saved another language for the screens: every open window starts again in it, this one back
    // where it was.
    relang() {
      paI18n.told();
      sessionStorage.setItem("mibo-goto", "setup/language");
      location.reload();
    },
    get state() {
      return state;
    },
    // Settings saved something the sidebar shows (the character's name or picture): read it again now.
    async changed() {
      try {
        state = await api("state");
      } catch {
        return;
      }
      redraw();
      await loadChats();
    },
    // A place in the app: "chat", "chat/<id>", "feed/<id>", "setup/model" and so on.
    go(page) {
      if (!ready) waiting = page;
      else follow(page);
    },
    openSettings() {
      ctx.go("setup");
    },
  };

  // ---- Pages
  // What a chat view tells the shell: what it has shown, a new title, a chat gone elsewhere.
  const hooks = {
    seen(id, seq) {
      if (id !== MAIN || seq <= read) return;
      read = seq;
      store("mibo-main-read", String(read));
      renderSide();
    },
    changed(id, title) {
      const chat = chats.find((one) => one.id === id);
      if (chat && title && !chat.title) {
        chat.title = title;
        renderSide();
      }
      loadChats();
    },
    gone(id) {
      forget(id);
      loadChats();
    },
    // What a reply on its way is doing ("正在搜索：…"), in the line under its name; "" when it is done.
    doing(line) {
      if (line === doingLine) return;
      doingLine = line;
      renderSide();
    },
    // The main chat's last words from the character, for the card when what it does is not known.
    said(id, text) {
      if (id !== MAIN) return;
      lastLine = String(text).replace(/\s+/g, " ").trim();
      if (!now) renderSide();
    },
  };
  // A chat that is no more: its view goes, and if it was open the main chat takes its place.
  function forget(id) {
    const view = views.get(id);
    if (!view) return;
    view.drop();
    views.delete(id);
    if (current?.chat === id) openChat(MAIN);
  }

  function openChat(id, then) {
    // A plain chat the list no longer has (deleted in another window) opens the main chat instead.
    if (id !== MAIN && listed && !chats.some((chat) => chat.id === id)) id = MAIN;
    // Back from Settings: what was connected there shows on the sidebar.
    if (current?.page === "setup")
      api("state")
        .then((value) => {
          state = value;
          renderSide();
        })
        .catch(() => {});
    current = { page: "chat", chat: id };
    $("setup-view").hidden = true;
    $("chat-view").hidden = false;
    const view = viewOf(id);
    for (const [key, other] of views) other.node.hidden = key !== id;
    if (finds) finds.node.hidden = true;
    renderSide();
    closeDrawer();
    const shown = view.show();
    then?.(view, shown);
  }
  // A chat's view, made out of sight the first time it is needed.
  function viewOf(id) {
    if (!views.has(id)) {
      const view = window.paChat.create(id, ctx, hooks);
      view.node.hidden = true;
      views.set(id, view);
      $("chat-view").append(view.node);
    }
    return views.get(id);
  }

  // 推荐记录 in the chats' place: its finds drawn by the main chat, a reply to one written there.
  function openFinds() {
    current = { page: "finds" };
    $("setup-view").hidden = true;
    $("chat-view").hidden = false;
    finds ??= window.paFinds.create(ctx, {
      card: (card) => viewOf(MAIN).findCard(card),
      reply: () => openChat(MAIN, async (view, shown) => {
        await shown;
        view.focus();
      }),
    });
    if (!finds.node.isConnected) $("chat-view").append(finds.node);
    for (const view of views.values()) view.node.hidden = true;
    finds.node.hidden = false;
    renderSide();
    closeDrawer();
    finds.show();
  }

  // The browser's 问角色 (browse.js): the chat on screen, else the main one, with `text` in its box.
  window.paAsk = (text) =>
    openChat(current?.page === "chat" ? current.chat : MAIN, async (view, shown) => {
      await shown;
      const input = view.node.querySelector(".chat-input");
      if (!input) return;
      input.value = input.value ? `${input.value}\n${text}` : text;
      input.dispatchEvent(new Event("input"));
      input.focus();
    });

  function openSetup(part) {
    current = { page: "setup" };
    $("chat-view").hidden = true;
    $("setup-view").hidden = false;
    renderSide();
    closeDrawer();
    if (!setupMounted) {
      setupMounted = true;
      window.paSetup.mount($("setup-view"), ctx);
    }
    window.paSetup.show(part);
  }

  function follow(goto) {
    const [name, ...rest] = goto.split("/");
    const part = rest.join("/");
    if (name === "settings" || name === "setup") return openSetup(part || undefined);
    if (name === "places" || name === "connect") return openSetup("connect");
    if (name === "chat" && part) return openChat(part);
    if (name === "finds") return openFinds();
    if (name === "feed" && part) return openChat(MAIN, (view, shown) => view.bring(part, shown));
    openChat(MAIN);
  }

  // ---- The sidebar
  const titleOf = (chat) => chat.title || say("newChat");
  const mainName = () => main.name || state?.name || say("someone");
  // What the character is doing now, as the pet reads it; its last words when that is not known.
  let now = null,
    lastLine = "";

  async function loadNow() {
    try {
      const value = await api("home");
      now = value?.now && typeof value.now === "object" ? value.now : null;
    } catch {
      now = null;
    }
    renderSide();
  }
  let nowTimer = null;
  async function pollNow() {
    clearTimeout(nowTimer);
    if (document.hidden) return;
    await loadNow();
    clearTimeout(nowTimer);
    if (!document.hidden) nowTimer = setTimeout(pollNow, NOW_MS);
  }
  let doingLine = "";
  function nowWords() {
    if (doingLine) return doingLine;
    if (!now) return lastLine;
    const words = paI18n.words("app").now;
    const where = String(now.where || "").trim();
    if (now.state === "out") return where ? words.out.replace("{where}", where) : words.outSomewhere;
    if (now.state === "answering") return words.answering;
    if (now.state === "asleep") {
      const until = Number(now.until) ? new Date(Number(now.until) * 1000) : null;
      const time = until?.toLocaleTimeString(paI18n.locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
      return time ? words.asleepUntil.replace("{time}", time) : words.asleep;
    }
    if (now.state === "back") return Number(now.count) ? paI18n.t("app.now.back", { count: Number(now.count) }) : words.home;
    return words.home;
  }
  // The dot on its face: about (green), asleep (grey), or none while it is not known.
  const liveState = () => (!now ? "off" : now.state === "asleep" ? "asleep" : "on");

  async function loadChats() {
    try {
      const value = await api("chats");
      main = value.main;
      chats = value.chats;
      listed = true;
    } catch {
      // The list stays as it was; the next look tries again.
      return;
    }
    for (const id of [...views.keys()]) if (id !== MAIN && !chats.some((chat) => chat.id === id)) forget(id);
    renderSide();
  }

  let chatsTimer = null;
  async function pollChats() {
    clearTimeout(chatsTimer);
    if (document.hidden) return;
    await loadChats();
    clearTimeout(chatsTimer);
    if (!document.hidden) chatsTimer = setTimeout(pollChats, frameKey ? TOLD_MS : CHATS_MS);
  }
  // window.py: a chat changed. The list looks again, and so does the chat on screen (chat.js).
  window.paChats = () => {
    if (!ready) return;
    pollChats();
    document.dispatchEvent(new Event("pa-chats"));
  };

  // The plain chats under 今天 / 前 7 天 / 更早, by when each was last written in.
  const GROUPS = ["today", "week", "earlier"];
  function groupOf(chat) {
    const at = new Date(chat.at || 0);
    if (Number.isNaN(at.getTime())) return "earlier";
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (at >= today) return "today";
    return at >= today.getTime() - 7 * DAY_MS ? "week" : "earlier";
  }
  // Rows are kept by id, so one being renamed or with its menu open is not rebuilt under the pointer.
  const rows = new Map();
  const groups = new Map();
  function groupNode(key) {
    if (!groups.has(key)) {
      const box = document.createElement("section");
      box.className = "side-group";
      const label = document.createElement("h2");
      label.className = "side-group-label";
      label.id = `side-group-${key}`;
      const list = document.createElement("ul");
      list.setAttribute("aria-labelledby", label.id);
      box.append(label, list);
      groups.set(key, box);
    }
    return groups.get(key);
  }

  function renderSide() {
    const open = current?.page === "chat" ? current.chat : "";
    const mainButton = $("side-main");
    $("side-main-name").textContent = mainName();
    const doing = nowWords();
    $("side-main-sub").textContent = doing;
    $("side-live").dataset.state = liveState();
    mainButton.classList.toggle("active", open === MAIN);
    if (open === MAIN) mainButton.setAttribute("aria-current", "page");
    else mainButton.removeAttribute("aria-current");
    // New since last seen here, unless it is on screen right now.
    const fresh = open === MAIN && !document.hidden ? 0 : Math.max(0, main.last - read);
    const count = $("side-count");
    count.hidden = !fresh;
    count.textContent = fresh > 99 ? "99+" : String(fresh);
    mainButton.setAttribute("aria-label", [mainName(), doing, fresh ? say("unread") : ""].filter(Boolean).join(" · "));
    mainButton.dataset.tip = mainName();
    $("side-settings").classList.toggle("active", current?.page === "setup");
    $("side-finds").classList.toggle("active", current?.page === "finds");
    if (current?.page === "finds") $("side-finds").setAttribute("aria-current", "page");
    else $("side-finds").removeAttribute("aria-current");

    // A chat not written in yet stays out of the list: 新对话 itself stands for it while it is open.
    const blankOpen = chats.some((chat) => chat.id === open && !chat.title);
    $("side-new").classList.toggle("active", blankOpen);
    if (blankOpen) $("side-new").setAttribute("aria-current", "page");
    else $("side-new").removeAttribute("aria-current");

    const list = $("side-chats");
    const placed = new Set();
    for (const key of GROUPS) {
      const members = chats.filter((chat) => chat.title && groupOf(chat) === key);
      const box = groupNode(key);
      box.querySelector(".side-group-label").textContent = say(key);
      const ul = box.querySelector("ul");
      members.forEach((chat, index) => {
        const row = rows.get(chat.id) || chatRow(chat.id);
        rows.set(chat.id, row);
        placed.add(chat.id);
        paintRow(row, chat, open === chat.id);
        if (ul.children[index] !== row) ul.insertBefore(row, ul.children[index] || null);
      });
      if (members.length && box.parentElement !== list) list.append(box);
      if (!members.length) box.remove();
    }
    for (const [id, row] of rows)
      if (!placed.has(id)) {
        row.remove();
        rows.delete(id);
      }
    // Today first, then the week, then earlier.
    for (const key of GROUPS) if (groups.get(key)?.parentElement === list) list.append(groups.get(key));
    renderTitle();
    for (const [id, view] of views) if (id !== MAIN) view.title(chats.find((chat) => chat.id === id)?.title || "");
  }

  function chatRow(id) {
    const row = document.createElement("li");
    row.className = "side-chat";
    row.dataset.id = id;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "side-chat-open";
    open.append(document.createElement("span"));
    open.firstChild.className = "side-chat-title";
    open.addEventListener("click", () => openChat(id));
    const more = document.createElement("button");
    more.type = "button";
    more.className = "side-chat-more";
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.innerHTML =
      '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/></svg>';
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      if (menu.id === id) closeMenu();
      else openMenu(id, more);
    });
    row.append(open, more);
    return row;
  }

  function paintRow(row, chat, on) {
    if (row.classList.contains("renaming")) return;
    const title = titleOf(chat);
    row.querySelector(".side-chat-title").textContent = title;
    row.querySelector(".side-chat-open").title = title;
    const open = row.querySelector(".side-chat-open");
    row.classList.toggle("active", on);
    row.classList.toggle("untitled", !chat.title);
    // A 无痕对话 wears its mark in the list too (app.css).
    row.classList.toggle("private", Boolean(chat.private));
    if (chat.private) open.title = `${title} · ${say("privateChat")}`;
    if (on) open.setAttribute("aria-current", "page");
    else open.removeAttribute("aria-current");
    row.querySelector(".side-chat-more").setAttribute("aria-label", say("more").replace("{title}", title));
  }

  // The bar names a plain chat on the column's left edge, renamed by a click; the main chat
  // is named by the sidebar's card, Settings by its own page, so the bar stays empty for them.
  const PENCIL =
    '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><path d="M21.17 6.81a1 1 0 0 0-3.98-3.98L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z"/></svg>';
  function renderTitle() {
    const bar = $("page-title");
    if (bar.querySelector("input")) return;
    const chat = current?.page === "chat" && current.chat !== MAIN ? chats.find((one) => one.id === current.chat) : null;
    if (!chat) return bar.replaceChildren();
    let name = bar.querySelector(".titlebar-title");
    if (!name) {
      name = document.createElement("button");
      name.type = "button";
      name.className = "titlebar-title";
      name.innerHTML = `<span></span>${PENCIL}`;
      name.addEventListener("click", () => renameChat(current.chat, "bar"));
      bar.replaceChildren(name);
    }
    name.querySelector("span").textContent = titleOf(chat);
    name.title = say("renameChat");
    name.setAttribute("aria-label", `${titleOf(chat)} · ${say("renameChat")}`);
  }

  // ---- A chat's menu: rename, delete
  const menu = { id: "", from: null };
  function openMenu(id, from) {
    closeMenu();
    menu.id = id;
    menu.from = from;
    from.setAttribute("aria-expanded", "true");
    from.closest(".side-chat").classList.add("menu-open");
    $("side-delete").querySelector("span").textContent = say("delete");
    const box = $("side-menu");
    box.hidden = false;
    // Under the button, its right edge on the button's, so it stays over the sidebar.
    const at = from.getBoundingClientRect();
    const left = at.right - box.offsetWidth;
    const below = at.bottom + 4 + box.offsetHeight < innerHeight - 8;
    box.style.left = `${Math.max(8, left)}px`;
    box.style.top = `${below ? at.bottom + 4 : at.top - 4 - box.offsetHeight}px`;
    $("side-rename").focus();
  }
  function closeMenu(focusBack = false) {
    if (!menu.id) return;
    $("side-menu").hidden = true;
    menu.from.setAttribute("aria-expanded", "false");
    menu.from.closest(".side-chat")?.classList.remove("menu-open");
    if (focusBack) menu.from.focus();
    menu.id = "";
    menu.from = null;
  }

  // One question in the middle of the window before something that cannot be undone: true on its yes. With
  // `also`, a tick for a further step, unticked: its yes is { also } with the tick's state.
  function confirmBox({ title, text = "", ok, also = "" }) {
    const box = document.createElement("dialog");
    box.className = "confirm";
    const words = document.createElement("h2");
    words.className = "confirm-title";
    words.textContent = title;
    const note = document.createElement("p");
    note.className = "confirm-text";
    note.textContent = text;
    note.hidden = !text;
    const actions = document.createElement("div");
    actions.className = "confirm-actions";
    for (const [cls, label, answer] of [["confirm-btn", say("cancel"), ""], ["confirm-btn confirm-ok", ok, "ok"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = cls;
      button.textContent = label;
      button.addEventListener("click", () => box.close(answer));
      actions.append(button);
    }
    const tick = document.createElement("label");
    tick.className = "confirm-also";
    const check = document.createElement("input");
    check.type = "checkbox";
    tick.append(check, also);
    tick.hidden = !also;
    box.append(words, note, tick, actions);
    // A click on the dimmed page around it is a no.
    box.addEventListener("click", (event) => event.target === box && box.close(""));
    return new Promise((resolve) => {
      box.addEventListener("close", () => {
        box.remove();
        resolve(box.returnValue === "ok" && (also ? { also: check.checked } : true));
      });
      document.body.append(box);
      box.showModal();
    });
  }

  // Delete asks once, in the middle of the window, then goes.
  async function deleteChat() {
    const id = menu.id;
    closeMenu();
    if (!(await confirmBox({ title: say("deleteAsk"), text: say("deleteNote"), ok: say("delete") }))) return;
    try {
      await api("chats/delete", { id });
    } catch {
      // Not deleted: the list shows it as it is.
    }
    await loadChats();
  }

  // Rename in place, in the sidebar's row or in the bar: Enter or leaving the field keeps it,
  // Escape leaves it as it was.
  function renameChat(id = menu.id, where = "side") {
    closeMenu();
    const row = rows.get(id);
    const chat = chats.find((one) => one.id === id);
    if (!chat || !row) return;
    const field = document.createElement("input");
    field.className = where === "bar" ? "titlebar-input" : "side-chat-input";
    field.maxLength = TITLE_MAX;
    field.value = chat.title;
    field.placeholder = say("newChat");
    field.setAttribute("aria-label", say("renameLabel"));
    if (where === "bar") $("page-title").replaceChildren(field);
    else {
      row.classList.add("renaming");
      row.prepend(field);
    }
    field.focus();
    field.select();
    let done = false;
    const finish = async (keep) => {
      if (done) return;
      done = true;
      const title = field.value.trim();
      field.remove();
      row.classList.remove("renaming");
      if (keep && title && title !== chat.title) {
        // Shown at once; the list read after says what was kept.
        chat.title = title;
        renderSide();
        try {
          await api("chats/rename", { id, title });
        } catch {
          // Refused: the list read below puts the old name back.
        }
        await loadChats();
      } else renderSide();
      if (document.activeElement === document.body)
        (where === "bar" ? $("page-title").querySelector("button") : row.querySelector(".side-chat-open"))?.focus();
    };
    field.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }
    });
    field.addEventListener("blur", () => finish(true));
  }

  $("side-rename").addEventListener("click", () => renameChat());
  $("side-delete").addEventListener("click", deleteChat);
  $("side-menu").addEventListener("keydown", (event) => {
    const items = [$("side-rename"), $("side-delete")];
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (!step) return;
    event.preventDefault();
    items[(items.indexOf(document.activeElement) + step + items.length) % items.length].focus();
  });
  document.addEventListener("click", (event) => {
    if (menu.id && !event.target.closest("#side-menu")) closeMenu();
  });
  $("side-chats").addEventListener("scroll", () => closeMenu());
  window.addEventListener("resize", () => closeMenu());

  // A new chat, or a 无痕对话; one of that kind not written in yet is opened again rather than another made.
  async function newChat(isPrivate = false) {
    const blank = chats.find((chat) => !chat.title && Boolean(chat.private) === isPrivate);
    if (blank) return openChat(blank.id);
    let made;
    try {
      made = await api("chats", isPrivate ? { private: true } : {});
    } catch {
      return;
    }
    chats = [made, ...chats.filter((chat) => chat.id !== made.id)];
    openChat(made.id);
    loadChats();
  }

  $("side-main").addEventListener("click", () => openChat(MAIN));
  $("side-new").addEventListener("click", () => newChat());
  $("side-finds").addEventListener("click", openFinds);
  $("side-settings").addEventListener("click", () => openSetup());

  // ---- The rail: below 1000px, or folded by hand, the sidebar is a strip of icons;
  // opening it from there lays the whole sidebar over the page.
  let collapsed = stored("mibo-side") === "collapsed";
  // A page in front folds the whole sidebar to its rail while it is there (browse.js), and the sidebar comes back
  // when the page leaves: by the switch in Settings (setup/device.js; on unless turned off there), and with it off
  // only on a screen with no room for the sidebar beside the page (`tight`), unless the person folded or unfolded
  // it themselves once. Not kept, and the fold button unfolds it.
  let paged = false;
  const railed = () => NARROW.matches || collapsed || paged;
  window.paSideFold = (on, tight) => {
    paged = on && (stored("mibo-side-page") !== "keep" || (tight && !stored("mibo-side")));
    renderSideState();
  };
  function renderSideState() {
    const rail = railed();
    document.body.classList.toggle("side-rail", rail);
    if (!rail) document.body.classList.remove("side-open");
    const shown = !rail || document.body.classList.contains("side-open");
    const toggle = $("side-toggle");
    toggle.setAttribute("aria-expanded", String(shown));
    toggle.setAttribute("aria-label", say(shown ? "sideClose" : "sideOpen"));
    toggle.title = say(shown ? "sideClose" : "sideOpen");
    toggle.dataset.tip = say(shown ? "sideClose" : "sideOpen");
  }
  function closeDrawer(focusBack = false) {
    if (!document.body.classList.contains("side-open")) return;
    document.body.classList.remove("side-open");
    closeMenu();
    renderSideState();
    if (focusBack) $("side-toggle").focus();
  }
  function openDrawer() {
    document.body.classList.add("side-open");
    renderSideState();
    $("side-main").focus();
  }
  $("side-toggle").addEventListener("click", () => {
    if (document.body.classList.contains("side-open")) {
      // Folded by hand on a wide window: opening from the rail unfolds it for good.
      if (!NARROW.matches) {
        collapsed = false;
        store("mibo-side", "open");
      }
      closeDrawer();
    } else if (railed() && NARROW.matches) openDrawer();
    else {
      collapsed = !railed();
      paged = false;
      store("mibo-side", collapsed ? "collapsed" : "open");
      renderSideState();
    }
  });
  $("side-scrim").addEventListener("click", () => closeDrawer());
  NARROW.addEventListener("change", renderSideState);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (menu.id) closeMenu(true);
      else closeDrawer(true);
      return;
    }
    // Ctrl N: a new chat, as chat apps have it.
    if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && event.key.toLowerCase() === "n") {
      if (!ready || document.body.classList.contains("onboarding")) return;
      event.preventDefault();
      newChat();
      return;
    }
    // A key typed while nothing takes text goes into the open chat's box, as chat apps have it.
    const typed = event.key.trim().length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && !event.isComposing;
    const field = event.target.closest?.("input, textarea, select, [contenteditable]");
    if (typed && !field && !menu.id && ready && current?.page === "chat" && !document.body.classList.contains("side-open"))
      views.get(current.chat)?.focus();
  });

  function renderText() {
    for (const node of document.querySelectorAll("[data-t]")) node.textContent = say(node.dataset.t);
    $("side").setAttribute("aria-label", say("nav"));
    for (const [id, key] of [
      ["side-new", "newChat"],
      ["side-finds", "finds"],
      ["side-settings", "settings"],
    ])
      $(id).dataset.tip = say(key);
    for (const [id, key] of [
      ["window-minimize", "minimize"],
      ["window-close", "close"],
    ]) {
      $(id).setAttribute("aria-label", say(key));
      $(id).title = say(key);
    }
    renderSideState();
    renderSide();
  }

  // ---- The window's bar: drawn by the page while the window has none of its own.
  function renderFrame() {
    document.body.classList.toggle("frameless", frame.frameless);
    document.body.classList.toggle("maximized", frame.frameless && frame.maximized);
    $("window-controls").hidden = !frame.frameless;
    const key = frame.maximized ? "restore" : "maximize";
    $("window-maximize").setAttribute("aria-label", say(key));
    $("window-maximize").title = say(key);
  }

  // Asked when the page shows or takes the focus, and when window.py says the window changed
  // (maximized or restored, the pet sent it somewhere, another figure): paFrame.
  async function loadFrame() {
    const was = figure();
    try {
      const value = await api("window");
      // The window's figure moved (the pet's menu, a choice in Settings): it wins over one picked here before.
      if (String(value.figure || "") !== frame.figure) picked = "";
      frame = {
        frameless: Boolean(value.frameless),
        maximized: Boolean(value.maximized),
        figure: String(value.figure || ""),
        goto: String(value.goto || ""),
      };
    } catch {
      // In a browser tab, or before the page is let in: the window keeps its own bar.
    }
    renderFrame();
    if (figure() !== was) redraw();
    if (figure() === "mibo") repaint();
    if (frame.goto && ready) follow(frame.goto);
    else if (frame.goto) waiting = frame.goto;
  }
  window.paFrame = loadFrame;
  async function windowDo(action) {
    try {
      const value = await api("window", { action });
      frame.frameless = Boolean(value.frameless);
      frame.maximized = Boolean(value.maximized);
      renderFrame();
    } catch {
      // The window is gone or will not do it: its bar stays as it was.
    }
  }
  $("window-minimize").addEventListener("click", () => windowDo("minimize"));
  $("window-maximize").addEventListener("click", () => windowDo(frame.maximized ? "restore" : "maximize"));
  $("window-close").addEventListener("click", () => windowDo("close"));
  // Double-clicking a title bar maximizes the window, as Windows' own does.
  $("titlebar").addEventListener("dblclick", (event) => {
    if (frame.frameless && !event.target.closest("button")) windowDo(frame.maximized ? "restore" : "maximize");
  });
  // The pet opens the window on a thing by showing it or bringing it forward; where to go is read then,
  // whether or not the window draws its own bar.
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearTimeout(chatsTimer);
      clearTimeout(nowTimer);
    } else {
      loadFrame();
      if (ready) {
        pollChats();
        pollNow();
      }
    }
    renderSide();
  });
  // Coming to the front, the window hands focus to the first control it finds: a ring nobody asked for.
  // A button that gets focus in that moment, with no key pressed, lets it go.
  let arrived = performance.now();
  let tabbed = false;
  document.addEventListener("keydown", (event) => (tabbed = event.key === "Tab"), true);
  document.addEventListener("pointerdown", () => (tabbed = false), true);
  document.addEventListener("visibilitychange", () => (arrived = performance.now()));
  document.addEventListener("focusin", (event) => {
    if (!tabbed && performance.now() - arrived < 400 && event.target.matches("button, a")) event.target.blur();
  });
  window.addEventListener("focus", () => {
    arrived = performance.now();
    tabbed = false;
    loadFrame();
  });
  // window.py: a game or something full screen is in front (quiet), or went. Meanwhile the page
  // counts as hidden, here and in chat.js, so it asks nothing, as in the tray.
  let quietNow = false;
  const seenHidden = Object.getOwnPropertyDescriptor(Document.prototype, "hidden").get;
  Object.defineProperty(document, "hidden", { get: () => quietNow || seenHidden.call(document) });
  window.paQuiet = (on) => {
    if (on === quietNow) return;
    quietNow = on;
    document.dispatchEvent(new Event("visibilitychange"));
  };

  // The app proper: the sidebar and its pages, opening on the main chat.
  function enter() {
    document.body.classList.remove("onboarding");
    $("onboard-view").hidden = true;
    ready = true;
    $("side-avatar").prepend(ctx.face("who-face"));
    const goto = waiting;
    waiting = "";
    follow(goto);
    pollChats();
    pollNow();
  }

  (async () => {
    // First, and not waited for: a frameless window needs its buttons whatever else loads.
    loadFrame();
    try {
      state = await api("state");
    } catch {
      // The pages say for themselves what they cannot reach.
    }
    // Back where a change of language left it.
    waiting = sessionStorage.getItem("mibo-goto") || waiting;
    sessionStorage.removeItem("mibo-goto");
    // The character is known now: the page takes its colour and its faces.
    redraw();
    renderText();
    renderFrame();
    let first = false;
    try {
      first = Boolean(window.paOnboard) && (await window.paOnboard.needed(ctx));
    } catch {
      // Whether it is the first run could not be told: the app opens as usual.
    }
    document.body.classList.remove("starting");
    if (!first) return enter();
    document.body.classList.add("onboarding");
    $("onboard-view").hidden = false;
    window.paOnboard.mount($("onboard-view"), ctx, async () => {
      // The first run may have named the character or picked its figure.
      try {
        state = await api("state");
      } catch {
        // The pages ask again for what they need.
      }
      redraw();
      enter();
    });
  })();
})();
