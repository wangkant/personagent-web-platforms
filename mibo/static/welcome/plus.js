"use strict";
// The composer's "+": a menu, opening upward, of what can go with a message (pictures and files, a
// screenshot, the page open in the app's browser or one read there lately, the clipboard), what the
// character may do for this message (search the web, operate the browser, operate the computer), and
// what to ask of it now (a find, a subject to look up on a walk now, what it remembers, a 无痕对话). An entry shows only
// when it can work (GET /api/compose, panels/compose.py). What is chosen for the message rides as a chip
// beside the "+", sent with it; the chat (chat.js) makes one of these per chat view.
window.paPlus = (() => {
  // Clipboard text up to this long goes into the box as a quote; longer, it is attached as a text file.
  const QUOTE_MAX = 600;
  // Facts asked when the menu opens: drawn from the last ones if the answer is slower than this.
  const FACTS_WAIT_MS = 300;
  const SVG = "http://www.w3.org/2000/svg";
  const I = {
    plus: '<path d="M12 5v14M5 12h14"/>',
    clip: '<path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    shot: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><rect x="7" y="8" width="10" height="8" rx="1"/>',
    page: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M2 9h20M6 6.5h.01M9 6.5h.01"/>',
    recent: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
    board: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
    globe: '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
    pointer: '<path d="M14 4.1 12 6M5.1 8l-2.9-.8M6 12l-1.9 2M7.2 2.2 8 5.1"/><path d="M9.04 9.69a.5.5 0 0 1 .65-.65l11 4.5a.5.5 0 0 1-.07.95l-4.35 1.04a1 1 0 0 0-.74.74l-1.04 4.35a.5.5 0 0 1-.95.07z"/>',
    monitor: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    spark: '<path d="M9.94 15.5a2 2 0 0 0-1.44-1.44l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0l1.58 6.13a2 2 0 0 0 1.44 1.44l6.13 1.58a.5.5 0 0 1 0 .96l-6.13 1.58a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0z"/>',
    compass: '<circle cx="12" cy="12" r="10"/><path d="m16.24 7.76-2.12 6.36-6.36 2.12 2.12-6.36z"/>',
    memory: '<path d="M9 4.5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.3A3 3 0 0 0 9 17.5a2.5 2.5 0 0 0 3 2V5.5a2.5 2.5 0 0 0-3-1zM15 4.5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.3 3 3 0 0 1-4.5 4.7 2.5 2.5 0 0 1-3 2"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    right: '<path d="m9 18 6-6-6-6"/>',
    left: '<path d="m15 18-6-6 6-6"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    // A dashed bubble: the 无痕对话.
    ghost: '<path d="M10.1 2.18a10 10 0 0 1 3.8 0M13.9 21.82a10 10 0 0 1-3.8 0M17.6 3.72a10 10 0 0 1 2.7 2.7M2.18 13.9a10 10 0 0 1 0-3.8M20.28 17.6a10 10 0 0 1-2.7 2.7M21.82 10.1a10 10 0 0 1 0 3.8M3.72 6.4a10 10 0 0 1 2.7-2.7"/><path d="m6.8 20.79-3.9.9a.5.5 0 0 1-.6-.6l.9-3.9"/>',
  };
  // What each choice for a message is called on its chip, and its icon.
  const CHOICES = { search_off: ["searchOff", "globe"], browser: ["browser", "pointer"], computer: ["computer", "monitor"],
    schedule: ["schedule", "clock"], page: ["browser", "pointer"], page_on: ["browser", "pointer"] };

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
  const hostOf = (url) => {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  };
  const fileOf = (base64, name, type) => {
    const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    return new File([bytes], name, { type });
  };

  // A page attached or sent: its title and site, as a chip like a file's.
  function pageChip(item, tag = "div") {
    const chip = el(tag, "att att-file att-page");
    const icon = el("span", "att-icon");
    icon.append(svg(I.page));
    const words = el("span", "att-words");
    words.append(el("span", "att-name", item.title || hostOf(item.url)), el("span", "att-size", hostOf(item.url)));
    chip.append(icon, words);
    chip.title = item.url;
    return chip;
  }

  function create(env) {
    const { ctx } = env;
    const words = () => paI18n.words("plus");
    const say = (key) => words()[key];
    // What is chosen for the next message, and whether the box holds a subject for a walk instead.
    const chosen = new Set();
    let wondering = false;
    let facts = {};
    let open = false;
    let asking = null;

    const wrap = el("div", "plus-wrap");
    const button = el("button", "chat-attach");
    button.type = "button";
    button.append(svg(I.plus));
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    const menu = el("div", "side-menu plus-menu");
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    wrap.append(button, menu);
    const chips = el("div", "plus-chips");

    // ---- What can work now
    // As the menu opens, the screenshot is got ready too (it takes seconds to start).
    async function readFacts(opening = false) {
      try {
        facts = await ctx.api(opening ? "compose?warm=1" : "compose");
      } catch {
        // Not answering: the menu offers what needs nothing from the app.
      }
      return facts;
    }
    readFacts();

    function entry(icon, label, run, { hint = "", checked = null, more = false } = {}) {
      const node = el("button", "side-menu-item plus-item");
      node.type = "button";
      node.setAttribute("role", checked === null ? "menuitem" : "menuitemcheckbox");
      if (checked !== null) node.setAttribute("aria-checked", String(checked));
      const text = el("span", "plus-text");
      text.append(el("span", "plus-label", label));
      if (hint) text.append(el("span", "plus-hint", hint));
      node.append(svg(icon), text);
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
    const heading = (text) => el("div", "plus-head", text);

    function groups() {
      const configured = env.configured();
      const add = [];
      if (configured) {
        add.push(entry(I.clip, say("files"), () => (close(), env.pick())));
        if (facts.capture) add.push(entry(I.shot, say("shot"), () => (close(), shoot()), { hint: say("shotHint") }));
        if (facts.page)
          add.push(entry(I.page, say("page"), () => (close(), env.attachItem({ kind: "page", ...facts.page })),
            { hint: facts.page.title || hostOf(facts.page.url) }));
        if (facts.recent) add.push(entry(I.recent, say("recent"), showRecent, { more: true }));
        if (facts.clipboard)
          add.push(entry(I.board, say("clip"), () => (close(), paste()),
            { hint: say(facts.clipboard === "text" ? "clipText" : "clipPicture") }));
      }
      const message = configured
        ? [
            entry(I.globe, say("search"), () => toggle("search_off"), { checked: !chosen.has("search_off") }),
            ...(facts.browser ? [entry(I.pointer, say("browser"), () => toggle("browser"), { checked: chosen.has("browser") })] : []),
            ...(facts.computer
              ? [entry(I.monitor, say("computer"), () => toggle("computer"), { checked: chosen.has("computer"), hint: say("computerHint") })]
              : []),
            // Made in the main chat, where its results arrive (panels/schedule.py).
            ...(env.main
              ? [entry(I.clock, say("schedule"), () => toggle("schedule"), { checked: chosen.has("schedule"), hint: say("scheduleHint") })]
              : []),
          ]
        : [];
      const ask = [];
      if (configured && env.main) ask.push(entry(I.spark, say("find"), () => (close(), bringFind())));
      // Its result comes into the main chat, so it is asked there.
      if (configured && env.main && facts.walks)
        ask.push(entry(I.compass, say("wonder"), () => (close(), startWonder()), { hint: say("wonderHint") }));
      // A walk cannot leave: the entry says why and opens Settings › 外出.
      else if (configured && env.main && say("walkWhy")[facts.walkWhy])
        ask.push(entry(I.compass, say("wonder"), () => (close(), ctx.go("setup/walks")), { hint: say("walkWhy")[facts.walkWhy] }));
      ask.push(entry(I.memory, say("memory"), () => (close(), ctx.go("setup/memory"))));
      // A chat that neither reads nor keeps memory; ticked in one, it leaves for a plain new chat (chat.js).
      if (configured)
        ask.push(entry(I.ghost, say("private"), () => (close(), env.setPrivate(!env.private())),
          { checked: env.private(), hint: say("privateHint") }));
      return [
        [say("add"), add],
        [say("message"), message],
        [env.name(), ask],
      ].filter(([, items]) => items.length);
    }

    function draw(sections) {
      menu.replaceChildren();
      sections.forEach(([title, items], index) => {
        if (index) menu.append(el("div", "plus-rule"));
        if (title) menu.append(heading(title));
        menu.append(...items);
      });
      place();
    }
    // Upward from the "+", its left edge on the button's. Where the chat leaves too little room above
    // (the empty start, the box in the middle) it stands beside the button and slides down until it
    // fits, the way a context menu does; only a window shorter than the menu makes it scroll.
    function place() {
      const view = (wrap.closest(".chat") || document.body).getBoundingClientRect();
      const at = button.getBoundingClientRect();
      menu.style.maxHeight = menu.style.bottom = menu.style.left = "";
      const tall = menu.scrollHeight;
      if (tall <= at.top - view.top - 20) return;
      const room = view.height - 24;
      const bottom = Math.min(view.bottom - 12, view.top + 12 + Math.min(tall, room));
      menu.style.maxHeight = `${room}px`;
      menu.style.bottom = `${wrap.getBoundingClientRect().bottom - bottom}px`;
      menu.style.left = `${at.width + 8}px`;
    }
    const items = () => [...menu.querySelectorAll(".plus-item")];

    async function show(fromKeys = false) {
      if (open) return;
      open = true;
      button.setAttribute("aria-expanded", "true");
      wrap.classList.add("open");
      await Promise.race([readFacts(true), new Promise((done) => setTimeout(done, FACTS_WAIT_MS))]);
      if (!open) return;
      menu.hidden = false;
      draw(groups());
      if (fromKeys) items()[0]?.focus();
    }
    function close(focusBack = false) {
      if (!open) return;
      open = false;
      menu.hidden = true;
      wrap.classList.remove("open");
      button.setAttribute("aria-expanded", "false");
      if (focusBack) button.focus();
    }

    async function showRecent() {
      let pages = [];
      try {
        pages = (await ctx.api("compose/recent")).pages || [];
      } catch {
        // None to offer: the list says so by being empty.
      }
      const back = entry(I.left, say("back"), () => draw(groups()));
      back.classList.add("plus-back");
      const rows = pages.map((page) =>
        entry(I.page, page.title || page.host, () => (close(), env.attachItem({ kind: "page", url: page.url, title: page.title })),
          { hint: page.host }),
      );
      draw([["", [back]], [say("recent"), rows]]);
      items()[1]?.focus();
    }

    // Opened from the keyboard (a click with no pointer), the first entry takes the focus.
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
      if (step) {
        event.preventDefault();
        list[(at + step + list.length) % list.length]?.focus();
      } else if (event.key === "Home" || event.key === "End") {
        event.preventDefault();
        list[event.key === "Home" ? 0 : list.length - 1]?.focus();
      } else if (event.key === "Tab") close();
    });
    const outside = (event) => {
      if (open && !wrap.contains(event.target)) close();
    };
    const keys = (event) => {
      if (event.key === "Escape" && open) {
        event.stopPropagation();
        close(true);
      }
    };
    const resized = () => close();
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", keys, true);
    addEventListener("resize", resized);

    // ---- The entries
    async function shoot() {
      try {
        const value = await ctx.api("compose/capture", { lang: ctx.lang });
        if (value.picture) env.attach([fileOf(value.picture, say("shotName"), "image/png")]);
        else env.input.focus();
      } catch {
        env.refuse(say("shotFailed"));
      }
    }
    async function paste() {
      try {
        const value = await ctx.api("compose/clipboard");
        if (value.kind === "picture") env.attach([fileOf(value.picture, say("clipPictureName"), "image/png")]);
        else if (value.kind === "text" && value.text.trim()) {
          if (value.text.length > QUOTE_MAX) env.attach([new File([value.text], say("clipName"), { type: "text/plain" })]);
          else env.quote(value.text);
        } else env.refuse(say("clipEmpty"));
      } catch {
        env.refuse(say("failed"));
      }
    }
    async function bringFind() {
      try {
        const value = await ctx.api("compose/recommend", {});
        if (!value.count) env.flash(say("noFind"));
        env.refresh();
      } catch {
        env.flash(say("failed"));
      }
    }
    function toggle(choice) {
      close();
      if (chosen.has(choice)) chosen.delete(choice);
      else chosen.add(choice);
      paint();
      env.input.focus();
    }
    function startWonder() {
      wondering = true;
      paint();
      env.input.focus();
    }

    // ---- The chips beside the "+": one row, each with its cross
    function chip(icon, label, remove) {
      const node = el("span", "plus-chip");
      node.append(svg(icon), el("span", "plus-chip-text", label));
      const x = el("button", "plus-chip-x");
      x.type = "button";
      x.append(svg(I.x));
      x.setAttribute("aria-label", say("remove").replace("{what}", label));
      x.title = x.getAttribute("aria-label");
      x.addEventListener("click", () => {
        remove();
        paint();
        env.input.focus();
      });
      node.append(x);
      return node;
    }
    function paint() {
      const list = [];
      if (wondering) list.push(chip(I.compass, say("wonder"), () => (wondering = false)));
      for (const [choice, [key, icon]] of Object.entries(CHOICES))
        if (chosen.has(choice)) list.push(chip(I[icon], say(key), () => chosen.delete(choice)));
      chips.replaceChildren(...list);
      chips.hidden = !list.length;
      env.placeholder(wondering ? say("wonderPlaceholder") : chosen.has("schedule") ? say("schedulePlaceholder") : "");
    }

    return {
      button: wrap,
      chips,
      render() {
        button.setAttribute("aria-label", say("open"));
        button.title = say("open");
        paint();
        if (open) draw(groups());
      },
      // The choices for the message being sent; the chips go with it.
      take() {
        const list = [...chosen];
        chosen.clear();
        paint();
        return list;
      },
      // The box held a subject to look up: sent out on a walk instead of as a message. True when it was.
      wonder(text) {
        if (!wondering || !text.trim()) return false;
        wondering = false;
        paint();
        ctx
          .api("compose/wonder", { words: text })
          .then((value) => {
            const said = value.held === "walks_off" ? "plus.wonderOff" : value.later ? "plus.wonderQueued" : "plus.wonderGone";
            env.flash(paI18n.t(said, { what: value.words }));
          })
          .catch(() => env.flash(say("failed")));
        return true;
      },
      // A sent message's choices, quietly under it.
      sent(list) {
        const row = el("div", "me-choices");
        for (const choice of list) {
          const [key, icon] = CHOICES[choice] || [];
          if (!key) continue;
          const tag = el("span", "me-choice");
          tag.append(svg(I[icon]), el("span", "", say(key)));
          row.append(tag);
        }
        return row;
      },
      drop() {
        document.removeEventListener("pointerdown", outside, true);
        document.removeEventListener("keydown", keys, true);
        removeEventListener("resize", resized);
      },
    };
  }

  return { create, pageChip };
})();
