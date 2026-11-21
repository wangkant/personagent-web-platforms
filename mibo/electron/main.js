// Mibo's Electron main process: one window holding browser views, driven by the Python app over a local websocket.
//
// stdin carries one line, {"port", "token", "data", "downloads", "held"}, and is then closed: the main process
// connects to ws://127.0.0.1:<port>, sends {"hello": token} and answers requests {"id", "op", ...} with
// {"id", "result"} or {"id", "error"}. Events go the other way as {"event", ...}. The token is never written to
// disk or argv, and the main process listens on no port of its own: page operation goes through
// webContents.debugger, in process. Its profile, caches and logs go to "data", a folder in the app's home, never to
// the system's per-user folders.
//
// Ops:
//   tab.create {place, offscreen, ua}   a view on the persistent partition, its debugger attached and never
//                                       throttled; place "front" (shown), "under" (in the window beneath the views
//                                       shown) or "out" (in no window)
//   tab.close {tab}, tab.url {tab}
//   cdp {tab, method, params, session}  one DevTools command, to the page or to a frame's session
//   capture {tab}                       webContents.capturePage() as base64 PNG
//   download.answer {download, yes}     a held download moved to the download folder, or dropped
//   metrics                             the processes Electron runs, with their memory
//   quit
// Events: ready, cdp {tab, method, params, session}, opened {tab, url}, download {download, tab, url, filename,
// path, bytes}, download.done {download, state, finished, path}.
"use strict";

const { app, BrowserWindow, WebContentsView, session } = require("electron");
const fs = require("node:fs");
const path = require("node:path");

const PARTITION = "persist:mibo-browse";
const views = new Map();
const downloads = new Map();
let socket = null;
let window = null;
let made = 0;

function send(message) {
  if (socket && socket.readyState === 1) socket.send(JSON.stringify(message));
}

// The user agent without Electron's and this app's own words, as a Chrome of the same version sends it.
function chromeAgent(agent) {
  return agent.replace(/ Electron\/\S+/, "").replace(/ mibo-shell\/\S+/, "");
}

function layout() {
  const [wide, high] = window.getContentSize();
  for (const entry of views.values()) {
    if (entry.place !== "out") entry.view.setBounds({ x: 0, y: 0, width: wide, height: high });
  }
}

function create({ place = "front", offscreen = false, ua = "" } = {}) {
  const view = new WebContentsView({
    webPreferences: {
      partition: PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false,
      backgroundThrottling: false, offscreen: Boolean(offscreen),
    },
  });
  const id = `tab-${++made}`;
  const contents = view.webContents;
  if (ua === "chrome") contents.setUserAgent(chromeAgent(contents.getUserAgent()));
  // A page never opens a window of its own here: the app decides where an address goes.
  contents.setWindowOpenHandler(({ url }) => {
    send({ event: "opened", tab: id, url });
    return { action: "deny" };
  });
  contents.debugger.attach("1.3");
  contents.debugger.on("message", (_event, method, params, sessionId) => {
    send({ event: "cdp", tab: id, method, params, session: sessionId || "" });
  });
  contents.debugger.on("detach", (_event, reason) => send({ event: "detached", tab: id, reason }));
  views.set(id, { view, place });
  if (place === "front") window.contentView.addChildView(view);
  if (place === "under") window.contentView.addChildView(view, 0);
  layout();
  return { tab: id, pid: contents.getOSProcessId() };
}

function contentsOf(id) {
  const entry = views.get(id);
  if (!entry) throw new Error(`no tab ${id}`);
  return entry.view.webContents;
}

// A free name in the download folder: "a.csv", then "a (1).csv", as browsers name a second copy.
function freeName(folder, name) {
  const { name: stem, ext } = path.parse(name);
  let target = path.join(folder, name);
  for (let n = 1; fs.existsSync(target); n++) target = path.join(folder, `${stem} (${n})${ext}`);
  return target;
}

// A download finished and answered: moved to the download folder on yes, deleted on no.
function settle(id) {
  const held = downloads.get(id);
  if (held.state === "progressing" || held.yes === undefined) return;
  downloads.delete(id);
  let placed = "";
  if (held.yes && held.state === "completed") {
    placed = freeName(held.folder, path.basename(held.staged));
    fs.renameSync(held.staged, placed);
  } else {
    fs.rmSync(path.dirname(held.staged), { recursive: true, force: true });
  }
  send({ event: "download.done", download: id, state: held.yes ? held.state : "dropped", finished: held.state,
    path: placed });
}

