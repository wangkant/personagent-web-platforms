"use strict";
// The chats with the character. The main chat is also where it brings what it found: its
// sentence about the thing, the thing's card under it. A plain chat is only talk. The shell
// makes one view per chat. The column reads quietly: the character's words without bubbles,
// its face, name and time once at the start of each run; the person's words in soft bubbles.
window.paChat = (() => {
  const MAIN = "main";
  // Failures that the settings page can fix get a way there.
  const SETTINGS_FIXES = new Set(["key_required", "chat_failed", "model_no_balance"]);
  // What the character sends on its own is looked for this often while the chat is on screen; in
  // the app's own window it is looked for when the window says a chat changed ("pa-chats"), and
  // only now and then besides.
  const POLL_MS = 3000;
  const TOLD_POLL_MS = 60000;
  // Past the service's own turn budget (three minutes) a reply is not coming: the wait ends.
  const TURN_MS = 200000;
  // The box grows with the text up to this height, then scrolls.
  const INPUT_MAX = 220;
  // Closer to the end than this, the chat counts as read to the end and stays there as it grows.
  const END_PX = 80;
  // A plain chat is named after the first message sent in it, cut to this many characters.
  const TITLE_CLIP = 40;
  // The character's messages further apart than this start a new run, with its name and time again.
  const RUN_GAP_MS = 10 * 60 * 1000;
  // What a message can carry (welcome.py checks it again): at most `most` things. A picture of at
  // most `picked` bytes, sent as picked when it is within `side` pixels and `picture` bytes, else
  // drawn smaller as WebP until it is; a text file of at most `file` bytes, by its extension.
  const ATTACH = { most: 4, picked: 20 * 1024 * 1024, picture: 1000000, side: 1600, file: 512 * 1024 };
  const PICTURE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  const TEXT_KINDS = new Set(
    ("txt text md markdown rst csv tsv json jsonl yaml yml toml ini cfg conf log xml html htm " +
      "css scss js mjs cjs ts tsx jsx vue py ipynb java kt kts scala go rs c h cc cpp hpp cs m mm " +
      "swift rb php pl lua r sql sh bash zsh ps1 bat cmd tex bib srt vtt diff patch").split(" "),
  );
  const SVG = "http://www.w3.org/2000/svg";
  // Lucide-style strokes.
  const I = {
    window: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18"/>',
    terminal: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7 10 3 2-3 2"/><path d="M13 15h4"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    stop: '<rect x="5" y="5" width="14" height="14" rx="2.5"/>',
    reply: '<path d="M9 17l-5-5 5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
    heart:
      '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7z"/>',
    open: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
    tv: '<rect x="2" y="7" width="20" height="15" rx="2"/><path d="m17 2-5 5-5-5"/>',
    video: '<rect x="2" y="4" width="20" height="16" rx="4"/><path d="m10 9 5 3-5 3z"/>',
    file: '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8M16 17H8M10 9H8"/>',
    branch: '<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    post: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    shrink: '<path d="m14 10 7-7"/><path d="M20 10h-6V4"/><path d="m3 21 7-7"/><path d="M4 14h6v6"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    skip: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
    less: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8"/>',
    raise: '<circle cx="12" cy="12" r="10"/><path d="M8 12h8M12 8v8"/>',
    seen: '<path d="M20 6 9 17l-5-5"/>',
    alert: '<circle cx="12" cy="12" r="10"/><path d="M12 8v4M12 16h.01"/>',
    talk: '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22z"/>',
    calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    compass: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    // A dashed bubble: the 无痕对话.
    ghost: '<path d="M10.1 2.18a10 10 0 0 1 3.8 0M13.9 21.82a10 10 0 0 1-3.8 0M17.6 3.72a10 10 0 0 1 2.7 2.7M2.18 13.9a10 10 0 0 1 0-3.8M20.28 17.6a10 10 0 0 1-2.7 2.7M21.82 10.1a10 10 0 0 1 0 3.8M3.72 6.4a10 10 0 0 1 2.7-2.7"/><path d="m6.8 20.79-3.9.9a.5.5 0 0 1-.6-.6l.9-3.9"/>',
  };
  const YOUTUBE = ["www.youtube-nocookie.com", "www.youtube.com", "youtube.com", "youtube-nocookie.com"];

  // Seconds a player has run: from YouTube's own state changes once it sends them
  // (asked to with "listening"), else the time it has been on the page.
  function playClock(frame, origin) {
    let total = 0;
    let since = performance.now();
    let heard = false;
    const hear = (event) => {
      if (event.source !== frame.contentWindow) return;
      let said;
      try {
        said = JSON.parse(event.data);
      } catch {
        return;
      }
      const state = said?.event === "onStateChange" ? said.info : said?.info?.playerState;
      if (typeof state !== "number") return;
      const now = performance.now();
      // From its first word on, only the time it says it plays counts.
      if (!heard) {
        heard = true;
        since = null;
      }
      if (state === 1) since ??= now;
      else if (since !== null) {
        total += now - since;
        since = null;
      }
    };
    if (origin) {
      addEventListener("message", hear);
      // Asked until it answers, as YouTube's own API does: its player is ready a little after its page.
      frame.addEventListener("load", () => {
        let tries = 0;
        const ask = setInterval(() => {
          if (heard || ++tries > 40 || !frame.isConnected) return clearInterval(ask);
          frame.contentWindow.postMessage(JSON.stringify({ event: "listening", id: 1, channel: "widget" }), origin);
        }, 250);
      });
    }
    return {
      seconds: () => Math.min(86400, (total + (since === null ? 0 : performance.now() - since)) / 1000),
      stop: () => removeEventListener("message", hear),
    };
  }
  // The videos playing in place in any chat, each with its way to tell the feed how long it has run.
  // Told when it is folded away, scrolled out of view or the page is put away; the feed keeps the longest.
  const players = new Map();
  const tellAll = () => players.forEach((tell) => tell());
  const offView = new IntersectionObserver((entries) => {
    for (const entry of entries) if (!entry.isIntersecting) players.get(entry.target)?.();
  });
  document.addEventListener("visibilitychange", () => document.hidden && tellAll());
  addEventListener("pagehide", tellAll);
  const IDEA_ICONS = { main: [I.compass, I.calendar, I.talk], plain: [I.talk, I.calendar, I.help] };
  const PLAY = '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/>';

  function el(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function button(className, text, run) {
    const node = el("button", className, text);
    node.type = "button";
    node.addEventListener("click", run);
    return node;
  }
  function svg(paths, className = "i") {
    const node = document.createElementNS(SVG, "svg");
    node.setAttribute("viewBox", "0 0 24 24");
    node.setAttribute("aria-hidden", "true");
    node.setAttribute("class", className);
    node.innerHTML = paths;
    return node;
  }
  // A button holding only an icon, named for screen readers and on hover.
  function iconButton(className, paths, label, run) {
    const node = button(className, "", run);
    node.append(svg(paths));
    node.setAttribute("aria-label", label);
    node.title = label;
    return node;
  }
  // The head of a card that asks the person: an icon, what is asked, and one faint line under it.
  function cardHead(paths, title, meta = "") {
    const head = el("div", "op-card-head");
    const tile = el("span", "op-card-icon");
    tile.append(svg(paths));
    const words = el("div", "op-card-words");
    words.append(el("p", "op-card-title", title));
    if (meta) words.append(el("p", "op-card-meta", meta));
    head.append(tile, words);
    return head;
  }
  const motion = () => (matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth");
  // An ISO time, or epoch seconds (remarks keep those) or milliseconds.
  const when = (at) => {
    const time = typeof at === "number" ? (at < 1e12 ? at * 1000 : at) : at ? new Date(at).getTime() : NaN;
    return Number.isFinite(time) && time > 0 ? time : 0;
  };
  const dayKey = (time) => {
    const d = new Date(time);
    return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
  };
  const hostOf = (url) => {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  };
  const extOf = (name) => (/\.([^.]+)$/.exec(name)?.[1] || "").toLowerCase();
  function sizeOf(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    return bytes < 1048576 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
  }
  const base64Of = (blob) =>
    new Promise((done, fail) => {
      const reader = new FileReader();
      reader.onload = () => done(String(reader.result).split(",")[1] || "");
      reader.onerror = () => fail(reader.error);
      reader.readAsDataURL(blob);
    });
  // A picture as it is sent: as picked when small enough, else drawn smaller as WebP.
  async function sendable(file) {
    const bitmap = await createImageBitmap(file);
    try {
      let scale = Math.min(1, ATTACH.side / Math.max(bitmap.width, bitmap.height));
      if (scale === 1 && file.size <= ATTACH.picture) return file;
      for (;;) {
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        const blob = await new Promise((done) => canvas.toBlob(done, "image/webp", 0.85));
        if (blob.size <= ATTACH.picture) return blob;
        scale *= 0.75;
      }
    } finally {
      bitmap.close();
    }
  }
  // Whether a file's bytes read as text, as welcome.py reads them: UTF-8, UTF-16 with its mark, or GB18030.
  function readsAsText(buffer) {
    const bytes = new Uint8Array(buffer);
    if ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff)) return true;
    for (const label of ["utf-8", "gb18030"]) {
      try {
        return !new TextDecoder(label, { fatal: true }).decode(bytes).includes("\0");
      } catch {
        // Not this one: the next.
      }
    }
    return false;
  }

  // A video's length as a player shows it.
  function clock(seconds) {
    const s = Math.round(seconds);
    const two = (n) => String(n).padStart(2, "0");
    const h = Math.floor(s / 3600);
    return h ? `${h}:${two(Math.floor((s % 3600) / 60))}:${two(s % 60)}` : `${Math.floor(s / 60)}:${two(s % 60)}`;
  }
  // One quiet icon for where a thing is from; only the cover carries colour.
  function siteIcon(card) {
    const host = hostOf(card.url);
    if (/(^|\.)(github\.com|gitlab\.com|gitee\.com|codeberg\.org)$/.test(host)) return I.branch;
    if (card.kind === "video") return /bilibili|b23\.tv/.test(host) ? I.tv : I.video;
    if (card.kind === "article") return I.file;
    if (card.kind === "post") return I.post;
    return I.globe;
  }

  function create(id, ctx, hooks) {
    const main = id === MAIN;
    const words = () => paI18n.words("chat");
    const locale = () => paI18n.locale;
    // As the shell last read it, so a new chat can be typed in at once; read again on show.
    let state = ctx.state;
    let seen = 0,
      tag = "",
      sending = false,
      // The typing row of the reply being waited for.
      waiting = null,
      // Ends the wait for that reply (the stop button, or past TURN_MS), and the stop's own request.
      cut = null,
      stopping = null,
      // The wait's own limit, drawn out while the character works in the browser; the card it waits on.
      turnLimit = null,
      // The cards answered beside the page this chat has a line for.
      noted = new Set(),
      shellCard = null,
      deskCard = null,
      polling = false,
      pollTimer = null,
      loaded = false,
      atEnd = true,
      title = "",
      replying = null,
      // What rides on the next message: { key, kind, file, size, url, data } each, data once it is ready.
      attached = [],
      attachKey = 0,
      dragDepth = 0,
      // A 无痕对话: it reads no memory and leaves none (welcome.py PageChat.private).
      privateChat = false;
    // Where the thread was last drawn to: the day, and the character's open run of messages.
    let lastDay = "",
      run = null;
    // Pictures behind the page's token, fetched once per view.
    const blobs = new Map();
    const parts = {};

    const displayName = () => state?.name || ctx.name || words().someone;
    const say = (key, name = displayName()) => String(words()[key] ?? words().generic).replaceAll("{name}", name);
    const settingsButton = (className = "pa-btn pa-btn-secondary pa-btn-sm") =>
      button(className, say("settings"), () => ctx.go("setup/model"));
    const visible = () => root.isConnected && !root.closest("[hidden]") && !document.hidden;
    // A reply under way keeps asking out of sight too: a card it waits on stays (cardwait.py) and is there when
    // they come back, and the wait for the reply goes on.
    const asking = () => root.isConnected && (sending || visible());
    const history = (after) =>
      ctx.api(`chat/history?chat=${encodeURIComponent(id)}${after === undefined ? "" : `&after=${after}`}`);
    const blob = (path) => {
      if (!blobs.has(path)) blobs.set(path, ctx.picture(path));
      return blobs.get(path);
    };
    // What the link cards and the steps above a reply take from the chat (linkcard.js, activity.js).
    const links = { api: ctx.api, picture: blob, link: (url, className) => linkNode(url, className),
      findCard: (card) => findCard(card), grew: () => keepEnd(), goOn: (text) => !sending && send(text), go: ctx.go,
      confirm: ctx.confirm };
    const linkCards = (text, skip = "") => window.paLinks.cards(window.paLinks.urls(text, [skip]), links);

    // ---- The page: the thread, or the empty and not-set-up states, and the composer
    const root = el("div", `chat ${main ? "chat-main" : "chat-plain"}`);
    parts.scroll = el("div", "chat-scroll");
    const column = el("div", "chat-col chat-thread");
    parts.messages = el("div", "chat-messages");
    parts.messages.setAttribute("role", "log");
    column.append(parts.messages);
    parts.scroll.append(column);
    parts.scroll.addEventListener("scroll", () => {
      const box = parts.scroll;
      atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < END_PX;
      closeMenus();
    });

    // No messages yet: the character's bust fading out, a greeting, the composer, three ideas.
    parts.empty = el("div", "chat-empty");
    const emptyCol = el("div", "chat-col");
    const hello = el("div", "chat-hello");
    parts.greet = el("h1");
    parts.emptyText = el("p");
    hello.append(ctx.bust("chat-bust"), parts.greet, parts.emptyText);
    parts.ideas = el("div", "chat-ideas");
    parts.emptySlot = el("div", "chat-empty-slot");
    // A new plain chat can be made a 无痕对话 before its first message; its mark heads the chat.
    parts.privateToggle = button("chat-private-toggle", "", () => setPrivate(!privateChat));
    parts.mark = el("div", "chat-private");
    parts.mark.hidden = true;
    emptyCol.append(hello, parts.emptySlot, parts.ideas, parts.privateToggle);
    parts.empty.append(emptyCol);
    parts.empty.hidden = true;

    // No model yet: said in the middle of the page, with the way to set it up.
    parts.off = el("div", "chat-off");
    parts.off.hidden = true;

    // The composer: two rows at rest, growing with the text; + and send on a row below.
    parts.compose = el("div", "chat-compose");
    parts.banner = el("div", "chat-banner");
    parts.banner.setAttribute("role", "status");
    parts.banner.hidden = true;
    // A file that could not be added, and why: until the next thing is added or it is closed.
    parts.refused = el("div", "chat-refused");
    parts.refused.setAttribute("role", "alert");
    parts.refused.hidden = true;
    parts.form = el("form", "chat-composer");
    parts.quote = el("div", "chat-composer-quote");
    parts.quote.hidden = true;
    // What is attached, above the text.
    parts.tray = el("div", "chat-tray");
    parts.tray.hidden = true;
    parts.input = el("textarea", "chat-input");
    parts.input.rows = 2;
    const tools = el("div", "chat-tools");
    parts.picker = el("input");
    parts.picker.type = "file";
    parts.picker.multiple = true;
    parts.picker.hidden = true;
    parts.picker.accept = [...PICTURE_TYPES, ...[...TEXT_KINDS].map((kind) => `.${kind}`)].join(",");
    parts.picker.addEventListener("change", () => {
      attach([...parts.picker.files]);
      parts.picker.value = "";
    });
    // "+": a menu of what can go with a message and what to ask for (plus.js).
    let held = "";
    const plus = window.paPlus.create({
      ctx, main, input: parts.input,
      name: () => displayName(),
      configured: () => Boolean(state?.configured),
      pick: () => parts.picker.click(),
      attach, refuse, flash, attachItem,
      refresh: () => poll(),
      private: () => privateChat,
      setPrivate,
      quote(text) {
        const block = `${text.trim().split(/\r?\n/).map((line) => `> ${line}`).join("\n")}\n\n`;
        const at = parts.input.selectionStart ?? parts.input.value.length;
        parts.input.setRangeText(block, at, parts.input.selectionEnd ?? at, "end");
        grow();
        parts.input.focus();
      },
      placeholder(text) {
        held = text;
        parts.input.placeholder = text || say(parts.empty.hidden ? "placeholder" : "placeholderEmpty");
      },
    });
    parts.plus = plus.button;
    // Beside it, the model's short name, its thinking level and the usage (modelbar.js).
    const bar = window.paModelBar.create({
      ctx, flash,
      pictures: () => Boolean(parts.messages.querySelector(".me-att .pic")),
      tools: () => Boolean(parts.messages.querySelector(".act, .me-choice")),
    });
    parts.send = el("button", "chat-send");
    parts.send.type = "submit";
    tools.append(parts.plus, bar.node, plus.chips, parts.picker, parts.send);
    parts.form.append(parts.quote, parts.tray, parts.input, tools);
    parts.hint = el("p", "chat-hint");
    parts.compose.append(parts.banner, parts.refused, parts.form, parts.hint);
    // A click on the box's padding still puts the caret in the text.
    parts.form.addEventListener("mousedown", (event) => {
      if (event.target === parts.form || event.target === tools) {
        event.preventDefault();
        parts.input.focus();
      }
    });
    parts.dock = el("div", "chat-dock");
    const dockCol = el("div", "chat-col");
    parts.dock.append(dockCol);
    dockCol.append(parts.compose);
    // Files dragged over the chat: a frame saying they can be let go here.
    parts.dropZone = el("div", "chat-drop");
    root.append(parts.scroll, parts.empty, parts.off, parts.dock, parts.dropZone);

    const carriesFiles = (event) => Boolean(state?.configured) && [...(event.dataTransfer?.types || [])].includes("Files");
    root.addEventListener("dragenter", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth += 1;
      root.classList.add("dropping");
    });
    root.addEventListener("dragover", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    });
    root.addEventListener("dragleave", (event) => {
      if (!carriesFiles(event)) return;
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) root.classList.remove("dropping");
    });
    root.addEventListener("drop", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      dragDepth = 0;
      root.classList.remove("dropping");
      attach([...event.dataTransfer.files]);
    });
    // A picture pasted from the clipboard; text that comes with it is pasted as text instead.
    parts.input.addEventListener("paste", (event) => {
      const files = [...event.clipboardData.files];
      if (!files.length || event.clipboardData.types.includes("text/plain")) return;
      event.preventDefault();
      attach(files);
    });

    parts.input.addEventListener("input", grow);
    parts.input.addEventListener("keydown", (event) => {
      // Enter while an input method is composing picks the word, it does not send.
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        submit();
      } else if (event.key === "Escape" && sending) {
        event.stopPropagation();
        stopReply("stopped");
      } else if (event.key === "Escape" && replying) {
        event.stopPropagation();
        setReply(null);
      }
    });
    // Enter sends from the box itself: a submit here is the button, which stops a reply on its way.
    parts.form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (sending) stopReply("stopped");
      else submit();
    });
    // The page's own listeners, taken off again when the chat is deleted.
    const listeners = {
      visibilitychange: () => {
        if (!asking()) clearTimeout(pollTimer);
        else if (visible()) poll();
      },
      // A look under way may have asked before this change: one more once it is done.
      "pa-chats": function told() {
        if (!visible()) return;
        if (polling || sending) setTimeout(told, 500);
        else poll();
      },
      // A card's own menu closes on any click elsewhere.
      click: (event) => {
        if (!event.target.closest(".find-more-wrap")) closeMenus();
      },
      keydown: (event) => {
        if (event.key === "Escape" && root.querySelector(".find-more-wrap.open")) {
          event.stopPropagation();
          closeMenus();
        }
      },
    };
    for (const [type, run] of Object.entries(listeners)) document.addEventListener(type, run);

    function greeting() {
      const hour = new Date().getHours();
      const part = hour < 5 ? "night" : hour < 11 ? "morning" : hour < 13 ? "noon" : hour < 18 ? "afternoon" : hour < 23 ? "evening" : "night";
      return words().greet[part];
    }

    function renderText() {
      root.lang = locale();
      parts.greet.textContent = greeting();
      // A plain chat needs no words of its own: the greeting and the ideas say it.
      parts.emptyText.hidden = !main;
      parts.emptyText.textContent = main ? say("emptyMain") : "";
      // The main chat's ideas may point at its outings; a plain chat's do not.
      const icons = IDEA_ICONS[main ? "main" : "plain"];
      parts.ideas.replaceChildren(
        ...words()[main ? "ideas" : "ideasPlain"].map((idea, index) => {
          const chip = button("chat-idea", "", () => {
            parts.input.value = idea;
            grow();
            parts.input.focus();
          });
          chip.append(svg(icons[index % icons.length]), el("span", "", idea));
          return chip;
        }),
      );
      parts.input.placeholder = say(parts.empty.hidden ? "placeholder" : "placeholderEmpty");
      parts.input.setAttribute("aria-label", say("send"));
      parts.send.setAttribute("aria-label", say(sending ? "stop" : "send"));
      parts.send.title = say(sending ? "stop" : "send");
      plus.render();
      bar.render();
      parts.dropZone.textContent = say("dropHere");
      paintTray();
      for (const name of parts.messages.querySelectorAll(".ai-name")) name.textContent = displayName();
      paintQuote();
      parts.mark.replaceChildren(svg(I.ghost), el("strong", "", say("privateMark")), el("span", "", say("privateNote")));
      parts.privateToggle.replaceChildren(svg(I.ghost), el("span", "", say(privateChat ? "privateLeave" : "privateStart")));
    }

    // Which of the three the page shows: the thread, the empty start, or the way to set a model up.
    function renderSetup() {
      const configured = Boolean(state?.configured);
      parts.off.hidden = configured || !state;
      parts.off.replaceChildren();
      if (!configured && state) {
        const box = el("div", "chat-col chat-off-inner");
        box.append(ctx.bust("chat-bust"), el("h1", "", say("offTitle")), el("p", "", say("offText")),
          settingsButton("pa-btn pa-btn-primary"));
        parts.off.append(box);
      }
      const empty = configured && loaded && !parts.messages.querySelector(".me, .ai, .aside, .chat-error");
      parts.empty.hidden = !empty;
      parts.scroll.hidden = !parts.off.hidden || empty;
      // The one composer moves between the empty start and the dock, text and all.
      // Moved only when it changes place: moving a node takes the focus out of its box.
      const place = empty ? parts.emptySlot : parts.dock.firstChild;
      if (parts.compose.parentNode !== place) place.append(parts.compose);
      parts.dock.hidden = !configured || empty;
      parts.compose.classList.toggle("in-empty", empty);
      // The mark heads the empty start or the thread; the toggle is a plain chat's, before its first message.
      parts.mark.hidden = !privateChat;
      const marked = empty ? emptyCol : column;
      if (parts.mark.parentNode !== marked) marked.prepend(parts.mark);
      parts.privateToggle.hidden = main;
      parts.privateToggle.setAttribute("aria-pressed", String(privateChat));
      parts.input.placeholder = held || say(empty ? "placeholderEmpty" : "placeholder");
      parts.banner.replaceChildren();
      parts.banner.hidden = !(configured && state.modelUnreachable);
      if (!parts.banner.hidden)
        parts.banner.append(svg(I.alert), el("span", "chat-banner-text", say("unreachable")),
          settingsButton("pa-btn pa-btn-link chat-banner-link"));
      syncSend();
    }
    // While a reply is on its way the send button is the stop button.
    function syncSend() {
      if (parts.send.classList.contains("stopping") !== sending || !parts.send.firstChild) {
        parts.send.classList.toggle("stopping", sending);
        parts.send.replaceChildren(svg(sending ? I.stop : I.up));
        parts.send.setAttribute("aria-label", say(sending ? "stop" : "send"));
        parts.send.title = say(sending ? "stop" : "send");
      }
      parts.send.disabled = !sending && (!state?.configured || !(parts.input.value.trim() || attached.length) ||
        attached.some((item) => !item.data));
    }
    function grow() {
      parts.input.style.height = "auto";
      parts.input.style.height = `${Math.min(parts.input.scrollHeight, INPUT_MAX)}px`;
      parts.input.classList.toggle("scrolls", parts.input.scrollHeight > INPUT_MAX);
      syncSend();
    }
    function toBottom() {
      parts.scroll.scrollTop = parts.scroll.scrollHeight;
      atEnd = true;
    }
    // Something above grew after it was drawn: the end stays in sight only if it was.
    const keepEnd = () => atEnd && toBottom();
    // The same when the thread's own box changes size: the window, the chat set beside a page, the box growing.
    new ResizeObserver(keepEnd).observe(parts.scroll);

    // ---- The thread's frame: days, and the character's runs
    function dayLabel(time) {
      const date = new Date(time);
      const start = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
      const back = Math.round((start(new Date()) - start(date)) / 86400000);
      const year = date.getFullYear() !== new Date().getFullYear();
      const day = words().week
        ? `${year ? `${date.getFullYear()}年` : ""}${date.getMonth() + 1}月${date.getDate()}日 ${words().week[date.getDay()]}`
        : date.toLocaleDateString(locale(), { weekday: "short", month: "short", day: "numeric", ...(year ? { year: "numeric" } : {}) });
      const near = back === 0 ? words().today : back === 1 ? words().yesterday : "";
      return near ? `${near} · ${day}` : day;
    }
    const timeOf = (time) =>
      new Date(time).toLocaleTimeString(locale(), paI18n.lang === "en" ? { hour: "numeric", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
    // A new day gets its line before its first message.
    function separate(time) {
      if (!time || dayKey(time) === lastDay) return;
      lastDay = dayKey(time);
      const line = el("div", "day", dayLabel(time));
      line.setAttribute("role", "separator");
      parts.messages.append(line);
    }
    // The open run of the character's messages, or a new one with its face, name and time.
    function botRun(time) {
      const open =
        run && run.node === parts.messages.lastElementChild &&
        (!time || !run.last || (dayKey(time) === dayKey(run.last) && time - run.last < RUN_GAP_MS));
      if (open) {
        if (time) run.last = time;
        return run.body;
      }
      const node = el("section", "ai");
      const body = el("div", "ai-body");
      const meta = el("div", "ai-meta");
      meta.append(el("span", "ai-name", displayName()));
      if (time) {
        const stamp = el("time", "ai-time", timeOf(time));
        stamp.dateTime = new Date(time).toISOString();
        meta.append(stamp);
      }
      body.append(meta);
      node.append(ctx.face("ai-face"), body);
      parts.messages.append(node);
      run = { node, body, last: time };
      return body;
    }
    // A line taken out of a run: a run left with nothing in it goes too.
    function unline(node) {
      const section = node.closest(".ai");
      node.remove();
      if (section && !section.querySelector(".line")) {
        section.remove();
        if (run?.node === section) run = null;
      }
    }

    // ---- Messages
    const isRemark = (item) => item.remark === true || item.kind === "remark";
    async function showPicture(holder, name, alt = say("picture")) {
      try {
        const url = name.startsWith("blob:") ? name : await blob(`/api/chat/picture/${encodeURIComponent(name)}`);
        const image = el("img");
        image.alt = alt;
        image.addEventListener("load", keepEnd, { once: true });
        image.src = url;
        holder.classList.remove("loading", "pa-shimmer");
        holder.replaceChildren(image);
      } catch {
        holder.classList.remove("loading", "pa-shimmer");
        holder.classList.add("gone");
        holder.textContent = say("pictureGone");
      }
    }
    function pictureNode(item) {
      const holder = el("div", "pic loading pa-shimmer");
      if (item.picture) showPicture(holder, item.picture);
      else {
        holder.classList.remove("loading", "pa-shimmer");
        holder.classList.add("gone");
        holder.textContent = say("pictureGone");
      }
      return holder;
    }
    // What the person attached, above their words: pictures as they are, files as chips that open.
    function attachedNode(list) {
      const box = el("div", "me-att");
      for (const item of list) {
        if (item.kind === "picture") {
          const holder = el("div", "pic loading pa-shimmer");
          showPicture(holder, item.url || item.name, say("myPicture"));
          box.append(holder);
          continue;
        }
        // A page from the app's browser opens the way every link opens.
        if (item.kind === "page") {
          const link = window.paPlus.pageChip(item, "a");
          link.href = item.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          box.append(link);
          continue;
        }
        const node = el("div", "att-sent");
        // Stored ones open: those of a message still on its way have no name yet.
        const head = item.name ? button("att att-file", "", () => showFile(node, head, item.name)) : el("div", "att att-file");
        head.append(fileWords(item));
        if (item.name) {
          head.setAttribute("aria-expanded", "false");
          head.setAttribute("aria-label", `${say("showFile")}: ${item.file}`);
        }
        node.append(head);
        box.append(node);
      }
      return box;
    }
    function fileWords(item) {
      const icon = el("span", "att-icon");
      icon.append(svg(I.file));
      const words = el("span", "att-words");
      const size = el("span", "att-size", sizeOf(item.size));
      if (item.clipped) {
        size.textContent += ` · ${say("clipped")}`;
        size.title = say("clippedTip");
      }
      words.append(el("span", "att-name", item.file), size);
      const frag = document.createDocumentFragment();
      frag.append(icon, words);
      return frag;
    }
    async function showFile(node, head, name) {
      const open = head.getAttribute("aria-expanded") !== "true";
      head.setAttribute("aria-expanded", String(open));
      node.querySelector(".att-text")?.remove();
      if (!open) return;
      const text = el("pre", "att-text pa-shimmer");
      node.append(text);
      try {
        text.textContent = await ctx.attachedText(`/api/chat/file/${encodeURIComponent(name)}`);
      } catch {
        text.classList.add("gone");
        text.textContent = say("fileGone");
      }
      text.classList.remove("pa-shimmer");
    }
    // A thing brought with no sentence of its own is said by its title: not twice.
    function saysTitle(text, card) {
      const words = String(text || "").replace(/[…\s.]+$/, "");
      return !words || (card.title || "").startsWith(words) || text === card.url;
    }
    // A link in the character's words, opened the way every link in the app opens.
    function linkNode(url, className = "say-link") {
      const link = el("a", className, url);
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      return link;
    }
    // `text` into `node`, its web addresses as links.
    function linked(node, text) {
      let at = 0;
      for (const match of text.matchAll(/https?:\/\/[^\s<>"'，。！？；）】」]+/g)) {
        const url = match[0].replace(/[.,;:!?)\]]+$/, "");
        node.append(text.slice(at, match.index), window.paLinks.inline(url, links));
        at = match.index + url.length;
      }
      node.append(text.slice(at));
      return node;
    }
    // A fenced code block, drawn as code with a button that copies it.
    function codeNode(lang, code) {
      const box = el("div", "code");
      const head = el("div", "code-head");
      const copy = button("code-copy", say("copy"), async () => {
        try {
          await navigator.clipboard.writeText(code);
          copy.textContent = say("copied");
          setTimeout(() => (copy.textContent = say("copy")), 1500);
        } catch {
          // The code is still there to select by hand.
        }
      });
      head.append(el("span", "code-lang", lang), copy);
      const pre = el("pre");
      pre.append(el("code", "", code));
      box.append(head, pre);
      return box;
    }
    // The character's words are Markdown. Blocks: paragraphs, headings, lists, quotes, tables, rules and
    // fenced code. Inside them: code spans, links, bold, italic and struck text. All of it is drawn as text
    // nodes, so a tag in a reply shows as written; a picture is a link and is never loaded. Underscores,
    // backslashes and an asterisk beside a space or inside a word stay as written (__init__, C:\Users,
    // 2 * 3, x**2).
    const FENCE = /^(\s*)(`{3,}|~{3,})[ \t]*([^`\s]*)[^`]*$/;
    const HEADING = /^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$/;
    const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
    const QUOTE = /^ {0,3}>[ \t]?/;
    const ITEM = /^(\s*)([-*+•]|\d{1,9}[.)])[ \t]+(.*)$/;
    const TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$/;
    const INLINE = new RegExp(
      [
        /(?<!`)(`+)(?!`)([\s\S]*?[^`])\1(?!`)/,
        /!?\[([^\]\n]+)\]\((https?:\/\/[^\s()]*(?:\([^\s()]*\)[^\s()]*)*)(?:[ \t]+"[^"\n]*")?\)/,
        /<(https?:\/\/[^\s<>]+)>/,
        /https?:\/\/[^\s<>"'，。！？；）】」]+/,
        /(?<![A-Za-z0-9*])\*\*(?=[^\s*])([^\n]*?[^\s*])\*\*(?![A-Za-z0-9*])/,
        /~~(?=\S)([^\n]*?\S)~~/,
        /(?<![A-Za-z0-9*])\*(?=[^\s*])([^\n*]*?[^\s*])\*(?![A-Za-z0-9*])/,
      ]
        .map((part) => part.source)
        .join("|"),
      "g",
    );
    function inlineInto(node, text) {
      let at = 0;
      for (const match of text.matchAll(INLINE)) {
        const [whole, ticks, code, label, target, pointed, strong, struck, em] = match;
        node.append(text.slice(at, match.index));
        at = match.index + whole.length;
        if (ticks) node.append(el("code", "", code.replace(/^ ([\s\S]*) $/, "$1")));
        else if (target) {
          const link = linkNode(target);
          link.textContent = label;
          link.title = target;
          node.append(link);
        } else if (pointed) node.append(window.paLinks.inline(pointed, links));
        else if (strong !== undefined) node.append(inlineInto(el("strong"), strong));
        else if (struck !== undefined) node.append(inlineInto(el("del"), struck));
        else if (em !== undefined) node.append(inlineInto(el("em"), em));
        else {
          // A bare address, without the sentence's punctuation after it.
          const url = whole.replace(/[.,;:!?)\]*]+$/, "");
          node.append(window.paLinks.inline(url, links), whole.slice(url.length));
        }
      }
      node.append(text.slice(at));
      return node;
    }
    // The cells of a table row: cut at the bars outside code spans, the outer bars dropped.
    function cellsOf(line) {
      const row = line.trim().replace(/^\|/, "").replace(/(?<!\\)\|$/, "");
      const cells = [];
      let cell = "";
      let code = false;
      for (let n = 0; n < row.length; n += 1) {
        if (row[n] === "\\" && row[n + 1] === "|") {
          cell += "|";
          n += 1;
        } else if (row[n] === "|" && !code) {
          cells.push(cell.trim());
          cell = "";
        } else {
          if (row[n] === "`") code = !code;
          cell += row[n];
        }
      }
      cells.push(cell.trim());
      return cells;
    }
    const tableAt = (lines, n) =>
      lines[n].includes("|") && TABLE_RULE.test(lines[n + 1] || "") && cellsOf(lines[n]).length === cellsOf(lines[n + 1]).length;
    // A table from lines[n] on; the line after it.
    function tableInto(node, lines, n) {
      const sides = cellsOf(lines[n + 1]).map((rule) => (!rule.endsWith(":") ? "" : rule.startsWith(":") ? "center" : "right"));
      const table = el("table");
      const row = (cells, tag) => {
        const line = el("tr");
        sides.forEach((side, column) => {
          const cell = inlineInto(el(tag), cells[column] || "");
          if (side) cell.style.textAlign = side;
          line.append(cell);
        });
        return line;
      };
      table.createTHead().append(row(cellsOf(lines[n]), "th"));
      const body = table.createTBody();
      let end = n + 2;
      for (; end < lines.length && lines[end].includes("|"); end += 1) body.append(row(cellsOf(lines[end]), "td"));
      const box = el("div", "say-table");
      box.append(table);
      node.append(box);
      return end;
    }
    // A list from lines[n] on: the items at its indent, each with the lines indented under it; the line after it.
    function listInto(node, lines, n) {
      const [, indent, mark] = lines[n].match(ITEM);
      const ordered = /\d/.test(mark);
      const list = el(ordered ? "ol" : "ul");
      if (ordered) list.start = parseInt(mark, 10);
      const under = (line) => line.search(/\S/) >= indent.length + 2;
      while (n < lines.length) {
        const item = lines[n].match(ITEM);
        if (!item || Math.abs(item[1].length - indent.length) > 1 || /\d/.test(item[2]) !== ordered) break;
        const inner = [item[3]];
        const width = item[1].length + item[2].length + 1;
        for (n += 1; n < lines.length; n += 1) {
          // A blank line stays in the item only when what follows it is still under the item.
          const next = lines[n].trim() ? lines[n] : lines.slice(n).find((line) => line.trim()) || "";
          if (!under(next)) break;
          inner.push(lines[n].replace(new RegExp(`^\\s{0,${width}}`), ""));
        }
        const li = blocksInto(el("li"), inner);
        // An item of one paragraph is its words alone.
        if (li.children.length === 1 && li.firstChild.tagName === "P") li.replaceChildren(...li.firstChild.childNodes);
        list.append(li);
        while (n < lines.length && !lines[n].trim() && ITEM.test(lines.slice(n).find((line) => line.trim()) || "")) n += 1;
      }
      node.append(list);
      return n;
    }
    const startsBlock = (lines, n) =>
      FENCE.test(lines[n]) || HEADING.test(lines[n]) || RULE.test(lines[n]) || QUOTE.test(lines[n]) || ITEM.test(lines[n]) || tableAt(lines, n);
    function blocksInto(node, lines) {
      for (let n = 0; n < lines.length; ) {
        const line = lines[n];
        const fence = line.match(FENCE);
        const heading = line.match(HEADING);
        if (!line.trim()) n += 1;
        else if (fence) {
          const close = new RegExp(`^\\s*${fence[2][0]}{${fence[2].length},}\\s*$`);
          let end = n + 1;
          while (end < lines.length && !close.test(lines[end])) end += 1;
          const dent = new RegExp(`^\\s{0,${fence[1].length}}`);
          node.append(codeNode(fence[3], lines.slice(n + 1, end).map((inner) => inner.replace(dent, "")).join("\n")));
          n = end + 1;
        } else if (heading) {
          node.append(inlineInto(el(`h${Math.min(6, heading[1].length + 2)}`), heading[2]));
          n += 1;
        } else if (RULE.test(line)) {
          node.append(el("hr"));
          n += 1;
        } else if (tableAt(lines, n)) n = tableInto(node, lines, n);
        else if (QUOTE.test(line)) {
          let end = n;
          while (end < lines.length && QUOTE.test(lines[end])) end += 1;
          node.append(blocksInto(el("blockquote"), lines.slice(n, end).map((inner) => inner.replace(QUOTE, ""))));
          n = end;
        } else if (ITEM.test(line)) n = listInto(node, lines, n);
        else {
          let end = n + 1;
          while (end < lines.length && lines[end].trim() && !startsBlock(lines, end)) end += 1;
          node.append(inlineInto(el("p"), lines.slice(n, end).join("\n")));
          n = end;
        }
      }
      return node;
    }
    // The character's words, drawn (see above).
    function sayNode(text) {
      return blocksInto(el("div", "say rich"), String(text || "").split("\n"));
    }
    // The words with the lines their link cards say taken out; fenced code keeps every line.
    function wordsOf(text) {
      return String(text || "")
        .split(/^([ \t]*```[^\n]*\n[\s\S]*?(?:\n[ \t]*```[ \t]*$|(?![\s\S])))/m)
        .map((part, n) => (n % 2 ? part : window.paLinks.bare(part)))
        .filter(Boolean)
        .join("\n");
    }
    // `loading`: drawn as part of a whole history, which reports to the shell once at its end.
    function showMessage(item, arrived = false, loading = false) {
      const time = when(item.at);
      separate(time);
      let node;
      if (item.role === "user") {
        node = el("section", "me");
        if (item.attachments?.length) node.append(attachedNode(item.attachments));
        const bubble = el("div", "bubble");
        if (item.quote) {
          bubble.classList.add("has-quote");
          bubble.append(quoteNode(item.quote));
        }
        if (item.picture !== undefined) bubble.append(pictureNode(item));
        else if (item.text) bubble.append(linked(el("span", "bubble-text"), item.text));
        // Attachments alone need no empty bubble under them.
        if (bubble.firstChild) node.append(bubble);
        const sent = item.picture === undefined && linkCards(item.text);
        if (sent) node.append(sent);
        if (item.choices?.length) node.append(plus.sent(item.choices));
        parts.messages.append(node);
        if (item.failed) failed(node, item.failed, item.text, item.seq, item.quote, item.attachments || [], item.choices || []);
      } else if (item.role === "memory") {
        // What the background memory kept from this chat: one quiet line under the last reply (activity.js).
        node = window.paActivity.saved(item, links);
        const last = parts.messages.lastElementChild;
        (last?.classList.contains("ai") ? last.querySelector(".ai-body") : parts.messages).append(node);
      } else if (isRemark(item)) {
        // A passing remark: no face, quieter, on the text's edge.
        node = el("p", "aside");
        node.append(el("span", "", item.text));
        if (time) {
          const stamp = el("time", "", timeOf(time));
          stamp.dateTime = new Date(time).toISOString();
          node.append(stamp);
        }
        parts.messages.append(node);
      } else {
        const body = botRun(time);
        node = el("div", "line");
        // What it did for the reply, above its words (activity.js): above the first of them when it said several.
        const did = window.paActivity.summary(item, links);
        const first = did && body.querySelector(":scope > .line:not(.typing)");
        if (first) first.before(did);
        else if (did) node.append(did);
        // A line that is only an address is said by its card.
        const words = wordsOf(item.text);
        if (item.picture !== undefined) node.append(pictureNode(item));
        else if (!(item.card && saysTitle(item.text, item.card)) && words) node.append(sayNode(words));
        // Each card answered beside the page while it worked in it: one line, kept with the reply.
        for (const step of item.trace || []) {
          if (step.tool === "operate" && step.confirmed) for (const line of step.confirmed.split("\n")) node.append(el("p", "op-note", line));
        }
        const cards = item.picture === undefined && linkCards(item.text, item.card?.url);
        if (cards) node.append(cards);
        const from = window.paActivity.sources(item.sources, links);
        if (from) node.append(from);
        // The memories the reply drew on, each with edit and forget (activity.js).
        const recalled = window.paActivity.recalled(item, links);
        if (recalled) node.append(recalled);
        // The files the reply changed, with 撤销这次的改动 (activity.js).
        const changed = window.paActivity.changes(item, links);
        if (changed) node.append(changed);
        // Under the answer to a memory command, the way to Settings › Memory (welcome.py marks its last line).
        if (item.manage) node.append(button("pa-btn pa-btn-link chat-manage", say("manageMemory"), () => ctx.go("setup/memory")));
        if (item.card) {
          node.classList.add("has-find");
          node.append(findCard(item.card));
          if (item.card.id) node.dataset.item = item.card.id;
        }
        // A scheduled task's mark, what the reply did to tasks, and what a run left for them (schedulechat.js).
        window.paScheduleChat.decorate(node, item, (text) => !sending && send(text));
        body.append(node);
      }
      node.dataset.seq = item.seq || "";
      if (arrived) node.classList.add("arrived");
      seen = Math.max(seen, item.seq || 0);
      parts.empty.hidden = true;
      if (loading) return node;
      // The main chat's last words from the character stand in when what it is doing is not known.
      if (main && item.role !== "user" && item.text) hooks.said?.(id, item.text);
      // What arrives on its own does not pull someone reading further up down to it.
      if (arrived) keepEnd();
      else toBottom();
      if (visible()) hooks.seen(id, seen);
      return node;
    }
    // A find quoted at the head of the person's bubble: a rule, its picture, its title.
    function quoteNode(quote) {
      const node = el("div", "quote");
      if (quote.picture) {
        const thumb = el("span", "quote-thumb");
        const img = el("img");
        img.alt = "";
        blob(quote.picture)
          .then((url) => {
            img.src = url;
          })
          .catch(() => thumb.remove());
        thumb.append(img);
        node.append(thumb);
      }
      node.append(el("span", "quote-title", quote.title || quote.site || ""));
      return node;
    }
    // Under a message that went unanswered: why, and a way to send it again.
    function failed(mine, code, text, retrySeq = null, quote = null, files = [], choices = []) {
      // Stopped by the person: said quietly, with only the way to send it again.
      const quiet = code === "stopped";
      mine.classList.add("unanswered");
      mine.classList.toggle("stopped", quiet);
      const mark = el("small", "msg-mark");
      if (quiet) mark.append(el("span", "", say("stopped")));
      else mark.append(svg(I.alert), el("span", "", say("unanswered")));
      mine.append(mark);
      const note = el("div", quiet ? "notice quiet" : "notice");
      if (!quiet) note.append(bar.problem(code) || el("p", "", say(code)));
      const actions = el("div", "notice-actions");
      actions.append(
        button("pa-btn pa-btn-secondary pa-btn-sm", say("retry"), () => {
          if (sending) return;
          note.remove();
          mine.remove();
          send(text, retrySeq, quote, files, choices);
        }),
      );
      if (SETTINGS_FIXES.has(code)) actions.append(settingsButton("pa-btn pa-btn-ghost pa-btn-sm"));
      note.append(actions);
      mine.after(note);
      toBottom();
    }
    function typingRow() {
      const body = botRun(Date.now());
      const node = el("div", "line typing");
      const dots = el("span", "dots");
      dots.setAttribute("role", "status");
      dots.setAttribute("aria-label", say("typing"));
      dots.append(el("i"), el("i"), el("i"));
      // While a reply is on its way: what the character is looking up for it.
      node.append(dots, el("span", "doing"));
      body.append(node);
      keepEnd();
      return node;
    }
    // What came, shown at once and in order.
    function reveal(items) {
      for (const item of items) if (!(item.seq && item.seq <= seen)) showMessage(item, item.role !== "user");
    }
    // The marks of a reply being waited for go.
    function stopReveal() {
      for (const node of parts.messages.querySelectorAll(".line.typing")) unline(node);
    }
    function showHistory(value) {
      stopReveal();
      // A video playing in the thread drawn before stops with it, and how long it ran is told.
      for (const node of parts.messages.querySelectorAll(".find.is-playing")) {
        node.querySelector(".player").remove();
        players.get(node)?.();
      }
      parts.messages.replaceChildren();
      lastDay = "";
      run = null;
      seen = 0;
      tag = value.chat || "";
      if (privateChat !== Boolean(value.private)) {
        privateChat = Boolean(value.private);
        renderText();
      }
      if (value.trimmed && value.messages.length) parts.messages.append(el("p", "chat-trimmed", say("trimmed")));
      for (const item of value.messages) showMessage(item, false, true);
      const said = value.messages.findLast((item) => item.role !== "user" && item.text);
      if (main && said) hooks.said?.(id, said.text);
      seen = Math.max(seen, value.last || 0);
      if (visible()) hooks.seen(id, seen);
      toBottom();
    }
    // A plain chat deleted from another window: the shell lets it go.
    function lost(error) {
      if (error?.message !== "invalid_chat") return false;
      hooks.gone(id);
      return true;
    }

    // ---- A thing it brought: a 16:10 cover beside a body laid to its height, a fixed foot
    function findCard(card) {
      const node = el("article", "find");
      if (card.skipped) return folded(node, card);
      const site = String(card.site || "").replace(/^www\./, "") || hostOf(card.url);
      const video = card.kind === "video" && card.embed;
      const cover = video ? el("div", "cover") : original(card, "cover");
      if (!video) {
        cover.tabIndex = -1;
        cover.setAttribute("aria-hidden", "true");
      }
      if (card.picture) {
        const img = el("img");
        img.alt = "";
        img.decoding = "async";
        cover.classList.add("pa-shimmer");
        img.addEventListener("load", () => cover.classList.remove("pa-shimmer"), { once: true });
        blob(card.picture)
          .then((url) => {
            img.src = url;
          })
          .catch(() => {
            // A picture that does not arrive leaves the quiet cover, not a broken frame.
            img.remove();
            cover.classList.remove("pa-shimmer");
            cover.classList.add("blank");
            cover.append(svg(siteIcon(card), "i cover-mark"));
          });
        cover.append(img);
      } else {
        cover.classList.add("blank");
        cover.append(svg(siteIcon(card), "i cover-mark"));
      }
      if (video) {
        const play = button("play", "", () => playIn(node, card));
        play.setAttribute("aria-label", `${say("play")}: ${card.title || site}`);
        play.append(svg(PLAY, "play-mark"));
        cover.append(play);
        if (card.length) cover.append(el("span", "dur", clock(card.length)));
      }

      const body = el("div", "find-body");
      const top = el("div", "find-top");
      const icon = el("span", "site");
      icon.append(svg(siteIcon(card)));
      top.append(icon, el("span", "src", site));
      if (card.author && card.author !== site) top.append(el("span", "dot", "·"), el("span", "by", card.author));
      if (card.why) top.append(whyChip(card));
      const heading = el("h3");
      const link = original(card, "find-title");
      link.textContent = card.title || site;
      heading.append(link);
      body.append(top, heading);
      if (card.excerpt) body.append(el("p", "find-text", card.excerpt));

      // The cover and the site's icon already say what kind it is: the foot says only how old.
      const foot = el("div", "find-foot");
      if (card.at) {
        const stamp = el("time", "", ago(card.at));
        stamp.dateTime = card.at;
        stamp.title = new Date(card.at).toLocaleString(locale());
        foot.append(stamp);
      }
      foot.append(actions(card, node));
      body.append(foot);
      node.append(cover, body);
      return node;
    }
    // The player is put in place only when asked: an embedded player is heavy
    // and would start talking to its site as soon as it loads.
    function playIn(node, card) {
      const player = el("div", "player");
      const frame = el("iframe");
      const src = new URL(card.embed);
      src.searchParams.set("autoplay", "1");
      // YouTube's player says when it plays and pauses, once asked to.
      const youtube = YOUTUBE.includes(src.hostname);
      // The origin tells the player where to send its replies, as YouTube's own API does.
      if (youtube) {
        src.searchParams.set("enablejsapi", "1");
        src.searchParams.set("origin", location.origin);
      }
      const ran = playClock(frame, youtube ? src.origin : "");
      frame.src = src.href;
      frame.title = card.title || "";
      frame.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
      // The page itself sends no referrer; without one YouTube refuses to play (its error 153).
      frame.referrerPolicy = "strict-origin-when-cross-origin";
      // Each telling carries the whole time so far, so only a second more than last time is told;
      // once the player is gone (收起, folded, the chat redrawn) it is told for the last time.
      let told = 0;
      players.set(node, () => {
        const seconds = ran.seconds();
        // Kept alive: the last telling may come as the window closes.
        if (seconds - told >= 1) react(card, "play", { play_s: Math.round(seconds * 10) / 10 }, { keepalive: true });
        told = Math.max(told, seconds);
        if (frame.isConnected) return;
        ran.stop();
        players.delete(node);
        offView.unobserve(node);
      });
      offView.observe(node);
      // 收起 sits on the card's own line under the video, never over the site's player.
      const close = button("player-close", "", () => {
        player.remove();
        close.remove();
        players.get(node)?.();
        node.classList.remove("is-playing");
        node.querySelector(".play")?.focus();
      });
      close.append(svg(I.shrink), el("span", "", say("collapse")));
      player.append(frame);
      node.prepend(player);
      node.querySelector(".find-top").append(close);
      node.classList.add("is-playing");
      keepEnd();
      // Watching it in place is the video's way of opening it.
      react(card, "open");
    }
    function original(card, className) {
      const link = el("a", className);
      link.href = card.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.addEventListener("click", () => react(card, "open"));
      return link;
    }
    function actions(card, node) {
      const row = el("span", "acts");
      // Only a thing from the library can be answered or reacted to; anything can be opened.
      if (card.id) {
        const reply = button("reply-btn", "", () => setReply(card));
        reply.append(svg(I.reply), el("span", "", say("reply")));
        const like = iconButton("icon-btn like", I.heart, say("like"), async () => {
          const liked = !card.liked;
          setLiked(like, card, liked);
          if (liked) {
            like.classList.remove("pop");
            void like.offsetWidth;
            like.classList.add("pop");
          }
          try {
            await ctx.api("feed/react", reaction(card, liked ? "like" : "unlike"));
          } catch {
            setLiked(like, card, !liked);
          }
        });
        setLiked(like, card, Boolean(card.liked));
        row.append(reply, like);
      }
      const open = original(card, "icon-btn");
      open.append(svg(I.open));
      open.setAttribute("aria-label", say("open"));
      open.title = say("open");
      row.append(open);
      if (card.id) row.append(moreMenu(card, node));
      return row;
    }
    // Why this one (recommend.Why), beside the site: a press offers more or less of what it names, the
    // lean Settings shows too; a press on the one already set takes it back.
    function whyChip(card) {
      const why = card.why;
      if (!why.key) return el("span", "find-why", why.text);
      const wrap = el("span", "find-more-wrap find-why-wrap");
      const menu = el("div", "find-menu");
      menu.setAttribute("role", "menu");
      const chip = button("find-why", why.text, () => {
        const open = !wrap.classList.contains("open");
        closeMenus();
        wrap.classList.toggle("open", open);
        chip.setAttribute("aria-expanded", String(open));
        if (open) menu.querySelector("button").focus();
      });
      chip.setAttribute("aria-haspopup", "menu");
      chip.setAttribute("aria-expanded", "false");
      const choice = (to, paths, text) => {
        const node = button("", "", () => {
          closeMenus();
          why.lean = why.lean === to ? null : to;
          for (const other of menu.children) other.setAttribute("aria-checked", String(other === node && why.lean === to));
          ctx.api("feed/lean", { key: why.key, to: why.lean }).catch(() => {});
        });
        node.setAttribute("role", "menuitemradio");
        node.setAttribute("aria-checked", String(why.lean === to));
        node.append(svg(paths), el("span", "", text));
        return node;
      };
      menu.append(choice(1, I.raise, say("leanMore")), choice(-1, I.less, say("leanLess")));
      wrap.append(chip, menu);
      return wrap;
    }
    // Not interested, behind ⋯, with what it was about: this one, its kind of topic, its source, or
    // seen already (which says nothing of their taste).
    function moreMenu(card, node) {
      const wrap = el("span", "find-more-wrap");
      const menu = el("div", "find-menu");
      menu.setAttribute("role", "menu");
      const item = (paths, text, kind) => {
        const choice = button("", "", () => {
          closeMenus();
          fold(card, node, kind);
        });
        choice.setAttribute("role", "menuitem");
        choice.append(svg(paths), el("span", "", text));
        return choice;
      };
      menu.append(item(I.skip, say("skip"), "skip"));
      if (card.topic) menu.append(item(I.less, say("lessTopic", card.topic.name), "topic"));
      if (card.source) menu.append(item(I.less, say("less"), "hide_source"));
      menu.append(item(I.seen, say("known"), "known"));
      const more = iconButton("icon-btn", I.more, say("more"), () => {
        const open = !wrap.classList.contains("open");
        closeMenus();
        wrap.classList.toggle("open", open);
        more.setAttribute("aria-expanded", String(open));
        if (open) menu.querySelector("button").focus();
      });
      more.setAttribute("aria-haspopup", "menu");
      more.setAttribute("aria-expanded", "false");
      wrap.append(more, menu);
      return wrap;
    }
    function closeMenus() {
      for (const wrap of root.querySelectorAll(".find-more-wrap.open")) {
        wrap.classList.remove("open");
        wrap.firstChild.setAttribute("aria-expanded", "false");
      }
    }
    function setLiked(like, card, liked) {
      card.liked = liked;
      like.setAttribute("aria-pressed", String(liked));
      like.classList.toggle("loved", liked);
      like.setAttribute("aria-label", say(liked ? "liked" : "like"));
      like.title = say(liked ? "liked" : "like");
    }
    // The one line a card turned down folds to: what the no was, and the way to take it back.
    function folded(node, card) {
      const key = { topic: "topicDone", hide_source: "lessDone", known: "knownDone" }[card.skipped] || "skipped";
      const note = el("div", "find-folded-line");
      const undo = button("find-undo", say("undo"), async () => {
        undo.disabled = true;
        try {
          await ctx.api("feed/undo", { id: card.id });
        } catch {
          // Not taken back: the line stays, to be tried again.
          undo.disabled = false;
          return;
        }
        card.skipped = "";
        node.replaceWith(findCard(card));
      });
      note.append(svg(I.skip), el("span", "", say(key, card.topic?.name)), undo);
      node.className = "find folded";
      node.style.height = "";
      node.replaceChildren(note);
      return node;
    }
    // Not for me, in the way `kind` says (skip: this one; topic; hide_source; known: seen already): the
    // card folds down to one quiet line, so the chat below it does not jump.
    async function fold(card, node, kind) {
      // A video playing on it stops here, and how long it ran is told.
      node.querySelector(".player")?.remove();
      players.get(node)?.();
      node.style.height = `${node.offsetHeight}px`;
      void node.offsetHeight;
      node.classList.add("folding");
      node.style.height = "44px";
      card.skipped = kind;
      setTimeout(() => card.skipped && folded(node, card), motion() === "auto" ? 0 : 260);
      const sent =
        kind === "known"
          ? reaction(card, "end", { end: "known" })
          : kind === "hide_source"
            ? reaction(card, kind)
            : reaction(card, "skip", { said: kind === "topic" ? { about: "topic", key: card.topic.key } : { about: "thing" } });
      try {
        await ctx.api("feed/react", sent);
      } catch {
        // Not recorded: the card comes back as it was.
        card.skipped = "";
        setTimeout(() => node.replaceWith(findCard(card)), 300);
      }
    }
    // What the feed learns from: where the card is (its place in the batch it came in) and the ranking's slot.
    function reaction(card, action, more = {}) {
      return { id: card.id, action, surface: card.surface || "chat", rank: card.rank ?? 0, slot: card.slot || "", ...more };
    }
    function react(card, kind, more, how) {
      // Opening still works when the note does not arrive; nothing to undo.
      if (card.id) ctx.api("feed/react", reaction(card, kind, more), how).catch(() => {});
    }
    function ago(at) {
      const then = new Date(at);
      const seconds = (then.getTime() - Date.now()) / 1000;
      const format = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
      const steps = [
        [60, 1, "second"],
        [3600, 60, "minute"],
        [86400, 3600, "hour"],
        [604800, 86400, "day"],
      ];
      if (Math.abs(seconds) < 45) return format.format(0, "second");
      for (const [limit, size, unit] of steps) {
        if (Math.abs(seconds) < limit) return format.format(Math.round(seconds / size), unit);
      }
      return then.toLocaleDateString(locale(), { month: "short", day: "numeric" });
    }

    // ---- Answering one thing it brought: the thing rides on the composer until sent
    function setReply(card) {
      replying = card ? { id: card.id, title: card.title || "", kind: card.kind || "", site: card.site || "", picture: card.picture || "" } : null;
      paintQuote();
      if (card) {
        parts.input.focus();
        keepEnd();
      }
    }
    function paintQuote() {
      parts.quote.hidden = !replying;
      parts.form.classList.toggle("has-quote", Boolean(replying));
      if (!replying) return parts.quote.replaceChildren();
      const line = quoteNode(replying);
      line.setAttribute("aria-label", say("replyTo").replace("{title}", replying.title));
      parts.quote.replaceChildren(line, iconButton("chat-quote-x", I.x, say("cancelReply"), () => {
        setReply(null);
        parts.input.focus();
      }));
    }

    // ---- Attaching: picked with +, pasted, or dropped on the chat
    function attach(files) {
      if (!state?.configured || !files.length) return;
      refuse("");
      for (const file of files) {
        if (attached.length >= ATTACH.most) {
          refuse(say("refusedMany"));
          break;
        }
        const kind = PICTURE_TYPES.has(file.type) ? "picture" : "file";
        const item = { key: ++attachKey, kind, file: file.name || "image.png", size: file.size, url: "", data: "" };
        const why =
          kind === "picture"
            ? file.size > ATTACH.picked && "refusedPictureSize"
            : !TEXT_KINDS.has(extOf(item.file))
              ? "refusedType"
              : file.size > ATTACH.file && "refusedFileSize";
        if (why) {
          refuse(say(why).replace("{file}", item.file));
          continue;
        }
        attached.push(item);
        (async () => {
          if (kind === "file") {
            if (!readsAsText(await file.arrayBuffer())) throw new Error("unreadable");
            item.data = await base64Of(file);
            return;
          }
          const picture = await sendable(file);
          item.size = picture.size;
          item.data = await base64Of(picture);
          item.url = URL.createObjectURL(picture);
        })()
          .catch(() => {
            unattach(item);
            refuse(say(kind === "picture" ? "refusedPicture" : "refusedUnreadable").replace("{file}", item.file));
          })
          .finally(paintTray);
      }
      paintTray();
      parts.input.focus();
    }
    function unattach(item) {
      attached = attached.filter((other) => other !== item);
      if (item.url && item.kind !== "page") URL.revokeObjectURL(item.url);
      paintTray();
    }
    // Something "+" adds that is no file: a page from the app's browser ({ kind: "page", url, title }).
    function attachItem(item) {
      refuse("");
      if (attached.length >= ATTACH.most) return refuse(say("refusedMany"));
      attached.push({ ...item, key: ++attachKey, file: item.title || item.url, data: item.kind });
      paintTray();
      parts.input.focus();
    }
    function paintTray() {
      parts.tray.hidden = !attached.length;
      parts.form.classList.toggle("has-tray", attached.length > 0);
      const chips = attached.map((item) => {
        const chip = el("div", `att att-${item.kind}${item.data ? "" : " pa-shimmer"}`);
        chip.title = item.file;
        if (item.kind === "picture") {
          if (item.url) {
            const image = el("img");
            image.alt = item.file;
            image.src = item.url;
            chip.append(image);
          }
        } else if (item.kind === "page") {
          chip.className = "att att-file att-page";
          chip.append(...window.paPlus.pageChip(item).childNodes);
        } else chip.append(fileWords(item));
        chip.append(iconButton("att-x", I.x, say("remove").replace("{file}", item.file), () => {
          unattach(item);
          parts.input.focus();
        }));
        return chip;
      });
      const row = el("div", "chat-tray-row");
      row.append(...chips);
      parts.tray.replaceChildren(row);
      syncSend();
    }
    function refuse(text) {
      parts.refused.hidden = !text;
      parts.refused.replaceChildren();
      if (text)
        parts.refused.append(svg(I.alert), el("span", "chat-refused-text", text),
          iconButton("chat-quote-x", I.x, say("dismiss"), () => refuse("")));
    }

    // A 无痕对话, or back to a plain chat: a new chat of that kind opens in its place (app.js).
    function setPrivate(on) {
      ctx.newChat(on);
    }

    // ---- Sending
    function submit() {
      if (sending) {
        // The text stays in the box: say why Enter did nothing.
        flash(say("wait"));
        return;
      }
      const text = parts.input.value;
      // A subject for its next walk goes there, not into the chat.
      if (plus.wonder(text)) {
        parts.input.value = "";
        grow();
        return;
      }
      if (!(text.trim() || attached.length) || !state?.configured || attached.some((item) => !item.data)) return;
      if (!bar.allow(submit)) return;
      const quote = replying;
      const files = attached;
      attached = [];
      refuse("");
      parts.input.value = "";
      setReply(null);
      grow();
      paintTray();
      // Beside a page in the app's browser, the page it is about goes with it (browse.js).
      send(text, null, quote, files, [...plus.take(), ...(window.paBeside?.take() || [])]);
    }
    async function send(text, retrySeq = null, quote = null, files = [], choices = []) {
      sending = true;
      syncSend();
      // What the character was sending on its own comes back in order with the answer.
      stopReveal();
      const mine = showMessage({ role: "user", text, quote, attachments: files, choices, at: new Date().toISOString() });
      renderSetup();
      const typing = typingRow();
      // The looks for what it sends on its own say meanwhile what it is looking up.
      waiting = typing;
      cut = new AbortController();
      turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
      poll();
      // The first message of a plain chat names it: shown at once, and as kept once the list is read again.
      const naming = text.trim() || files.find((item) => item.kind === "file")?.file || "";
      if (!main && !title && naming) hooks.changed(id, Array.from(naming.replace(/\s+/g, " ")).slice(0, TITLE_CLIP).join(""));
      try {
        // A message sent again after a reload has only its stored attachments: the chat sends those again.
        const attachments = files.filter((item) => item.data).map(({ kind, file, data, url }) =>
          kind === "page" ? { kind, url, title: file } : { kind, name: file, data });
        const value = await ctx.api("chat", { text, after: seen, retry: retrySeq, chat: id, reply_to: quote?.id || "", attachments, choices },
          { signal: cut.signal });
        for (const item of files) if (item.url) URL.revokeObjectURL(item.url);
        // The chat as recorded: anything sent meanwhile, this message and the reply.
        mine.remove();
        unline(typing);
        if (value.forgotLines || value.chat !== tag) showHistory(value.forgotLines ? value : await history());
        else reveal(value.messages);
      } catch (error) {
        unline(typing);
        if (lost(error)) return;
        const why = cut.signal.aborted ? String(cut.signal.reason) : "";
        // The service marks the message stopped once its reply has ended; a page process that hangs is not waited for.
        if (why) await Promise.race([stopping, new Promise((done) => setTimeout(done, 5000))]);
        // Stopped: the words go back in the box, to edit and send again.
        if (why === "stopped" && !parts.input.value) {
          parts.input.value = text;
          grow();
        }
        // A message the chat kept unanswered shows as kept, so it can be sent again after a reload too.
        let kept = null;
        try {
          kept = await history();
        } catch {
          // Not reachable: the notice below still offers to send it again.
        }
        const last = kept?.messages.at(-1);
        if (last && last.role === "user" && last.failed && last.text === text) showHistory(kept);
        else failed(mine, why || (error instanceof TypeError ? "network" : error.message), text, retrySeq, quote, files, choices);
        if (error.message === "chat_failed") refreshState();
      } finally {
        clearTimeout(turnLimit);
        showCard(null);
        showShellCard(null);
        showDeskCard(null);
        window.paToolCard.show(null, null);
        cut = stopping = null;
        hooks.doing("");
        sending = false;
        waiting = null;
        syncSend();
        if (visible()) parts.input.focus();
        if (!main) hooks.changed();
        poll();
        bar.refresh();
      }
    }
    // Something with an effect outside (web/gate.py) waits on the person's press in the column beside the page
    // (browse.js), which only this window's page can give: here the waiting row says so, and once it is answered
    // one line keeps what was decided.
    function showCard(card, answered = null) {
      if (!waiting) return;
      const label = waiting.querySelector(".doing");
      if (card && label) label.textContent = say("opWaitingPage");
      if (!answered || noted.has(answered.id)) return;
      noted.add(answered.id);
      waiting.before(el("p", "op-note", answered.said));
      keepEnd();
    }
    // A card before a command runs on this computer (shell/panel.py): what it will do in the app's own words, the
    // real list of files for a delete, the exact command under a fold; or a card asking for a folder. Only a press
    // here answers it, never words typed in the chat. The card that turns a switched-off ability on (turnon.py) is
    // drawn here too, with the words it carries.
    function showShellCard(card, place = "shell/card") {
      if (shellCard && (!card || shellCard.dataset.id !== card.id)) {
        if (!shellCard.classList.contains("answered")) shellCard.remove();
        shellCard = null;
      }
      if (!card || shellCard || !waiting) return;
      const said = card[ctx.lang] || card.en;
      const folder = card.type === "folder";
      const own = card.type === "turn_on";
      const node = el("div", "op-card shell-card");
      node.dataset.id = card.id;
      node.append(folder ? cardHead(I.folder, said.title, card.folder)
        : own ? cardHead(card.feature === "programs" ? I.window : I.terminal, said.title)
        : cardHead(I.terminal, said.title, say("shellAsk")));
      const lines = el("ul", "shell-card-lines");
      for (const line of said.lines || []) lines.append(el("li", "", line));
      if (lines.childElementCount) node.append(lines);
      // An edit's whole difference, indentation kept (activity.js).
      if (card.diff) node.append(window.paActivity.diff(card.diff));
      if (!folder && !own) {
        const facts = el("dl", "op-card-facts");
        facts.append(el("dt", "", say("shellWhere")), el("dd", "", card.cwd));
        node.append(facts);
        const fold = el("details", "shell-card-command");
        fold.append(el("summary", "", say("shellCommand")), el("pre", "", card.command));
        node.append(fold);
      }
      // `how`: {read: true} grants a folder read only, {other: true} asks the character for another way,
      // {scope: "chat" | "always"} allows it for this conversation or always for this program in this folder.
      const answer = async (yes, how = {}) => {
        for (const b of node.querySelectorAll("button")) b.disabled = true;
        node.classList.add("answered");
        const told = el("p", "op-card-told", own ? (yes ? said.on : said.off)
          : say(how.read ? "folderReadAllowed" : how.other ? "shellOtherAsked"
            : how.scope === "chat" ? "shellChatAllowed" : how.scope === "always" ? "shellAlwaysAllowed"
            : folder ? (yes ? "folderAllowed" : "folderDenied") : (yes ? "shellAllowed" : "shellDenied")));
        node.append(told);
        try {
          const response = await fetch(`/api/${place}`, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json",
              "X-Mibo-Setup-Token": sessionStorage.getItem("mibo-setup-token") || "",
              "X-Mibo-Window": sessionStorage.getItem("mibo-frame") || "" },
            body: JSON.stringify({ id: card.id, yes, ...how }),
          });
          if (!(await response.json().catch(() => ({}))).ok) told.textContent = say("opGone");
        } catch {
          told.textContent = say("opGone");
        }
      };
      const buttons = el("div", "op-card-buttons");
      if (folder && card.choices?.includes("read")) {
        // Read only, or sort in it too (WorkBuddy's 问一问 / 做一做); the narrower first.
        buttons.append(button("pa-btn pa-btn-primary pa-btn-sm", say("folderRead"), () => answer(true, { read: true })),
          button("pa-btn pa-btn-secondary pa-btn-sm", say("folderWrite"), () => answer(true)));
      } else if (card.other_way) {
        // What the command does cannot be told first: asking for another way leads, not reading the command.
        buttons.append(button("pa-btn pa-btn-primary pa-btn-sm", say("shellOtherWay"), () => answer(false, { other: true })),
          button("pa-btn pa-btn-secondary pa-btn-sm", say("shellYes"), () => answer(true)));
      } else {
        buttons.append(button("pa-btn pa-btn-primary pa-btn-sm", own ? said.yes : say(folder ? "folderYes" : "shellYes"), () => answer(true)));
        // What the app lets a wider yes cover (shell/panel.py _scope): this conversation, or this program here.
        if (card.choices?.includes("chat"))
          buttons.append(button("pa-btn pa-btn-secondary pa-btn-sm", say("shellChat"), () => answer(true, { scope: "chat" })));
        if (card.choices?.includes("always"))
          buttons.append(button("pa-btn pa-btn-secondary pa-btn-sm", say("shellAlways"), () => answer(true, { scope: "always" })));
      }
      buttons.append(button("pa-btn pa-btn-ghost pa-btn-sm", own ? said.no : say(folder ? "folderNo" : "shellNo"), () => answer(false)));
      node.append(buttons);
      waiting.after(node);
      shellCard = node;
      const label = waiting.querySelector(".doing");
      if (label) label.textContent = say("opWaiting");
      keepEnd();
    }
    // A card from a task in another program (desk/panel.py): may it do this one thing with an effect outside, shown
    // with the program, the window, the control, the exact text or the files, and the window's picture. Only a
    // press here answers it, never words typed in the chat.
    function showDeskCard(card) {
      if (deskCard && (!card || deskCard.dataset.id !== card.id)) {
        if (!deskCard.classList.contains("answered")) deskCard.remove();
        deskCard = null;
      }
      if (!card || deskCard || !waiting) return;
      // The card that asks to send a window's pictures: this once, always, or no.
      const three = (card.choices || []).includes("always");
      const node = el("div", "op-card desk-card");
      node.dataset.id = card.id;
      const facts = el("dl", "op-card-facts");
      const fact = (key, value, className = "") => {
        if (value) facts.append(el("dt", "", say(key)), el("dd", className, value));
      };
      if (String(card.type).startsWith("see_") && (card.text || card.said)) {
        // Working the window by its picture (see/): the app's own sentence, and the picture with the spot ringed.
        node.append(cardHead(I.window, card.said || card.text, card.window));
        if (card.said && card.text) node.append(el("p", "op-card-quote", card.text));
        if (card.picture) {
          const shot = el("img", "op-card-shot");
          shot.src = `data:image/jpeg;base64,${card.picture}`;
          shot.alt = "";
          node.append(shot);
        }
      } else {
        const kind = words().deskKinds[card.kind] || card.kind;
        node.append(cardHead(I.window, say("deskAsk").replace("{program}", card.program).replace("{kind}", kind), card.window));
        fact("deskTarget", card.target);
        fact(card.kind === "file" && card.files?.length ? "deskNewName" : "deskText", card.text, "op-card-text");
        fact("deskFiles", (card.files || []).join("\n"), "op-card-text");
        node.append(facts);
        if (card.picture) {
          const shot = el("img", "op-card-shot");
          shot.src = `data:image/jpeg;base64,${card.picture}`;
          shot.alt = "";
          node.append(shot);
        }
      }
      const answer = async (value) => {
        for (const b of node.querySelectorAll("button, input")) b.disabled = true;
        node.classList.add("answered");
        const told = el("p", "op-card-told", say(three ? { once: "deskOnceDone", always: "deskAlwaysDone", no: "deskRefused" }[value]
          : (value ? "deskDone" : "deskRefused")));
        node.append(told);
        try {
          const response = await fetch("/api/programs/card", {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json",
              "X-Mibo-Setup-Token": sessionStorage.getItem("mibo-setup-token") || "",
              "X-Mibo-Window": sessionStorage.getItem("mibo-frame") || "" },
            body: JSON.stringify(three ? { id: card.id, answer: value } : { id: card.id, yes: value }),
          });
          if (!(await response.json().catch(() => ({}))).ok) told.textContent = say("opGone");
        } catch {
          told.textContent = say("opGone");
        }
      };
      const buttons = el("div", "op-card-buttons");
      if (three) {
        buttons.append(button("pa-btn pa-btn-primary pa-btn-sm", say("deskOnce"), () => answer("once")),
          button("pa-btn pa-btn-secondary pa-btn-sm", say("deskAlways"), () => answer("always")),
          button("pa-btn pa-btn-ghost pa-btn-sm", say("deskNo"), () => answer("no")));
      } else {
        buttons.append(button("pa-btn pa-btn-primary pa-btn-sm", say("opYes"), () => answer(true)),
          button("pa-btn pa-btn-ghost pa-btn-sm", say("opNo"), () => answer(false)));
      }
      node.append(buttons);
      waiting.after(node);
      deskCard = node;
      const label = waiting.querySelector(".doing");
      if (label) label.textContent = say("opWaiting");
      keepEnd();
    }
    // The stop button, Esc, or the wait running past TURN_MS: the service cancels the reply, the page stops waiting.
    function stopReply(why) {
      if (!cut || cut.signal.aborted) return;
      stopping = ctx.api("chat/stop", { chat: id, why }).catch(() => null);
      cut.abort(why);
    }
    let flashTimer = null;
    function flash(text) {
      parts.hint.textContent = text;
      parts.hint.classList.add("flash");
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => {
        parts.hint.classList.remove("flash");
        parts.hint.textContent = "";
      }, 2500);
    }

    // ---- Looking for what the character sent on its own
    async function poll() {
      clearTimeout(pollTimer);
      if (!asking() || !state?.configured) return;
      if (sending && waiting) {
        try {
          const step = (await history(seen)).doing;
          const line = window.paActivity.live(waiting, step);
          // Working in the browser takes its time, and may wait on the person's press.
          if (step?.tool === "operate" && sending) {
            clearTimeout(turnLimit);
            turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
            const asked = await ctx.api("browser/confirm");
            showCard(asked.card, asked.answered);
          }
          // A command may wait on the person's press too (shell/panel.py).
          if (step?.tool === "shell" && sending) {
            clearTimeout(turnLimit);
            turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
            showShellCard((await ctx.api("shell/card")).card);
          }
          // A switched-off ability asks to be turned on (turnon.py), with the same card.
          if (step?.tool === "turn_on" && sending) {
            clearTimeout(turnLimit);
            turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
            showShellCard((await ctx.api("turnon/card")).card, "turnon/card");
          }
          // A task in another program may wait on the person's press as well (desk/panel.py).
          if (step?.tool === "desk" && sending) {
            clearTimeout(turnLimit);
            turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
            showDeskCard((await ctx.api("programs/card")).card);
          }
          // One of the person's MCP servers may wait on the person's press too (toolcard.js, mcp_hub.py).
          if (step?.tool === "mcp" && sending) {
            clearTimeout(turnLimit);
            turnLimit = setTimeout(() => stopReply("timeout"), TURN_MS);
            window.paToolCard.show(waiting, (await ctx.api("mcp/card")).card, keepEnd);
          }
          // The sidebar's line under the character's name says it too (app.js).
          if (sending) hooks.doing(line);
        } catch {
          // The waiting row keeps its dots.
        }
      } else if (!sending && !polling) {
        polling = true;
        try {
          const value = await history(seen);
          // A reply that started meanwhile brings these messages itself.
          if (!sending) {
            const was = parts.empty.hidden;
            if (value.chat !== tag) showHistory(await history());
            else reveal(value.messages.filter((item) => item.seq > seen));
            if (was !== parts.empty.hidden || !parts.empty.hidden) renderSetup();
          }
        } catch (error) {
          // The next look tries again; a lasting problem shows when a message is sent.
          if (lost(error)) return;
        } finally {
          polling = false;
        }
      }
      clearTimeout(pollTimer);
      if (asking()) pollTimer = setTimeout(poll, ctx.told && !sending ? TOLD_POLL_MS : POLL_MS);
    }

    async function refreshState() {
      try {
        state = await ctx.api("state");
      } catch {
        // Kept as it was: a failed send says what went wrong.
      }
      bar.refresh();
      // A notice above the composer takes room: the newest message stays in sight.
      const end = atEnd;
      renderText();
      renderSetup();
      if (end) toBottom();
    }

    async function loadHistory() {
      try {
        showHistory(await history());
        loaded = true;
      } catch (error) {
        if (lost(error)) return;
        const note = el("div", "chat-error");
        note.append(el("p", "", say("loadFailed")),
          button("pa-btn pa-btn-secondary pa-btn-sm", say("reload"), async () => {
            note.remove();
            await loadHistory();
            renderSetup();
            poll();
          }));
        parts.messages.replaceChildren(note);
      }
    }

    // ---- Bringing one thing into view, as the pet's card asks
    function flashOn(node) {
      node.scrollIntoView({ block: "center", behavior: motion() });
      atEnd = false;
      // Restart the mark when the same thing is asked for twice in a row.
      node.classList.remove("msg-flash");
      void node.offsetWidth;
      node.classList.add("msg-flash");
      setTimeout(() => node.classList.remove("msg-flash"), 2400);
    }
    const byItem = (item) => [...parts.messages.querySelectorAll(".line[data-item]")].find((n) => n.dataset.item === item);

    renderText();
    renderSetup();
    let showing = null;
    return {
      node: root,
      show() {
        showing = (async () => {
          await refreshState();
          if (!loaded) {
            await loadHistory();
            renderSetup();
          }
          toBottom();
          if (visible()) hooks.seen(id, seen);
          // A new chat waits for words, so its box takes the focus; a thread opens at rest,
          // and the first key typed puts the caret in the box (app.js).
          if (state?.configured && visible() && !parts.empty.hidden) parts.input.focus();
          poll();
        })();
        return showing;
      },
      // The main chat's message carrying library item `item`: the one there, or one the character sends now.
      async bring(item, shown = showing) {
        await shown;
        let node = byItem(item);
        if (!node) {
          let seq;
          try {
            ({ seq } = await ctx.api("chat/bring", { id: item }));
          } catch {
            // Gone from the library: the chat stays as it is.
            return;
          }
          // Shown at once, not typed out: the person asked for it.
          stopReveal();
          try {
            const value = await history(seen);
            if (value.chat !== tag) showHistory(await history());
            else for (const message of value.messages) if (message.seq > seen) showMessage(message);
          } catch {
            return;
          }
          renderSetup();
          node = parts.messages.querySelector(`[data-seq="${seq}"]`) || byItem(item);
        }
        if (node) flashOn(node);
      },
      title(text) {
        title = text;
      },
      // A suggestion or a goto may fill the box from outside.
      focus() {
        if (state?.configured) parts.input.focus();
      },
      // A find drawn as this chat draws one, for the 推荐记录 list (finds.js): its reply goes to this chat.
      findCard: (card) => findCard(card),
      drop() {
        clearTimeout(pollTimer);
        for (const [type, run] of Object.entries(listeners)) document.removeEventListener(type, run);
        plus.drop();
        bar.drop();
        root.remove();
        for (const url of blobs.values()) url.then((value) => URL.revokeObjectURL(value)).catch(() => {});
        for (const item of attached) if (item.url) URL.revokeObjectURL(item.url);
      },
    };
  }

  return { create };
})();
