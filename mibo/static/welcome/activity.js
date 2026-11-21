"use strict";
// What the character did for a reply, as one component for every kind of step. While it works: one quiet live
// line where the typing dots are. When done: one sentence above the reply saying what happened, with the time
// taken, that opens to a timeline (a line a step: its icon, a plain title, the detail that matters, the raw
// key presses and commands behind 详细信息); and under the reply the sources as small site chips, each opening
// its page, with one button for the list of all of them. Steps come as chat.js stores them ({ tool, what, out, cmd, status, ... }, welcome.py `_entry`);
// a kind not known here shows by its own words.
window.paActivity = (() => {
  const SVG = "http://www.w3.org/2000/svg";
  // Lucide-style strokes, one per kind.
  const ICONS = {
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    read: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
    pick: '<path d="M12 3.5l2 5.6 5.9.3-4.6 3.7 1.6 5.7L12 15.5l-4.9 3.3 1.6-5.7-4.6-3.7 5.9-.3z"/>',
    recall: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3.5V8h4.5M12 7.5V12l3 2"/>',
    look: '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    pages: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3.5V8h4.5M12 7.5V12l3 2"/>',
    operate: '<path d="M4.5 4.5l6.4 15.4 2.3-6.7 6.7-2.3z"/>',
    desk: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M3 9h18M6.5 6.5h.01M9.5 6.5h.01"/>',
    shell: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7.5 10 2.5 2-2.5 2M12.5 14.5h4"/>',
    write: '<path d="M12 3v3.5M12 17.5V21M3 12h3.5M17.5 12H21M5.6 5.6l2.5 2.5M15.9 15.9l2.5 2.5M5.6 18.4l2.5-2.5M15.9 8.1l2.5-2.5"/>',
    skill: '<path d="M6 3.5h8.5L19 8v11.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-15a1 1 0 0 1 1-1z"/><path d="M14 3.5V8h5M8.5 12.5h7M8.5 16h4.5"/>',
    mcp: '<path d="M14.7 6.3a4 4 0 0 0-5.2 5.2l-5 5a1.5 1.5 0 0 0 2.1 2.1l5-5a4 4 0 0 0 5.2-5.2l-2.4 2.4-2.1-.4-.4-2.1z"/>',
    chats: '<path d="M14 9a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M18 9h2a2 2 0 0 1 2 2v11l-4-4h-6a2 2 0 0 1-2-2v-1"/>',
    other: '<circle cx="12" cy="12" r="2.5"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
  };
  const svg = (paths, className = "i") => {
    const node = document.createElementNS(SVG, "svg");
    node.setAttribute("viewBox", "0 0 24 24");
    node.setAttribute("aria-hidden", "true");
    node.setAttribute("class", className);
    node.innerHTML = paths;
    return node;
  };
  const el = (tag, className = "", text = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };
  const fill = (text, values) => text.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
  const kindOf = (step) => (ICONS[step?.tool] && step.tool !== "chevron" ? step.tool : "other");
  const OK = new Set(["", "done", "ok", "confirmed"]);
  // "计算器：算 4 加 5" or "Calculator: add 4 and 5" as the program and the task.
  const programOf = (what) => {
    const at = String(what || "").search(/[：:]\s?/);
    return at > 0 ? [what.slice(0, at).trim(), what.slice(at + 1).trim()] : [String(what || ""), ""];
  };
  function clock(seconds, w) {
    const s = Math.max(1, Math.round(seconds));
    return s < 60 ? fill(w.seconds, { s }) : fill(w.minutes, { m: Math.floor(s / 60), s: s % 60 });
  }
  const hostOf = (url) => {
    try {
      return new URL(url).hostname.replace(/^www\./, "");
    } catch {
      return "";
    }
  };

  // ---- While it works
  // The waiting row (chat.js typingRow): its dots give way to the step under way, its icon and words.
  function live(row, step) {
    const label = row?.querySelector(".doing");
    if (!label) return "";
    const w = paI18n.words("activity");
    row.querySelector(".act-live-icon")?.remove();
    // Before any step the reply is being written: the dots themselves. A round after a step is thinking.
    if (!step || (step.tool === "write" && !row.dataset.stepped)) {
      label.textContent = "";
      return "";
    }
    const kind = kindOf(step);
    if (kind !== "write" && kind !== "recall") row.dataset.stepped = "1";
    const what = String(step.what || "");
    let head = w.live[kind] || w.live.other;
    let detail = kind === "recall" || kind === "write" ? "" : what;
    if (kind === "desk") {
      const [program, task] = programOf(what);
      head = fill(head, { program });
      detail = task;
    }
    if (!w.live[kind] && what) [head, detail] = [what, ""];
    label.replaceChildren(el("span", "act-live-head", head), ...(detail ? [el("span", "act-live-what", detail)] : []));
    label.before(svg(ICONS[kind], "i act-live-icon"));
    return detail ? `${head}：${detail}` : head;
  }

  // ---- When done: the sentence, and the timeline it opens to
  function sentence(trace, w) {
    const count = (tool) => trace.filter((step) => step.tool === tool).length;
    const parts = [];
    const searches = count("search"),
      reads = count("read");
    if (searches && reads) parts.push(paI18n.t("activity.searchedRead", { count: reads }));
    else if (searches) parts.push(w.searched);
    else if (reads) parts.push(paI18n.t("activity.readPages", { count: reads }));
    if (count("pick")) parts.push(w.picked);
    if (count("look")) parts.push(w.looked);
    if (count("pages")) parts.push(w.paged);
    if (count("chats")) parts.push(w.chatsSearched);
    for (const step of trace) {
      const status = String(step.status || "");
      if (step.tool === "operate") parts.push(OK.has(status) ? w.operated : w.operateNot);
      else if (step.tool === "desk") {
        const program = programOf(step.what)[0];
        parts.push(fill(status === "declined" ? w.deskDeclined : status === "looked" || status === "unchanged" ? w.deskLooked
          : OK.has(status) ? w.desked : w.deskNot, { program }));
      } else if (step.tool === "shell") {
        // The app's own words for the command ("把「a.txt」移到「图片」"), made a finished sentence.
        const what = String(step.what || "").replace(/^运行了/, "运行");
        parts.push(status === "declined" ? w.shellDeclined : OK.has(status) && what ? fill(w.shelled, { what }) : w.shellNot);
      } else if (step.tool === "skill") parts.push(fill(w.skilled, { name: step.what }));
      else if (step.tool === "mcp") {
        // "server · title": the tool's own name is what the sentence says.
        const name = String(step.what || "").split(" · ").pop();
        parts.push(fill(["declined", "start_declined", "never"].includes(status) ? w.mcpDeclined : OK.has(status) ? w.mcped : w.mcpNot, { name }));
      }
    }
    const known = new Set(["search", "read", "pick", "look", "pages", "chats", "operate", "desk", "shell", "skill", "mcp", "recall", "write"]);
    const others = trace.filter((step) => !known.has(step.tool)).length;
    if (others) parts.push(paI18n.t("activity.otherSteps", { count: others }));
    const unique = [...new Set(parts)];
    if (!paI18n.lang.startsWith("zh"))
      return unique.slice(0, 2).map((part, n) => (n ? part[0].toLowerCase() + part.slice(1) : part)).join(w.listSep) + (unique.length > 2 ? "…" : "");
    // Later parts join the first as one sentence: "已搜索网页，并在计算器中完成操作".
    const also = paI18n.lang === "zh-TW" ? "並" : "并";
    return unique.slice(0, 2).map((part, n) => (n && part.startsWith("已") ? `${also}${part.slice(1)}` : part)).join("，") + (unique.length > 2 ? "等" : "");
  }

  // A site's icon, as linkcard.js keeps it; a letter in a square until one comes, or when there is none.
  function siteMark(host, opts, className = "act-fav") {
    const box = el("span", className);
    box.textContent = (host.replace(/^www\./, "")[0] || "·").toUpperCase();
    window.paLinks?.icon(host, opts).then((url) => {
      if (!url) return;
      const img = el("img");
      img.alt = "";
      img.src = url;
      box.textContent = "";
      box.classList.add("has-img");
      box.append(img);
    });
    return box;
  }
  function link(url, className, opts) {
    const node = opts.link ? opts.link(url, className) : el("a", className);
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    node.textContent = "";
    return node;
  }
  // Folded under one step: the raw key presses, the command and what it printed.
  function more(w, blocks) {
    const box = el("details", "act-more");
    const head = el("summary", "act-more-head", w.more);
    box.append(head);
    for (const [label, text, mono] of blocks) {
      if (!text) continue;
      if (label) box.append(el("div", "act-more-label", label));
      box.append(el(mono ? "pre" : "div", mono ? "act-more-pre" : "act-more-text", text));
    }
    return box;
  }

  function stepNode(step, sources, used, w, opts, said) {
    const kind = kindOf(step);
    const status = String(step.status || "");
    const item = el("li", `act-step act-${kind}`);
    const mark = el("span", "act-mark");
    mark.append(svg(ICONS[kind]));
    const body = el("div", "act-body");
    const head = el("div", "act-title");
    const what = String(step.what || "");
    let title = w.title[kind] || what || step.tool || w.title.other;
    let detail = kind === "other" || !w.title[kind] ? "" : what;
    if (kind === "desk") [title, detail] = programOf(what);
    head.append(el("span", "", title));
    if (["operate", "desk", "shell", "mcp"].includes(kind) && !OK.has(status))
      head.append(el("span", "act-status", w.status[status] || w.status.other));
    body.append(head);
    if (kind === "read") {
      // The page itself, as the sources list keeps it: its icon, title and host.
      const source = sources.find((s, n) => !used.has(n) && (s.site === what || hostOf(s.url) === what));
      if (source) {
        used.add(sources.indexOf(source));
        const page = link(source.url, "act-page", opts);
        page.append(siteMark(hostOf(source.url), opts), el("span", "act-page-title", source.title || source.site),
          el("span", "act-page-host", hostOf(source.url)));
        body.append(page);
        detail = "";
      }
    }
    // Not said twice: a detail the sentence above already says.
    if (detail && !said.includes(detail)) body.append(el("div", "act-detail", detail));
    const lines = String(step.out || "").split("\n").filter((line) => line.trim());
    if (kind === "shell" && (step.cmd || step.out))
      body.append(more(w, [[w.command, step.cmd, true], [w.result, step.out, true]]));
    else if (kind === "mcp" && (step.cmd || step.out)) body.append(more(w, [[w.args, step.cmd, true], [w.result, step.out, true]]));
    else if ((kind === "desk" || kind === "operate") && lines.length) body.append(more(w, [["", lines.join("\n"), false]]));
    else if (kind === "chats")
      // The chats the lines came from, a line each ("id\tday\ttitle", pastchats.py), each opening that chat.
      for (const line of lines) {
        const [id, day, name] = line.split("\t");
        const chat = el("a", "act-page");
        chat.href = "#";
        chat.addEventListener("click", (event) => {
          event.preventDefault();
          opts.go?.(`chat/${id}`);
        });
        const date = new Date(`${day}T12:00:00`);
        chat.append(el("span", "act-page-title", name || (id === "main" ? w.chatMain : w.chatUntitled)),
          el("span", "act-page-host", isNaN(date) ? "" : new Intl.DateTimeFormat(paI18n.lang, { month: "short", day: "numeric" }).format(date)));
        body.append(chat);
      }
    item.append(mark, body);
    return item;
  }

  // Above the reply: null when it took no steps worth telling. The turn's changes to files are drawn under it.
  function summary(item, opts = {}) {
    const trace = (item.trace || []).filter((step) => step && step.tool && !["write", "recall", "changes"].includes(step.tool));
    if (!trace.length) return null;
    const w = paI18n.words("activity");
    const node = el("details", "act");
    const head = el("summary", "act-head");
    const said = sentence(trace, w);
    head.append(el("span", "act-sentence", said));
    if (item.took > 0) head.append(el("span", "act-took", fill(w.took, { t: clock(item.took, w) })));
    head.append(svg(ICONS.chevron, "i act-chevron"));
    const list = el("ol", "act-steps");
    const used = new Set();
    for (const step of trace) list.append(stepNode(step, item.sources || [], used, w, opts, said));
    node.append(head, list);
    // A card nobody answered in time ended its step (cardwait.py): 继续 under the latest reply asks again.
    if (!opts.goOn || !trace.some((step) => step.status === "unanswered")) return node;
    const row = el("div", "act-go-on");
    const go = el("button", "pa-btn pa-btn-secondary pa-btn-sm", w.goOn);
    go.type = "button";
    go.addEventListener("click", () => {
      go.disabled = true;
      opts.goOn(w.goOnSaid);
    });
    row.append(go);
    const both = document.createDocumentFragment();
    both.append(node, row);
    return both;
  }

  // Under the reply: the sites it drew on, a chip each that opens the site's first page in the app's browser
  // (opts.link, as every link in the chat does); when the pages are more than the chips, one button at the end
  // of the row opens the list of them all, with their titles.
  let lists = 0;
  function sources(list, opts = {}) {
    if (!list?.length) return null;
    const node = el("div", "act-src");
    const row = el("div", "act-src-row");
    const firsts = new Map();
    for (const source of list) {
      const host = hostOf(source.url);
      if (host && !firsts.has(host)) firsts.set(host, source);
    }
    const chips = [...firsts].slice(0, 4);
    for (const [host, source] of chips) {
      const chip = link(source.url, "act-chip", opts);
      const name = paI18n.t("activity.sourceOpen", { host, title: source.title || source.site || source.url });
      chip.setAttribute("aria-label", name);
      chip.title = name;
      chip.append(siteMark(host, opts), el("span", "act-chip-host", host));
      row.append(chip);
    }
    node.append(row);
    if (list.length <= chips.length) return node;
    const rows = el("ol", "act-src-list");
    rows.id = `act-src-list-${++lists}`;
    rows.hidden = true;
    for (const source of list) {
      const item = el("li");
      const page = link(source.url, "act-page", opts);
      page.append(siteMark(hostOf(source.url), opts), el("span", "act-page-title", source.title || source.site || source.url),
        el("span", "act-page-host", hostOf(source.url)));
      item.append(page);
      rows.append(item);
    }
    const all = el("button", "act-chip act-chip-more", paI18n.t("activity.sources", { count: list.length }));
    all.type = "button";
    all.setAttribute("aria-expanded", "false");
    all.setAttribute("aria-controls", rows.id);
    all.addEventListener("click", () => {
      rows.hidden = !rows.hidden;
      all.setAttribute("aria-expanded", String(!rows.hidden));
    });
    row.append(all);
    node.append(rows);
    return node;
  }

  // An edit's unified difference (shell/files.py diff): each line as it is, indentation kept, added and removed
  // lines marked; the @@ lines say where. Drawn on an edit's card and in the fold below.
  function diff(text) {
    const box = el("pre", "act-diff");
    for (const line of String(text || "").split("\n")) {
      const kind = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
      box.append(el("span", kind ? `act-diff-${kind}` : "", line || " "));
    }
    return box;
  }

  // A press that changes files: from the app's own page with its window key, as a card's press is (chat.js).
  const press = (path, body) =>
    fetch(`/api/${path}`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json",
        "X-Mibo-Setup-Token": sessionStorage.getItem("mibo-setup-token") || "",
        "X-Mibo-Window": sessionStorage.getItem("mibo-frame") || "" },
      body: JSON.stringify(body),
    }).then((r) => r.json());

  // Under the reply: the files the turn made, changed, moved or moved to the Recycle Bin (shell/checkpoint.py), each
  // shown in its folder by a click, an edit with its difference, and 撤销这次的改动 putting the whole turn back.
  function changes(item, opts = {}) {
    const turn = (item.trace || []).find((step) => step?.tool === "changes")?.what;
    if (!turn || !opts.api) return null;
    const w = paI18n.words("activity");
    const node = el("details", "act-chg");
    const head = el("summary", "act-chg-head");
    const count = el("span", "act-chg-count");
    head.append(svg(ICONS.read, "i act-chg-icon"), el("span", "", w.changes), count, svg(ICONS.chevron, "i act-chevron"));
    const list = el("ol", "act-chg-list");
    const foot = el("div", "act-chg-foot");
    node.append(head, list, foot);
    // Shown once the list has come.
    node.hidden = true;
    const draw = (view, done) => {
      node.hidden = false;
      count.textContent = paI18n.t("activity.changesCount", { count: view.items.length });
      list.replaceChildren();
      view.items.forEach((row, n) => {
        const li = el("li", "act-chg-row");
        const line = el("div", "act-chg-line");
        const place = row.dest || row.path;
        const name = el(row.op === "deleted" ? "span" : "button", "act-chg-name", place.split(/[\\/]/).pop());
        if (row.op !== "deleted") {
          name.type = "button";
          name.title = w.reveal;
          name.addEventListener("click", () => press("shell/reveal", { turn, n }).catch(() => {}));
        }
        line.append(el("span", `act-chg-op act-chg-${row.op}`, w.changeOp[row.op] || row.op), name,
          el("span", "act-chg-where", place.split(/[\\/]/).slice(-2, -1)[0] || ""));
        if (row.diff) line.append(el("span", "act-chg-lines", `+${row.added} −${row.removed}`));
        li.append(line);
        if (row.diff) {
          const fold = el("details", "act-more");
          fold.append(el("summary", "act-more-head", w.showDiff), diff(row.diff));
          li.append(fold);
        }
        list.append(li);
      });
      foot.replaceChildren();
      if (view.undone) {
        foot.append(el("p", "act-chg-told", w.undone));
        if (done?.left?.length) foot.append(el("p", "act-chg-told", paI18n.t("activity.undoLeft", { count: done.left.length })));
      } else if (view.kept) {
        const undo = el("button", "pa-btn pa-btn-secondary pa-btn-sm", w.undoTurn);
        undo.type = "button";
        undo.addEventListener("click", async () => {
          undo.disabled = true;
          const after = await press("shell/undo", { turn }).catch(() => null);
          if (after?.items) draw(after, after);
          else undo.disabled = false;
        });
        foot.append(undo);
      }
      opts.grew?.();
    };
    opts.api(`shell/changes?turn=${encodeURIComponent(turn)}`).then((view) => view?.items?.length && draw(view)).catch(() => {});
    return node;
  }

  // ---- Memory where it acts (person.py): what a reply drew on, and what the background memory kept.
  const MEMORY = '<path d="M9 4.5a3 3 0 0 0-3 3 3 3 0 0 0-1.5 5.3A3 3 0 0 0 9 17.5a2.5 2.5 0 0 0 3 2V5.5a2.5 2.5 0 0 0-3-1zM15 4.5a3 3 0 0 1 3 3 3 3 0 0 1 1.5 5.3 3 3 0 0 1-4.5 4.7 2.5 2.5 0 0 1-3 2"/>';
  const toMemory = (opts, label = paI18n.words("activity").memManage) => {
    const go = el("button", "act-mem-manage", label);
    go.type = "button";
    go.addEventListener("click", () => opts.go?.("setup/memory"));
    return go;
  };

  // Under a reply: the memories it drew on, folded; open, each with 编辑 and 忘记, as ChatGPT's sources list them.
  function recalled(item, opts = {}) {
    const used = item.memory?.used;
    if (!used?.length || !opts.api) return null;
    const m = paI18n.words("setup_memory");
    const node = el("details", "act-mem");
    const head = el("summary", "act-mem-head");
    head.append(svg(MEMORY, "i act-mem-icon"), el("span", "", paI18n.t("activity.memUsed", { count: used.length })),
      svg(ICONS.chevron, "i act-chevron"));
    const list = el("ol", "act-mem-list");
    for (const memory of used) {
      const row = el("li", "act-mem-row");
      const text = el("span", "act-mem-text", memory.text);
      const tools = el("span", "act-mem-tools");
      const tool = (label, run) => {
        const press = el("button", "act-mem-tool", label);
        press.type = "button";
        press.addEventListener("click", run);
        return press;
      };
      // What the page said back: the item is gone, or what was asked of it is done.
      const told = (words) => {
        text.classList.add("act-mem-gone");
        tools.replaceChildren(el("span", "act-mem-told", words));
      };
      const write = (action, body) =>
        opts.api(`person/${action}`, body).then(() => true, (failure) => (told(failure?.message === "activity_changed"
          ? paI18n.words("activity").memGone : m.errors?.[failure?.message] || m.generic || ""), false));
      const rest = () => tools.replaceChildren(
        tool(m.edit, () => {
          const field = el("input", "act-mem-field");
          field.value = text.textContent;
          field.maxLength = 60;
          field.setAttribute("aria-label", m.edit);
          const save = async () => {
            const words = field.value.trim();
            if (!words || words === memory.text) return back();
            if (await write("edit", { id: memory.id, text: words })) {
              memory.text = words;
              back();
            }
          };
          const back = () => {
            field.replaceWith(text);
            text.textContent = memory.text;
            rest();
          };
          field.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.isComposing) save();
            if (event.key === "Escape") back();
          });
          text.replaceWith(field);
          tools.replaceChildren(tool(m.save, save), tool(m.cancel, back));
          field.focus();
        }),
        tool(m.forget, async () => {
          if (!(await opts.confirm?.({ title: m.forgetAsk, text: m.forgetText, ok: m.forget }))) return;
          if (await write("forget", { id: memory.id })) told(paI18n.words("activity").memForgotten);
        }),
      );
      rest();
      row.append(text, tools);
      list.append(row);
    }
    const foot = el("div", "act-mem-foot");
    foot.append(toMemory(opts, paI18n.words("chat").manageMemory));
    node.append(head, list, foot);
    node.addEventListener("toggle", () => opts.grew?.());
    return node;
  }

  // A quiet line in the chat a memory came from, once the background memory kept it: 已记住：… · 管理.
  function saved(item, opts = {}) {
    const w = paI18n.words("activity");
    const node = el("p", "act-saved");
    const words = (item.saved || []).map((row) => row.text).join(w.memSep);
    // The words, the dot and 管理 run on as one line of text, so a narrow chat wraps them as a sentence.
    const text = el("span", "act-saved-text", fill(w.memSaved, { what: words }));
    text.append(el("span", "act-saved-dot", " · "), toMemory(opts));
    node.append(svg(MEMORY, "i act-mem-icon"), text);
    return node;
  }

  return { live, summary, sources, changes, diff, recalled, saved };
})();