async function handle(op, request) {
  switch (op) {
    case "tab.create":
      return create(request);
    case "tab.close": {
      const entry = views.get(request.tab);
      if (entry) {
        if (entry.place !== "out") window.contentView.removeChildView(entry.view);
        entry.view.webContents.close();
        views.delete(request.tab);
      }
      return {};
    }
    case "tab.url":
      return { url: contentsOf(request.tab).getURL() };
    case "cdp":
      return contentsOf(request.tab).debugger.sendCommand(request.method, request.params || {},
        request.session || undefined);
    case "capture": {
      const image = await contentsOf(request.tab).capturePage();
      const size = image.getSize();
      return { png: image.toPNG().toString("base64"), width: size.width, height: size.height };
    }
    case "download.answer": {
      const held = downloads.get(request.download);
      if (!held) throw new Error(`no download ${request.download}`);
      held.yes = Boolean(request.yes);
      if (!held.yes && held.state === "progressing") held.item.cancel();
      settle(request.download);
      return {};
    }
    case "metrics":
      return {
        electron: process.versions.electron, chrome: process.versions.chrome,
        processes: app.getAppMetrics().map((p) => ({ pid: p.pid, type: p.type, memory: p.memory })),
        views: [...views].map(([id, entry]) => ({ tab: id, pid: entry.view.webContents.getOSProcessId() })),
      };
    case "quit":
      setImmediate(() => app.quit());
      return {};
    default:
      throw new Error(`unknown op ${op}`);
  }
}

function connect(config) {
  socket = new WebSocket(`ws://127.0.0.1:${config.port}`);
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ hello: config.token }));
    send({ event: "ready" });
  });
  socket.addEventListener("message", async ({ data }) => {
    const request = JSON.parse(String(data));
    try {
      send({ id: request.id, result: (await handle(request.op, request)) || {} });
    } catch (error) {
      send({ id: request.id, error: String(error && error.message ? error.message : error) });
    }
  });
  // Without the app there is nothing to show.
  socket.addEventListener("close", () => app.quit());
  socket.addEventListener("error", () => app.quit());
}

function holdDownloads(config) {
  let count = 0;
  // Electron cannot pause a download before its first bytes land (a small file completes at once), so each one is
  // saved into the app's own holding folder and stays there until the app's card is answered: moved to the
  // download folder on yes, deleted on no. No save dialog is ever shown.
  session.fromPartition(PARTITION).on("will-download", (_event, item, contents) => {
    const id = `download-${++count}`;
    const staged = path.join(config.held, id, item.getFilename());
    fs.mkdirSync(path.dirname(staged), { recursive: true });
    item.setSavePath(staged);
    const held = { item, staged, folder: config.downloads, state: "progressing", yes: undefined };
    downloads.set(id, held);
    const tab = [...views].find(([, entry]) => entry.view.webContents === contents);
    send({ event: "download", download: id, tab: tab ? tab[0] : "", url: item.getURL(),
      filename: item.getFilename(), path: path.join(config.downloads, item.getFilename()),
      bytes: item.getTotalBytes() });
    item.once("done", (_e, state) => {
      held.state = state;
      settle(id);
    });
  });
}

function start(config) {
  window = new BrowserWindow({
    width: 1100, height: 800, title: "Mibo",
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
      preload: path.join(__dirname, "preload.js") },
  });
  window.on("resize", layout);
  // The window's own page may not go anywhere: addresses belong in views.
  window.webContents.on("will-navigate", (event) => event.preventDefault());
  window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  window.loadURL("about:blank");
  holdDownloads(config);
  connect(config);
}

app.on("web-contents-created", (_event, contents) => {
  // No page may attach a webview, whatever it asks.
  contents.on("will-attach-webview", (event) => event.preventDefault());
});
app.on("window-all-closed", () => app.quit());

// The app writes its one line and closes stdin: read whole, the same way on every system.
const config = JSON.parse(fs.readFileSync(0, "utf8").split("\n")[0]);
app.setPath("userData", config.data);
app.setAppLogsPath(path.join(config.data, "logs"));
app.whenReady().then(() => start(config));
