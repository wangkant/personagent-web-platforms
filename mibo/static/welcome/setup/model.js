"use strict";
// Settings › Model, and the service chooser the first run shares (onboard.js).
// At the top, what is in use: the service, the model, its state, and the two things
// people do most, change the model (the service's own list) and test the connection.
// Under it the chooser (open while nothing is connected, else behind 更换服务), the
// measured abilities and the usage (modelbar.js), and a fold for the backup model
// and the models for other uses. Saving goes through /api/config, which tests first.
(() => {
  // Each service's mark: two letters on a plain tile; a chip for one on this computer.
  const MARKS = {
    deepseek: "DS", bailian: "QW", zhipu: "GL", moonshot: "Ki", ark: "DB", siliconflow: "SF", minimax: "MM",
    qianfan: "QF", hunyuan: "HY", openrouter: "OR", openai: "AI", anthropic: "Cl", gemini: "Ge", xai: "X",
    mistral: "Mi", groq: "Gq",
  };
  // Where to get a program that runs models on this computer.
  const LOCAL_GET = { ollama: "https://ollama.com/download", lmstudio: "https://lmstudio.ai/download" };
  // Tiles shown before "more services".
  const TILES = 8;
  const SIGNIN_POLL_MS = 1500;
  const POLL_MS = 2500;
  // How long "working" stays beside the state after a test.
  const TESTED_MS = 6000;
  // The backup model and the models for other uses, as /api/tuning names them.
  const MORE = [
    ["LLM_FALLBACK_MODEL", "fallback"],
    ["LLM_FALLBACK_BASE_URL", "fallbackBase"],
    ["LLM_FALLBACK_API_KEY", "fallbackKey"],
    ["LLM_JUDGE_MODEL", "judge"],
    ["LLM_DM_MODEL", "dm"],
    ["CONTEXT_WINDOW", "contextWindow"],
  ];
  // The help under the context window, by where the window in use when it is blank comes from (context.window).
  const WINDOW_HELP = { host: "contextWindowHost", error: "contextWindowLearned", assumed: "contextWindowAssumed" };
  // A failed save or test, as a state of the top card.
  const STATE_OF = {
    key_rejected: "key", model_no_balance: "no_balance", model_not_found: "not_found", model_limited: "limited",
  };
  const SVG = "http://www.w3.org/2000/svg";
  const PATHS = {
    check: "M5 12.5l4.2 4.2L19 7",
    down: "M6 9l6 6 6-6",
    out: "M14 4.5h5.5V10M19.5 4.5L11 13M18 14v4.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1H10",
    search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5",
    chevron: "M9 6l6 6-6 6",
    spark: "M12 3.5l1.9 5.6 5.6 1.9-5.6 1.9L12 18.5l-1.9-5.6-5.6-1.9 5.6-1.9z",
    eye: "M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zM12 9.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z",
    eyeOff: "M3.5 3.5l17 17M10.6 5.6A10 10 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.9 3.7M6.4 6.9C3.9 8.6 2.5 12 2.5 12s3.5 6.5 9.5 6.5a9.6 9.6 0 0 0 4.3-1M9.9 9.9a2.8 2.8 0 0 0 4.2 4.2",
    chip: "M8 3.5v3M16 3.5v3M8 17.5v3M16 17.5v3M3.5 8h3M3.5 16h3M17.5 8h3M17.5 16h3M7.5 6.5h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z",
    server: "M5 4.5h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM5 13.5h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1zM7.5 7.5h.01M7.5 16.5h.01",
    tools: "M14.7 6.3a4 4 0 0 0-5.4 5.4l-4.8 4.8a1.4 1.4 0 0 0 2 2l4.8-4.8a4 4 0 0 0 5.4-5.4l-2.4 2.4-2-.4-.4-2z",
    pictures: "M5 5h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM4 16l4.5-4.5L13 16M13.5 13.5l2-2L20 16M15.5 8.5h.01",
    thinking: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.6 10.8c.6.5 1 1.2 1.1 2V16h5v-.2c.1-.8.5-1.5 1.1-2A6 6 0 0 0 12 3z",
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function icon(name, cls = "setup-icon") {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", cls);
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", PATHS[name]);
    svg.append(path);
    return svg;
  }
  const fill = (text, values = {}) => text.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
  function button(cls, text, onClick) {
    const node = el("button", `setup-button ${cls}`);
    node.type = "button";
    node.append(el("span", "setup-button-label", text));
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }
  function busy(node, on, text) {
    node.disabled = on;
    node.classList.toggle("setup-busy", on);
    node.setAttribute("aria-busy", String(on));
    if (text !== undefined) node.querySelector(".setup-button-label").textContent = text;
  }
  function outLink(href, text) {
    const a = el("a", "setup-link");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.append(el("span", "", text), icon("out", "setup-icon setup-icon-sm"));
    return a;
  }
  let fieldCount = 0;
  // A label above, help and then an error under, tied by aria.
  function field(label, control, help = "") {
    const id = `mp-field-${++fieldCount}`;
    const wrap = el("div", "setup-field");
    const head = el("div", "setup-label-row");
    const name = el("label", "setup-label", label);
    name.htmlFor = id;
    head.append(name);
    const target = control.matches("input, select") ? control : control.querySelector("input, select");
    target.id = id;
    const helpLine = el("p", "setup-help", help);
    helpLine.id = `${id}-help`;
    helpLine.hidden = !help;
    const error = el("p", "setup-error");
    error.id = `${id}-error`;
    error.hidden = true;
    error.setAttribute("role", "alert");
    wrap.append(head, control, helpLine, error);
    const described = () => [!helpLine.hidden && helpLine.id, !error.hidden && error.id].filter(Boolean).join(" ");
    target.setAttribute("aria-describedby", described());
    return {
      wrap, head, control: target,
      help(text) {
        helpLine.textContent = text || "";
        helpLine.hidden = !text;
        target.setAttribute("aria-describedby", described());
      },
      error(text) {
        error.textContent = text || "";
        error.hidden = !text;
        target.setAttribute("aria-invalid", String(Boolean(text)));
        target.setAttribute("aria-describedby", described());
      },
    };
  }
  function input(cls = "") {
    const node = el("input", `setup-input ${cls}`.trim());
    node.type = "text";
    node.autocomplete = "off";
    node.spellcheck = false;
    return node;
  }
  function fold(title) {
    const node = el("details", "setup-fold");
    const summary = el("summary", "setup-fold-summary");
    summary.append(icon("chevron", "setup-icon setup-fold-chevron"), el("span", "", title));
    const body = el("div", "setup-fold-body");
    node.append(summary, body);
    return { node, body };
  }
  const errorCode = (error) => (error instanceof TypeError ? "offline" : error?.message || String(error));

  // A service by id, named for the page's language: its short name for the tiles and the top card, and a
  // second line where it helps (setup_model.services in the catalog).
  function naming(catalog) {
    const services = paI18n.words("setup_model").services;
    const spec = (id) => catalog.find((p) => p.id === id);
    return {
      spec,
      name: (id) => (services[id] ? services[id].name : spec(id) ? spec(id).name : id),
      sub: (id) => services[id]?.sub || "",
      mark(id, cls = "") {
        const box = el("span", `mp-mark ${cls}`.trim());
        box.setAttribute("aria-hidden", "true");
        // The service's own mark (providermarks.js); two letters for one that has none.
        const own = window.paProviderMarks?.[id];
        if (own) {
          box.classList.add("mp-mark-own");
          box.innerHTML = own;
        } else if (MARKS[id]) box.textContent = MARKS[id];
        else box.append(icon(spec(id)?.local ? "chip" : id === "other" ? "server" : "chip", "setup-icon mp-mark-icon"));
        return box;
      },
    };
  }

  // ---- The chooser: a service, then only what it needs. Settings and the first run share it.
  // env: {api, catalog (providers), state() {configured, provider, baseUrl, model},
  //       who() {name, character, lang}, done(state), cancel() (offered once a model is connected)}
  function chooser(env) {
    const t = paI18n.words("setup_model");
    const n = naming(env.catalog);
    const say = (code) => t.errors[code] || t.errors.generic;
    const root = el("div", "mp mp-chooser");
    let selected = "";
    let expanded = false;
    let working = false;
    let found = null;
    let replacing = false;
    const signin = { phase: "idle", error: "", url: "", timer: 0 };

    // What this computer already has, one press away.
    const foundBox = el("div", "mp-found");
    // The tiles.
    const grid = el("div", "mp-grid");
    grid.setAttribute("role", "radiogroup");
    grid.setAttribute("aria-label", t.chooseTitle);
    const moreTile = el("button", "mp-tile mp-tile-more");
    moreTile.type = "button";
    moreTile.addEventListener("click", () => {
      expanded = !expanded;
      paintGrid();
    });

    // What the chosen service needs.
    const details = el("form", "mp-details");
    details.noValidate = true;

    // (a) A program on this computer.
    const local = el("div", "mp-local");
    const localLine = el("p", "mp-local-line");
    const localModel = el("select", "setup-input setup-select");
    const localModelField = field(t.localModel, localModel);
    const localActions = el("div", "mp-inline-actions");
    const localRetry = button("setup-button-secondary setup-button-sm", t.localRetry, detect);
    const localGet = el("span");
    localActions.append(localRetry, localGet);
    local.append(localLine, localModelField.wrap, localActions);

    // (b) Signing in with OpenRouter.
    const signinBox = el("div", "mp-signin");
    const signinGo = button("setup-button-secondary mp-signin-button", t.signin, signinStart);
    signinGo.prepend(icon("spark"));
    const signinCancel = button("setup-button-ghost setup-button-sm", t.cancel, signinStop);
    const signinRow = el("div", "mp-inline-actions");
    signinRow.append(signinGo, signinCancel);
    const signinLine = el("p", "setup-help");
    signinLine.setAttribute("aria-live", "polite");
    const signinOpen = outLink("#", t.signinOpenPage);
    const signinError = el("p", "setup-error");
    signinError.setAttribute("role", "alert");
    const signinOr = el("div", "mp-or");
    signinOr.append(el("span", "", t.signinOr));
    signinBox.append(signinRow, signinLine, signinOpen, signinError, signinOr);

    // (c) A key.
    const keyBox = el("div", "setup-secret");
    const key = input("setup-secret-input");
    key.type = "password";
    key.placeholder = t.keyPlaceholder;
    const reveal = el("button", "setup-reveal");
    reveal.type = "button";
    const paintReveal = () => {
      const shown = key.type === "text";
      reveal.replaceChildren(icon(shown ? "eyeOff" : "eye"));
      reveal.setAttribute("aria-label", shown ? t.hideKey : t.showKey);
      reveal.title = shown ? t.hideKey : t.showKey;
    };
    reveal.addEventListener("click", () => {
      key.type = key.type === "password" ? "text" : "password";
      paintReveal();
      key.focus();
    });
    paintReveal();
    keyBox.append(key, reveal);
    const keyField = field(t.key, keyBox);
    const getKey = outLink("#", t.getKey);
    getKey.classList.add("setup-link-head");
    keyField.head.append(getKey);
    // A key already saved for this service: never shown again, only replaced.
    const savedRow = el("div", "mp-saved");
    savedRow.append(el("span", "mp-saved-dots", "••••••••••••"), el("span", "mp-saved-word", t.keySaved));
    const replace = button("setup-button-link", t.keyReplace, () => {
      replacing = true;
      paintDetails();
      key.focus();
    });
    savedRow.append(replace);
    keyField.wrap.insertBefore(savedRow, keyBox);
    const envLine = el("div", "mp-env");
    const envWords = el("p", "setup-help");
    const envUse = button("setup-button-secondary setup-button-sm", t.envUse);
    envLine.append(envWords, envUse);
    keyField.wrap.append(envLine);
    key.addEventListener("input", () => keyField.error(""));

    // The address and the model: open for a custom service, folded for the rest.
    const base = input();
    base.inputMode = "url";
    base.placeholder = "https://…/v1";
    const baseField = field(t.address, base, t.addressHelp);
    const modelInput = input();
    const modelField = field(t.model, modelInput, t.modelHelp);
    const open = el("div", "mp-open");
    const adv = fold(t.advanced);
    adv.node.classList.add("mp-advanced");
    const noteLine = el("p", "setup-help");
    adv.body.append(noteLine);
    base.addEventListener("input", () => baseField.error(""));
    modelInput.addEventListener("input", () => modelField.error(""));

    const foot = el("div", "setup-foot mp-foot");
    const go = button("setup-button-primary", t.connect);
    go.type = "submit";
    const cancel = button("setup-button-ghost", t.cancel, () => env.cancel());
    const progress = el("p", "setup-progress");
    progress.setAttribute("aria-live", "polite");
    foot.append(go, cancel, progress);
    const error = el("p", "setup-error mp-error");
    error.setAttribute("role", "alert");
    error.hidden = true;
    const stays = el("p", "setup-fine", t.keyStays);

    details.append(local, signinBox, keyField.wrap, open, adv.node, foot, error, stays);
    root.append(foundBox, grid, details);
    details.addEventListener("submit", (event) => {
      event.preventDefault();
      submit();
    });

    function paintGrid() {
      const ids = env.catalog.map((p) => p.id);
      const shown = expanded ? ids : ids.slice(0, TILES);
      // The chosen one stays in sight.
      if (selected && !shown.includes(selected)) shown.push(selected);
      grid.replaceChildren();
      for (const id of shown) {
        const tile = el("button", "mp-tile");
        tile.type = "button";
        tile.setAttribute("role", "radio");
        tile.setAttribute("aria-checked", String(id === selected));
        tile.title = n.spec(id).name;
        const words = el("span", "mp-tile-words");
        words.append(el("span", "mp-tile-name", n.name(id)));
        const sub = n.sub(id);
        if (sub) words.append(el("span", "mp-tile-sub", sub));
        tile.append(n.mark(id), words);
        if (id === selected) tile.append(icon("check", "setup-icon mp-tile-tick"));
        tile.disabled = working;
        tile.addEventListener("click", () => choose(id));
        grid.append(tile);
      }
      if (ids.length > TILES) {
        moreTile.replaceChildren(el("span", "mp-tile-name", expanded ? t.fewerServices : `${t.moreServices} (${ids.length - TILES})`),
                                 icon("down", `setup-icon mp-more-icon${expanded ? " mp-more-up" : ""}`));
        moreTile.setAttribute("aria-expanded", String(expanded));
        grid.append(moreTile);
      }
    }

    function choose(id, focusKey = true) {
      selected = id;
      replacing = false;
      const s = env.state();
      const spec = n.spec(id);
      const mine = s.configured && s.provider === id;
      base.value = (mine && s.baseUrl) || spec.baseUrl;
      modelInput.value = (mine && s.model) || spec.model;
      key.value = "";
      for (const f of [keyField, baseField, modelField, localModelField]) f.error("");
      error.hidden = true;
      paintGrid();
      paintFound();
      paintDetails();
      if (focusKey && !keyField.wrap.hidden && !keyBox.hidden) key.focus();
    }

    function paintFound() {
      foundBox.replaceChildren();
      const rows = [
        ...(found?.local || []).map((row) => [row.provider, fill(t.foundLocal, { name: n.name(row.provider) })]),
        ...(found?.keys || []).map((row) => [row.provider, fill(t.foundEnv, { name: n.name(row.provider), variable: row.variable })]),
      ];
      for (const [id, text] of rows) {
        if (id === selected) continue;
        const line = el("div", "mp-found-row");
        line.append(n.mark(id), el("p", "mp-found-words", text),
                    button("setup-button-secondary setup-button-sm", t.foundPick, () => choose(id)));
        foundBox.append(line);
      }
      foundBox.hidden = !foundBox.children.length;
    }

    function localFound() {
      return found?.local.find((row) => row.provider === selected) || null;
    }

    function paintDetails() {
      const s = env.state();
      const spec = n.spec(selected);
      const isLocal = spec.local;
      const custom = selected === "other";
      const mine = s.configured && s.provider === selected;
      const running = isLocal ? localFound() : null;
      details.hidden = !selected;

      local.hidden = !isLocal;
      if (isLocal) {
        const name = n.name(selected);
        localLine.textContent = fill(running ? t.localRunning : t.localMissing, { name });
        localModel.replaceChildren(...(running?.models || []).map((m) => new Option(m, m)));
        if (running && mine && running.models.includes(s.model)) localModel.value = s.model;
        localModelField.wrap.hidden = !running || running.models.length < 2;
        localRetry.hidden = Boolean(running);
        localGet.replaceChildren(...(LOCAL_GET[selected] && !running ? [outLink(LOCAL_GET[selected], fill(t.localGet, { name }))] : []));
      }

      signinBox.hidden = selected !== "openrouter";
      paintSignin();

      keyField.wrap.hidden = isLocal;
      const keepSaved = mine && !replacing;
      savedRow.hidden = !keepSaved;
      keyBox.hidden = keepSaved;
      getKey.hidden = !spec.keyUrl;
      if (spec.keyUrl) getKey.href = spec.keyUrl;
      const env_ = found?.keys.find((row) => row.provider === selected);
      envLine.hidden = !env_ || keepSaved;
      if (env_) {
        envWords.textContent = fill(t.envFound, { variable: env_.variable });
        envUse.onclick = () => save({ provider: selected, apiKey: "", apiKeyFrom: env_.variable, baseUrl: spec.baseUrl, model: spec.model });
      }

      // A custom service needs its address and model in sight; the rest keep them folded.
      noteLine.textContent = spec.note;
      noteLine.hidden = !noteLine.textContent;
      modelField.help(custom ? "" : t.modelHelp);
      if (custom) {
        open.append(baseField.wrap, modelField.wrap);
        adv.node.hidden = true;
      } else {
        adv.body.prepend(baseField.wrap, modelField.wrap);
        adv.node.hidden = false;
        // A local program not found running may live at another address.
        if (isLocal && !running) adv.node.open = true;
      }
      open.hidden = !custom;
      // Nothing to go back to before a first connection.
      cancel.hidden = !s.configured;
      stays.hidden = isLocal;
      paintWorking();
    }

    function paintWorking() {
      busy(go, working, working ? t.connecting : t.connect);
      progress.textContent = working ? fill(t.connectingLine, { name: n.name(selected) }) : "";
      for (const one of [key, base, modelInput]) one.readOnly = working;
      cancel.disabled = working;
      envUse.disabled = working;
      for (const tile of grid.querySelectorAll(".mp-tile")) tile.disabled = working;
      if (working) signinGo.disabled = true;
    }

    function submit() {
      const s = env.state();
      const spec = n.spec(selected);
      const mine = s.configured && s.provider === selected;
      const typed = key.value.trim();
      if (!spec.local && !typed && !(mine && !replacing)) {
        keyField.error(t.errors.key_required);
        key.focus();
        return;
      }
      const running = spec.local ? localFound() : null;
      save({
        provider: selected,
        apiKey: typed,
        baseUrl: running && !adv.node.open ? running.baseUrl : base.value.trim(),
        model: running && !adv.node.open ? localModel.value || running.models[0] : modelInput.value.trim(),
      });
    }

    async function save(payload) {
      if (working) return;
      working = true;
      error.hidden = true;
      for (const f of [keyField, baseField, modelField]) f.error("");
      paintWorking();
      try {
        const state = await env.api("config", { ...env.who(), ...payload });
        key.value = "";
        replacing = false;
        working = false;
        env.done(state);
      } catch (failure) {
        working = false;
        place(errorCode(failure));
      } finally {
        paintWorking();
        paintSignin();
      }
    }

    // A code from the save: under its field, else one line under the button.
    function place(code) {
      const text = say(code);
      if (["key_required", "key_again", "key_rejected", "key_malformed"].includes(code) && !keyField.wrap.hidden) {
        replacing = true;
        paintDetails();
        keyField.error(text);
        key.focus();
        return;
      }
      if (["model_required", "model_not_found", "invalid_url", "https_required"].includes(code)) {
        if (selected !== "other") adv.node.open = true;
        const f = code.startsWith("model") ? modelField : baseField;
        f.error(text);
        f.control.focus();
        return;
      }
      error.textContent = text;
      error.hidden = false;
    }

    // ---- Signing in: the browser approves, the server keeps the key.
    function paintSignin() {
      const waiting = signin.phase === "waiting";
      const verifying = signin.phase === "verifying";
      const opening = signin.phase === "opening";
      busy(signinGo, opening || waiting || verifying,
        opening ? t.signinOpening : waiting || verifying ? t.signinPending : t.signin);
      if (working) signinGo.disabled = true;
      signinCancel.hidden = !waiting;
      signinLine.hidden = !(waiting || verifying);
      signinLine.textContent = verifying ? t.signinVerifying : t.signinWaiting;
      signinOpen.hidden = !(waiting && signin.url);
      if (waiting && signin.url) signinOpen.href = signin.url;
      signinError.hidden = signin.phase !== "failed";
      signinError.textContent = signin.phase === "failed" ? say(signin.error) : "";
    }
    function signinFollow(value) {
      signin.phase = value.state === "idle" ? (signin.phase === "opening" ? "opening" : "idle") : value.state;
      signin.error = value.error || "";
      signin.url = value.url || "";
      const polling = signin.phase === "waiting" || signin.phase === "verifying";
      if (polling && !signin.timer) signin.timer = setInterval(signinPoll, SIGNIN_POLL_MS);
      if (!polling && signin.timer) {
        clearInterval(signin.timer);
        signin.timer = 0;
      }
      // The server keeps a finished attempt until the page has seen it.
      if (signin.phase === "done" || signin.phase === "failed") env.api("signin/cancel", {}).catch(() => {});
      if (signin.phase === "done") return signinDone();
      paintSignin();
    }
    async function signinPoll() {
      try {
        signinFollow(await env.api("signin"));
      } catch {
        // The next tick asks again.
      }
    }
    async function signinStart() {
      signin.phase = "opening";
      signin.error = "";
      paintSignin();
      const form = env.who();
      const s = env.state();
      // The model already chosen at OpenRouter stays; otherwise the service's own default.
      if (s.provider === "openrouter" && s.configured && s.model) form.model = s.model;
      try {
        signinFollow(await env.api("signin/start", { provider: "openrouter", form, pageLang: paI18n.lang }));
      } catch (failure) {
        signin.phase = "failed";
        signin.error = errorCode(failure);
        paintSignin();
      }
    }
    async function signinStop() {
      busy(signinCancel, true);
      try {
        signinFollow(await env.api("signin/cancel", {}));
      } catch {
        // Still waiting: the next poll says so.
      } finally {
        busy(signinCancel, false);
      }
    }
    async function signinDone() {
      signin.phase = "idle";
      paintSignin();
      try {
        env.done(await env.api("state"));
      } catch {
        // The next look at the page reads it again.
      }
    }

    // What this computer has: a local program running, a key in the environment.
    async function detect() {
      busy(localRetry, true);
      try {
        found = await env.api("detect");
      } catch {
        found = found || { local: [], keys: [] };
      }
      busy(localRetry, false);
      paintFound();
      if (selected) paintDetails();
    }

    detect();
    env.api("signin")
      .then((value) => (value.state === "waiting" || value.state === "verifying") && signinFollow(value))
      .catch(() => {});

    return {
      node: root,
      // Opened on a service: the saved one, else the first for the page's language.
      open(id, focusKey = false) {
        const s = env.state();
        choose(id || (s.configured ? s.provider : env.catalog[0].id), focusKey);
        if (focusKey) {
          replacing = true;
          paintDetails();
          key.focus();
        }
      },
      busy: () => working || signin.phase === "waiting" || signin.phase === "verifying",
    };
  }

  window.paModelPick = { chooser };

  // ================================================================ Settings › Model ==
  window.paSetup.part("model", (kit) => {
    const { ctx } = kit;
    const t = paI18n.words("setup_model");
    const n = naming(kit.catalog.providers);
    const say = (code) => t.errors[code] || t.errors.generic;
    const view = {
      // This visit's last test: null before any, else {state, at}.
      tested: null,
      testing: false,
      switching: "",
      // Connected during this visit: the top card says so and offers the chat.
      done: false,
      choosing: false,
      bar: null,
      list: null,
      listOpen: false,
      query: "",
      problem: "",
    };
    let pollTimer = 0;
    let testedTimer = 0;
    const parts = {};
    const more = { loaded: false, saving: false };

    const node = kit.section("model");

    // ---- The model in use.
    const now = el("div", "setup-card mp mp-now");
    const top = el("div", "mp-now-top");
    const markSlot = el("span", "mp-now-mark");
    const words = el("div", "mp-now-words");
    const modelName = el("p", "mp-now-model");
    const line = el("p", "mp-now-line");
    const serviceName = el("span", "mp-now-service");
    const state = el("span", "mp-state");
    line.append(serviceName, state);
    const marks = el("div", "mp-abilities");
    words.append(modelName, line);
    const actions = el("div", "mp-now-actions");
    const pickWrap = el("div", "mp-pick");
    const pickButton = button("setup-button-secondary setup-button-sm mp-pick-button", t.changeModel, () => toggleList());
    pickButton.append(icon("down", "setup-icon setup-icon-sm"));
    pickButton.setAttribute("aria-haspopup", "listbox");
    pickButton.setAttribute("aria-expanded", "false");
    const listBox = el("div", "mp-list");
    listBox.hidden = true;
    pickWrap.append(pickButton, listBox);
    const testButton = button("setup-button-secondary setup-button-sm", t.test, runTest);
    const chatButton = button("setup-button-primary setup-button-sm", t.toChat, () => ctx.go("chat"));
    actions.append(chatButton, pickWrap, testButton);
    top.append(markSlot, words, actions, marks);

    // A failure: what happened, and what to do about it.
    const fix = el("div", "mp-fix");
    fix.setAttribute("role", "alert");
    const fixWords = el("p", "mp-fix-words");
    const fixActions = el("div", "mp-fix-actions");
    fix.append(fixWords, fixActions);
    const changeService = button("setup-button-link mp-change-service", t.changeService, () => openChooser());
    const nowFoot = el("div", "mp-now-foot");
    nowFoot.append(changeService);
    now.append(top, fix, nowFoot);

    // ---- The chooser, in a card of its own.
    const chooseCard = el("div", "setup-card mp-choose-card");
    const chooseTitle = el("h3", "setup-card-title");
    const picker = chooser({
      api: ctx.api,
      catalog: kit.catalog.providers,
      state: () => kit.state,
      who: () => {
        const s = kit.state;
        return { name: s.name || "", character: s.character || "custom", lang: (s.configured && s.lang) || paI18n.lang };
      },
      done: connected,
      cancel: () => {
        view.choosing = false;
        paint();
        changeService.focus();
      },
    });
    chooseCard.append(chooseTitle, picker.node);

    node.append(now, chooseCard);
    // What the model can do, measured, and what it spent (modelbar.js).
    const info = window.paModelBar.settings(kit, node);
    node.append(buildMore());

    function openChooser(focusKey = false) {
      view.choosing = true;
      paint();
      picker.open(kit.state.configured ? kit.state.provider : "", focusKey);
      chooseCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    async function connected(fresh) {
      kit.state = fresh;
      view.done = true;
      view.choosing = false;
      view.tested = { state: "ok", at: Date.now() };
      view.list = null;
      more.loaded = false;
      ctx.changed();
      paint();
      await loadBar();
      info.show();
      now.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    // The state as best known: this visit's test, else the last ability check, else the agent's.
    function stateNow() {
      if (view.testing) return "testing";
      if (view.tested) return view.tested.state;
      const error = view.bar?.abilities?.error;
      if (error && ["key", "no_balance", "not_found", "limited"].includes(error)) return error;
      if (kit.state.modelUnreachable) return "unreachable";
      return "ok";
    }

    function paint() {
      const s = kit.state;
      const on = Boolean(s.configured);
      now.classList.toggle("mp-now-off", !on);
      markSlot.replaceChildren(on ? n.mark(s.provider, "mp-mark-lg") : n.mark("", "mp-mark-lg mp-mark-empty"));
      actions.hidden = !on;
      marks.hidden = !on;
      nowFoot.hidden = !on || view.choosing;
      if (!on) {
        modelName.textContent = t.noneTitle;
        serviceName.textContent = t.noneText;
        state.hidden = true;
        fix.hidden = true;
        view.choosing = true;
      } else {
        modelName.textContent = view.switching ? view.switching : s.model;
        modelName.title = s.model;
        serviceName.textContent = n.name(s.provider);
        const now_ = stateNow();
        state.hidden = false;
        state.className = `mp-state mp-state-${now_}`;
        const fresh = view.tested && view.tested.state === "ok" && Date.now() - view.tested.at < TESTED_MS && !view.done;
        state.textContent = view.switching ? t.switching : fresh ? t.tested : t.states[now_];
        paintFix(now_);
      }
      chatButton.hidden = !(on && view.done);
      busy(testButton, view.testing, view.testing ? t.testing : t.test);
      pickButton.disabled = Boolean(view.switching) || view.testing;
      paintMarks();
      chooseCard.hidden = !view.choosing;
      chooseTitle.textContent = on ? t.changeTitle : t.chooseTitle;
      parts.more.hidden = !on;
    }

    function paintFix(now_) {
      const problem = view.problem;
      const failing = !["ok", "testing"].includes(now_);
      fix.hidden = !failing && !problem;
      if (fix.hidden) return;
      fixWords.textContent = problem || t.fixes[now_];
      fixActions.replaceChildren();
      const spec = n.spec(kit.state.provider);
      if (now_ === "key") {
        fixActions.append(button("setup-button-secondary setup-button-sm", t.replaceKey, () => openChooser(true)));
        if (spec?.keyUrl) fixActions.append(outLink(spec.keyUrl, t.getKey));
      }
      if (now_ === "no_balance" && view.bar?.console) fixActions.append(outLink(view.bar.console, t.topUp));
      if (now_ === "not_found") fixActions.append(button("setup-button-secondary setup-button-sm", t.changeModel, () => toggleList(true)));
      fixActions.hidden = !fixActions.children.length;
    }

    // The measured abilities as small marks under the model's name.
    function paintMarks() {
      marks.replaceChildren();
      const bar = view.bar;
      if (!bar?.configured) return;
      if (bar.checking) {
        marks.append(el("span", "mp-ability mp-ability-wait", t.checkingAbilities));
        return;
      }
      const rec = bar.abilities;
      if (!rec) return;
      for (const name of ["tools", "pictures", "thinking"]) {
        // Only what works shows here; the card below says what is missing and why.
        if (rec[name] !== "yes") continue;
        const chip = el("span", "mp-ability");
        chip.append(icon(name, "setup-icon mp-ability-icon"), el("span", "", t.abilities[name]));
        marks.append(chip);
      }
    }

    async function loadBar() {
      clearTimeout(pollTimer);
      try {
        view.bar = await ctx.api("modelbar");
      } catch {
        return;
      }
      paint();
      if (view.bar.checking) pollTimer = setTimeout(loadBar, POLL_MS);
    }

    async function runTest() {
      if (view.testing) return;
      view.testing = true;
      view.problem = "";
      view.done = false;
      paint();
      let result = "ok";
      try {
        await ctx.api("look/check", {});
      } catch (failure) {
        // A failed test comes back as its error code (app.js throws on one).
        result = STATE_OF[errorCode(failure)] || "unreachable";
      }
      view.testing = false;
      view.tested = { state: result, at: Date.now() };
      paint();
      clearTimeout(testedTimer);
      if (result === "ok") testedTimer = setTimeout(paint, TESTED_MS);
    }

    // ---- The service's models: searchable, the current one ticked.
    async function toggleList(force) {
      const opening = force ?? listBox.hidden;
      if (!opening) return closeList();
      view.listOpen = true;
      view.query = "";
      listBox.hidden = false;
      pickButton.setAttribute("aria-expanded", "true");
      paintList();
      if (!view.list) {
        try {
          view.list = await ctx.api("modelbar/models");
        } catch {
          view.list = { current: kit.state.model, recent: [], models: [], free: [] };
        }
        if (view.listOpen) paintList();
      }
      listBox.querySelector("input")?.focus();
    }
    function closeList() {
      view.listOpen = false;
      listBox.hidden = true;
      pickButton.setAttribute("aria-expanded", "false");
    }
    const search = input("mp-search-input");
    search.placeholder = t.search;
    search.setAttribute("aria-label", t.search);
    const searchBox = el("label", "mp-search");
    searchBox.append(icon("search", "setup-icon setup-icon-sm"), search);
    const rows = el("div", "mp-rows");
    rows.setAttribute("role", "listbox");
    listBox.append(searchBox, rows);
    search.addEventListener("input", () => {
      view.query = search.value;
      paintRows();
    });
    search.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        rows.querySelector(".mp-row")?.focus();
      } else if (event.key === "Enter") {
        event.preventDefault();
        rows.querySelector(".mp-row")?.click();
      }
    });
    listBox.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        closeList();
        pickButton.focus();
        return;
      }
      const all = [...rows.querySelectorAll(".mp-row")];
      const at = all.indexOf(document.activeElement);
      if (at < 0) return;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const next = at + (event.key === "ArrowDown" ? 1 : -1);
        (next < 0 ? search : all[Math.min(next, all.length - 1)]).focus();
      }
    });
    document.addEventListener("pointerdown", (event) => {
      if (view.listOpen && !pickWrap.contains(event.target)) closeList();
    }, true);

    function paintList() {
      search.value = view.query;
      paintRows();
    }
    function paintRows() {
      rows.replaceChildren();
      const list = view.list;
      if (!list) {
        rows.append(el("p", "mp-rows-note", t.loading));
        return;
      }
      const q = view.query.trim().toLowerCase();
      const match = (m) => !q || m.toLowerCase().includes(q);
      const free = new Set(list.free || []);
      const current = kit.state.model;
      const recent = (list.recent || []).filter((m) => m !== current && match(m));
      const rest = (list.models || []).filter((m) => m !== current && !recent.includes(m) && match(m));
      if (match(current)) rows.append(row(current, true, free));
      if (recent.length) rows.append(el("p", "mp-rows-head", t.recent), ...recent.map((m) => row(m, false, free)));
      if (rest.length) {
        if (recent.length) rows.append(el("p", "mp-rows-head", t.all));
        rows.append(...rest.slice(0, 300).map((m) => row(m, false, free)));
      }
      // A name the service does not list may still be one it runs.
      const typed = view.query.trim();
      const known = [current, ...(list.recent || []), ...(list.models || [])];
      if (typed && !known.includes(typed)) {
        const use = row(typed, false, free);
        use.querySelector(".mp-row-name").textContent = fill(t.useTyped, { name: typed });
        rows.append(use);
      }
      if (!rows.children.length) rows.append(el("p", "mp-rows-note", t.noMatch));
    }
    function row(model, current, free) {
      const item = el("button", "mp-row");
      item.type = "button";
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(current));
      item.append(el("span", "mp-row-name", model));
      if (free.has(model)) item.append(el("span", "mp-row-tag", t.free));
      if (current) item.append(icon("check", "setup-icon setup-icon-sm mp-row-tick"));
      item.addEventListener("click", () => (current ? closeList() : switchTo(model)));
      return item;
    }

    async function switchTo(model) {
      closeList();
      pickButton.focus();
      view.switching = model;
      view.problem = "";
      view.done = false;
      paint();
      try {
        view.bar = await ctx.api("modelbar/model", { model });
        kit.state = { ...kit.state, model };
        view.tested = { state: "ok", at: Date.now() };
        view.list = null;
        ctx.changed();
      } catch (failure) {
        const code = errorCode(failure);
        view.problem = fill(t.switchFailed, { name: model }) + say(code);
      }
      view.switching = "";
      paint();
      clearTimeout(testedTimer);
      testedTimer = setTimeout(paint, TESTED_MS);
      loadBar();
      info.show();
    }

    // ---- The backup model and the models for other uses (GET/POST /api/tuning).
    function buildMore() {
      const box = fold(t.more);
      box.node.classList.add("setup-card");
      const form = el("form", "mp-more-form");
      form.noValidate = true;
      const fields = {};
      const pair = el("div", "mp-more-pair");
      for (const [name, word] of MORE) {
        const control = input();
        if (name === "LLM_FALLBACK_API_KEY") {
          control.type = "password";
          control.classList.add("setup-secret-input");
        }
        if (name === "LLM_FALLBACK_BASE_URL") control.inputMode = "url";
        if (name === "CONTEXT_WINDOW") control.inputMode = "numeric";
        const one = field(t[word], control, t[`${word}Help`]);
        control.addEventListener("input", () => {
          one.error("");
          note.textContent = "";
        });
        fields[name] = one;
        if (name === "LLM_FALLBACK_BASE_URL") form.append(pair);
        (name.startsWith("LLM_FALLBACK_") && name !== "LLM_FALLBACK_MODEL" ? pair : form).append(one.wrap);
      }
      const footRow = el("div", "setup-foot");
      const save = button("setup-button-secondary", t.save);
      save.type = "submit";
      const note = el("p", "setup-foot-note");
      note.setAttribute("aria-live", "polite");
      footRow.append(save, note);
      const failLine = el("p", "setup-error");
      failLine.setAttribute("role", "alert");
      failLine.hidden = true;
      form.append(footRow, failLine);
      box.body.append(form);
      box.node.addEventListener("toggle", () => box.node.open && !more.loaded && loadMore());
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        saveMore();
      });
      Object.assign(more, { fields, save, note, failLine });
      parts.more = box.node;
      return box.node;
    }
    async function loadMore() {
      try {
        const value = await ctx.api("tuning");
        for (const [name] of MORE) {
          const one = more.fields[name].control;
          if (name === "LLM_FALLBACK_API_KEY") {
            one.value = "";
            one.placeholder = value.secrets[name] ? t.fallbackKeyKeep : "";
          } else one.value = value.values[name] || "";
        }
        more.fields.LLM_FALLBACK_BASE_URL.control.placeholder = value.llmBaseUrl || "";
        const room = value.contextWindow;
        more.fields.CONTEXT_WINDOW.control.placeholder = String(room.tokens);
        more.fields.CONTEXT_WINDOW.help(fill(t[WINDOW_HELP[room.from]], { n: room.tokens.toLocaleString() }));
        more.loaded = true;
        more.failLine.hidden = true;
      } catch (failure) {
        more.failLine.textContent = say(errorCode(failure));
        more.failLine.hidden = false;
      }
    }
    async function saveMore() {
      if (more.saving) return;
      const values = {};
      for (const [name] of MORE) {
        const value = more.fields[name].control.value.trim();
        // An empty key field keeps the saved key.
        if (name === "LLM_FALLBACK_API_KEY" && !value) continue;
        values[name] = value;
      }
      more.saving = true;
      more.failLine.hidden = true;
      more.note.textContent = "";
      busy(more.save, true, t.saving);
      try {
        await ctx.api("tuning", { values });
        await loadMore();
        more.note.textContent = t.saved;
      } catch (failure) {
        const code = errorCode(failure);
        const [kind, name] = code.split(":");
        if (kind === "tuning_invalid" && more.fields[name]) more.fields[name].error(t.errors.tuning_invalid);
        else if (code === "tuning_key_needed") more.fields.LLM_FALLBACK_API_KEY.error(say(code));
        else if (code === "tuning_https") more.fields.LLM_FALLBACK_BASE_URL.error(say(code));
        else {
          more.failLine.textContent = say(code);
          more.failLine.hidden = false;
        }
      } finally {
        more.saving = false;
        busy(more.save, false, t.save);
      }
    }

    // ---- Shown.
    if (!kit.state.configured) picker.open("");
    paint();
    loadBar();

    // Opened again: the model may have been connected or changed elsewhere since.
    async function show(asked) {
      // Asked for by a link while a model works: the chooser opens.
      if (asked && kit.state.configured && !view.choosing) openChooser();
      if (picker.busy() || view.switching) return;
      try {
        const fresh = await ctx.api("state");
        const s = kit.state;
        if (fresh.configured !== s.configured || fresh.provider !== s.provider || fresh.model !== s.model) {
          view.tested = null;
          view.list = null;
          kit.state = fresh;
          if (!fresh.configured) picker.open("");
        } else kit.state = fresh;
      } catch {
        return;
      }
      paint();
      loadBar();
      info.show();
    }

    return { node, show };
  });
})();
