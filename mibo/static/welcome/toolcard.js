"use strict";
// The card in the chat before one of the person's MCP servers is used (mcp_hub.py): starting a local program the
// first time, named by the program it runs with its exact command under a fold, or calling a tool that writes or
// deletes, with its arguments in plain words and the raw JSON under a fold; and what a server asks of the person
// during a call, a form they fill in (sent to the server alone) or a page
// they choose to open. Only a press here answers it (POST /api/mcp/card), never words typed in the chat or text from
// a server.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const ICON = {
    start: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="m7 10 3 2-3 2"/><path d="M13 15h4"/>',
    tool: '<path d="M14.7 6.3a4 4 0 0 0-5.2 5.2l-5 5a1.5 1.5 0 0 0 2.1 2.1l5-5a4 4 0 0 0 5.2-5.2l-2.4 2.4-2.1-.4-.4-2.1z"/>',
    ask: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    link: '<path d="M14 4h6v6"/><path d="M10 14 20 4"/><path d="M19 13v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h6"/>',
  };
  let shown = null;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fill(text, values) {
    return text.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
  }
  function head(kind, title, meta) {
    const node = el("div", "op-card-head");
    const tile = el("span", "op-card-icon");
    const mark = document.createElementNS(SVG, "svg");
    mark.setAttribute("viewBox", "0 0 24 24");
    mark.setAttribute("aria-hidden", "true");
    mark.setAttribute("class", "i");
    mark.innerHTML = ICON[kind];
    tile.append(mark);
    const words = el("div", "op-card-words");
    words.append(el("p", "op-card-title", title), el("p", "op-card-meta", meta));
    node.append(tile, words);
    return node;
  }

  // A server's form (mcp_tools.ask_fields): one labelled input per field; returns the node and a reader of the
  // values that marks a required field left empty and gives null then.
  function form(fields, t) {
    const node = el("div", "op-card-form");
    const reads = [];
    for (const field of fields) {
      const row = el("label", "op-card-field");
      const name = el("span", "op-card-field-name", field.title);
      if (field.required) name.append(el("span", "op-card-meta", ` · ${t.required}`));
      row.append(name);
      let input;
      if (field.kind === "choice") {
        input = el("select", "pa-select");
        if (!field.required) input.append(el("option", "", ""));
        for (const option of field.options) {
          const item = el("option", "", option.label);
          item.value = option.value;
          input.append(item);
        }
        input.value = field.default ?? (field.required ? field.options[0]?.value : "");
        reads.push(() => [field, input, input.value]);
      } else if (field.kind === "choices") {
        input = el("span", "op-card-field-choices");
        const boxes = field.options.map((option) => {
          const box = el("input", "pa-check");
          box.type = "checkbox";
          box.value = option.value;
          box.checked = (field.default || []).includes(option.value);
          const one = el("label", "op-card-field-choice");
          one.append(box, el("span", "", option.label));
          input.append(one);
          return box;
        });
        reads.push(() => [field, input, boxes.filter((b) => b.checked).map((b) => b.value)]);
      } else if (field.kind === "boolean") {
        input = el("input", "pa-check");
        input.type = "checkbox";
        input.checked = field.default === true;
        row.classList.add("op-card-field-check");
        reads.push(() => [field, input, input.checked]);
      } else {
        input = el("input", "pa-input");
        const numeric = field.kind === "number" || field.kind === "integer";
        input.type = numeric ? "number"
          : { email: "email", uri: "url", date: "date", "date-time": "datetime-local" }[field.format] || "text";
        if (numeric) {
          if (field.minimum !== undefined) input.min = field.minimum;
          if (field.maximum !== undefined) input.max = field.maximum;
          input.step = field.kind === "integer" ? "1" : "any";
        }
        if (field.default !== undefined) input.value = field.default;
        reads.push(() => [field, input,
          input.value.trim() === "" ? "" : numeric ? Number(input.value) : input.value.trim()]);
      }
      row.append(input);
      if (field.description) row.append(el("span", "op-card-meta", field.description));
      node.append(row);
    }
    const values = () => {
      const out = {};
      let whole = true;
      for (const read of reads) {
        const [field, input, value] = read();
        const empty = value === "" || (Array.isArray(value) && !value.length);
        input.setAttribute("aria-invalid", String(field.required && empty));
        if (field.required && empty) whole = false;
        if (!empty) out[field.name] = value;
      }
      return whole ? out : null;
    };
    return [node, values];
  }

  // `card` from GET /api/mcp/card shown under the waiting row; null takes an unanswered one away.
  function show(waiting, card, keepEnd = () => {}) {
    const t = paI18n.words("toolcard");
    if (shown && (!card || shown.dataset.id !== card.id)) {
      if (!shown.classList.contains("answered")) shown.remove();
      shown = null;
    }
    if (!card || shown || !waiting) return;
    const start = card.type === "mcp_start";
    const asked = card.type === "mcp_ask";
    const link = asked && card.mode === "url";
    let values = null;
    const node = el("div", "op-card mcp-card");
    node.dataset.id = card.id;
    const facts = el("dl", "op-card-facts");
    const fact = (name, value, cls = "") => facts.append(el("dt", "", name), el("dd", cls, value));
    if (start) {
      // The server and the program it runs in one line; the exact command, folder and values under the fold.
      node.append(head("start", fill(t.startTitle, card), `${t.startMeta} · ${card.program}`));
      fact(t.command, card.command, "op-card-text");
      fact(t.folder, card.cwd);
      fact(t.env, card.env.length ? card.env.join(", ") : t.none);
      const fold = el("details", "shell-card-command");
      fold.append(el("summary", "", t.startDetails), facts);
      node.append(el("p", "op-card-meta", t.startNote), fold);
    } else if (asked) {
      node.append(head(link ? "link" : "ask", fill(link ? t.linkTitle : t.askTitle, card), fill(t.askMeta, card)));
      if (card.message) node.append(el("p", "op-card-quote", card.message));
      if (link) {
        fact(t.address, card.url, "op-card-text");
        node.append(facts, el("p", "op-card-meta", t.linkNote));
      } else {
        const [fields, read] = form(card.fields, t);
        values = read;
        node.append(fields, el("p", "op-card-meta", fill(t.askNote, card)));
      }
    } else {
      node.append(head("tool", fill(t.toolTitle, { tool: card.title || card.tool }), fill(t.toolMeta, card)
        + (card.title && card.title !== card.tool ? ` · ${card.tool}` : "")));
      // Why the automatic review did not pass it, in one line (mcp_hub.py).
      if (card.review) node.append(el("p", "op-card-why", card.review));
      if (card.changed) node.append(el("p", "op-card-never", t.changed));
      if (card.destructive) node.append(el("p", "op-card-never", t.destructive));
      for (const [name, value] of card.args) fact(name, value, "op-card-text");
      if (!card.args.length) node.append(el("p", "op-card-meta", t.noArgs));
      else node.append(facts);
      const fold = el("details", "shell-card-command");
      fold.append(el("summary", "", t.raw), el("pre", "", card.raw));
      node.append(fold);
    }
    const answer = async (value) => {
      const filled = values && value === "once" ? values() : undefined;
      if (filled === null) return;
      for (const b of node.querySelectorAll("button, input")) b.disabled = true;
      node.classList.add("answered");
      const kind = start ? "start" : link ? "link" : asked ? "ask" : "tool";
      const told = el("p", "op-card-told", t[kind + { once: "Once", chat: "Chat", always: "Always", no: "No" }[value]]);
      node.querySelector(".op-card-told")?.remove();
      node.append(told);
      try {
        const response = await fetch("/api/mcp/card", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json",
            "X-Mibo-Setup-Token": sessionStorage.getItem("mibo-setup-token") || "",
            "X-Mibo-Window": sessionStorage.getItem("mibo-frame") || "" },
          body: JSON.stringify({ id: card.id, answer: value, values: filled }),
        });
        const said = await response.json().catch(() => ({}));
        if (said.problem) {
          // The server's form wants something else: the card stays up to be corrected.
          told.textContent = t.fix;
          node.classList.remove("answered");
          for (const b of node.querySelectorAll("button, input")) b.disabled = false;
        } else if (!said.ok) told.textContent = t.gone;
      } catch {
        told.textContent = t.gone;
      }
    };
    const buttons = el("div", "op-card-buttons");
    const add = (cls, text, value) => {
      const b = el("button", `pa-btn ${cls} pa-btn-sm`, text);
      b.type = "button";
      b.addEventListener("click", () => answer(value));
      buttons.append(b);
    };
    if (asked) {
      add("pa-btn-primary", link ? t.open : t.submit, "once");
      add("pa-btn-ghost", t.decline, "no");
    } else {
      // What the app offers on this card: a tool marked destructive asks each time.
      add("pa-btn-primary", t.once, "once");
      if (card.choices.includes("chat")) add("pa-btn-secondary", t.chat, "chat");
      if (card.choices.includes("always")) add("pa-btn-secondary", start ? t.always : t.alwaysTool, "always");
      add("pa-btn-ghost", t.no, "no");
    }
    node.append(buttons);
    waiting.after(node);
    shown = node;
    const label = waiting.querySelector(".doing");
    if (label) label.textContent = t.waiting;
    keepEnd();
  }

  window.paToolCard = { show };
})();
