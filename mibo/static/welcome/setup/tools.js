"use strict";
// Settings › 能力 › 连接器: the person's MCP servers (/api/mcp, mcp_hub.py) and the connector market
// (/api/mcp/market, mcp_market.py), in two views like Skills. Installed: each server's state said plainly with
// what to do next, its pinned version and updates, its values (names only: a saved value never comes back),
// its tools each asking, always allowed or never, servers brought over from other apps, and pasting a
// configuration for one the market does not have. Market: a short list chosen for the app, searched here, the
// official MCP Registry searched by name beyond it, and a detail view whose form fills the entry in. A local
// program starts only once the person allows its exact command; every tool asks on a card in the chat first.
// Market entries are data from the network: every string goes on the page as text, never as markup.
(() => {
  const WORDS = {
    zh: {
      hint: "连接器（MCP 服务器）为角色提供更多工具，例如查询地图、读取文档或管理任务。调用工具前默认需要你在对话中的卡片上确认；工具返回的内容仅作为资料，不会视为你的指令。",
      views: "连接器视图",
      installedTab: "已安装",
      marketTab: "市场",
      count: (n) => `共 ${n} 个连接器`,
      addTitle: "粘贴配置",
      addText: "适用于市场中没有的服务器：粘贴 mcpServers 配置（JSON）、服务器地址或启动命令，或选择配置文件。",
      pastePlaceholder: '{"mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}}\nhttps://mcp.example.com/mcp\nuvx mcp-server-time',
      add: "添加",
      pickFile: "选择文件",
      adding: "正在添加",
      added: "已添加。",
      addedNames: (names) => `已添加：${names.join("、")}`,
      secretNote: "环境变量和请求头中的值保存在系统凭据存储中，不写入文件；保存后不再显示，也不会发送给模型。",
      listTitle: "已添加的连接器",
      none: "尚未添加连接器。可在“市场”中选择，或从其他应用导入。",
      bad: "mcp.json 中以下条目无法读取，已跳过：{names}",
      elevated: "Mibo 正以管理员权限运行，为安全起见不会启动本机程序。请以普通权限重新打开 Mibo。",
      enable: (name) => `启用 ${name}`,
      remove: (name) => `移除 ${name}`,
      removeAsk: "移除连接器“{name}”？",
      removeText: "将停止该连接器，并删除其配置、已保存的值和工具权限设置。",
      removeOk: "移除",
      version: "版本 {v}",
      unpinned: "未固定版本",
      updateReady: "有新版本 {v}",
      update: "更新",
      updating: "正在更新",
      updateAll: (n) => `全部更新（${n}）`,
      updated: "已更新。新版本首次启动前需要再次确认。",
      pill: { connected: "已连接", starting: "正在启动", waiting: "等待确认", declined: "未允许", off: "已关闭", failed: "未连接",
        signin: "需要登录", signing_in: "正在登录" },
      state: {
        connected: "已连接，提供 {n} 个工具。",
        starting: "正在启动…",
        waiting: "尚未启动。首次启动本机程序需要你确认以下命令。",
        declined: "未允许启动此程序。",
        off: "已关闭，角色不会使用此连接器的工具。",
        signin: "此服务需要登录。点击“登录”后，将在浏览器中打开服务方的授权页面。",
        signing_in: "请在浏览器中完成授权。",
      },
      signinEnd: {
        expired: "登录已失效，请重新登录。",
        denied: "未在服务方页面上批准访问。",
        cancelled: "已取消登录。",
        timeout: "登录超过十分钟，已失效。",
        registration: "该服务器不允许应用自行注册，无法通过此方式登录。如服务方提供访问令牌，请在请求头中以 Authorization 填写。",
        failed: "服务方未完成授权。",
      },
      signin: "登录",
      signinOpen: "浏览器未打开时，请点击此处打开授权页面",
      signinCancel: "取消",
      signout: "退出登录",
      notRevoked: "已在本机退出登录，但服务方未确认撤销授权。如有需要，请在服务方的账户设置中撤销。",
      cut: "此服务器的工具超过 {n} 个上限，超出部分未加载。",
      errors_state: {
        not_found: "无法找到程序“{what}”。请安装该程序，或检查命令是否正确。",
        exited: "程序已退出。",
        refused: "无法连接该地址。请检查地址和网络。",
        timeout: "服务器未在规定时间内响应。",
        http: "服务器返回了错误。请检查地址和请求头。",
        protocol: "该程序未按 MCP 协议响应。请检查命令是否指向 MCP 服务器。",
        closed: "连接已断开。",
        missing_value: "缺少以下值：{what}。请在下方填写。",
        elevated: "Mibo 正以管理员权限运行，不会启动本机程序。请以普通权限重新打开 Mibo。",
        other: "连接未成功。",
      },
      runtimeNeed: "此命令需要 {title}，本机尚未安装。可将 {title} {version}（约 {size} MB，来源：{source}）下载至 Mibo 的文件夹，仅供 Mibo 启动的服务器使用，不影响本机其他程序。",
      runtimeGet: "下载 {title}",
      runtimeGetting: "正在下载 {title}（约 {size} MB）…",
      runtimeErrors: { runtime_fetch_failed: "下载未完成，请检查网络连接后重试。", runtime_hash_mismatch: "下载的文件与官方发布的校验值不符，已丢弃，请稍后重试。",
        runtime_write_failed: "无法写入 Mibo 的文件夹，请检查磁盘空间后重试。" },
      retryIn: "将在 {s} 秒后自动重试。",
      retry: "重试",
      output: "服务器输出",
      once: "本次允许",
      always: "始终允许",
      no: "不允许",
      allowAgain: "允许启动",
      alwaysStart: "已允许始终启动此命令。",
      folder: "工作文件夹：{path}",
      values: "已保存的值",
      addressKey: "地址中的密钥",
      valueSet: "已保存",
      valueMissing: "未填写",
      valueSave: "保存",
      valuePlaceholder: "输入新值",
      tools: "工具（{n}）",
      mode: { auto: "自动审查", always: "始终允许", never: "禁止" },
      modeLabel: (name) => `${name} 的权限`,
      readonlyHint: "提供方标注为只读",
      destructiveHint: "提供方标注为可能删除或覆盖数据",
      changed: "已更改，需要重新确认",
      groupRead: (n) => `只读工具（${n}）`,
      groupReadText: "提供方标注为只读、且未标注会删除数据的工具。此标注由提供方填写，Mibo 无法核实。",
      groupWrite: (n) => `写入与删除工具（${n}）`,
      groupWriteText: "其余工具，可能新建、修改或删除数据。提供方标注为可能删除数据的工具每次都需要确认。",
      logFold: (n) => (n ? `调用记录（${n}）` : "调用记录"),
      logNone: "暂无记录。",
      logClear: "清空记录",
      logClearAsk: "清空调用记录？",
      logClearText: "记录将被删除，无法恢复。",
      outcome: { done: "已完成", error: "工具返回错误", failed: "失败", declined: "已拒绝", unanswered: "未确认",
        never: "已禁止", changed: "工具已更改，未调用", start_once: "允许启动（本次）", start_always: "允许启动（始终）",
        start_no: "拒绝启动", refused: "审查已拦截" },
      how: { always: "始终允许", once: "本次允许", chat: "本次对话内允许", this_chat: "本次对话内已允许", review: "审查通过" },
      importOpen: "从其他应用导入",
      importTitle: "其他应用中的连接器",
      importHelp: "以下连接器来自本机 Claude、Cursor、VS Code 和 Cherry Studio 的配置。导入时复制一份到 Mibo，原配置保持不变；其中的值保存在系统凭据存储中。",
      importLooking: "正在查找",
      importNone: "未在 Claude、Cursor、VS Code 或 Cherry Studio 的配置中发现连接器。",
      importGo: (n) => (n ? `导入所选（${n}）` : "导入所选"),
      importing: "正在导入",
      importClose: "关闭",
      importHave: "已添加",
      importTaken: "已有同名连接器，导入后将另行命名",
      importOff: "在原应用中已停用，导入后保持停用",
      importValues: (n) => `含 ${n} 个保存的值`,
      importProblems: { unreadable: "无法读取该配置文件", variables: "使用了原应用特有的变量，无法导入",
        tools_bad_snippet: "配置无法识别", tools_bad_url: "地址无效", tools_bad_name: "名称无效" },
      search: "搜索连接器名称或用途",
      allCategories: "全部",
      shippedLine: (n) => `精选 ${n} 个连接器`,
      noMatch: "精选连接器中没有与“{q}”相符的条目。",
      noMatchCategory: "该分类中暂无连接器。",
      clear: "清除搜索",
      registryTitle: "官方 MCP 目录",
      registryNote: "以下条目来自官方 MCP 目录，按名称匹配，未经 Mibo 筛选。安装前请确认发布者。",
      registryLoading: "正在搜索官方 MCP 目录",
      registryNone: "官方 MCP 目录中没有名称含“{q}”的条目。",
      badgeRemote: "远程",
      badgeLocal: "本机程序",
      badgeSignin: "需要登录",
      badgeKey: "需要密钥",
      badgeSync: "同步",
      install: "安装",
      installing: "正在安装",
      installed: "已安装",
      installedDone: "已添加“{name}”，状态显示在下方。",
      back: "全部连接器",
      way: "安装方式",
      ways: { remote: "远程服务", npm: "本机运行（Node.js）", pypi: "本机运行（Python）" },
      runs: "运行内容",
      needs: "需要",
      needsSignin: "安装后在浏览器中登录服务方账户",
      needsNode: "Node.js；本机未安装时，可在安装后直接下载",
      needsUv: "uv（用于运行 Python 程序）；本机未安装时，可在安装后直接下载",
      needsKey: "填写下方的密钥",
      needsNothing: "无需其他准备",
      publisher: "发布者",
      source: "来源",
      licence: "许可",
      localNote: "这是会在本机运行的程序。首次启动前，Mibo 会请你确认确切的命令。",
      remoteNote: "这是由服务方运行的远程服务，工具的参数和结果经由服务方传递。",
      form: "需要填写的信息",
      required: "必填",
      optional: "选填",
      getKey: "获取密钥",
      secretSaved: "密钥保存在系统凭据存储中，不会显示，也不会发送给模型。",
      syncTitle: "同步魔搭托管服务",
      syncText: "填写魔搭 ModelScope 的访问令牌后，Mibo 会添加你在魔搭 MCP 广场上开通的全部托管服务。再次同步可加入之后开通的服务。",
      syncToken: "访问令牌",
      syncSaved: "访问令牌已保存，可留空直接同步。",
      syncGo: "同步",
      syncing: "正在同步",
      synced: (n) => `已同步 ${n} 个托管服务。`,
      syncNone: "魔搭中尚未开通托管服务。请先在魔搭 MCP 广场开通，再回到此处同步。",
      errors: {
        tools_bad_snippet: "无法识别该配置。请粘贴包含 command 或 url 的 JSON、以 https:// 开头的地址，或启动命令。",
        tools_bad_name: "名称只能包含字母、数字、点、连字符和下划线，最多 40 个字符。",
        tools_bad_url: "地址必须以 http:// 或 https:// 开头，且不能包含用户名或密码。",
        tools_name_taken: "已有同名的连接器。",
        tools_too_many: "连接器数量已达上限。",
        tools_unknown_server: "该连接器已不存在。",
        tools_unknown_tool: "该工具已不存在。",
        tools_write_failed: "无法写入配置文件。",
        tools_file_damaged: "主文件夹中的 mcp.json 无法读取。请修正或删除该文件。",
        tools_required: "请填写所有必填项。",
        market_gone: "目录中已没有该连接器。",
        market_search_failed: "无法搜索官方 MCP 目录，请检查网络后重试。",
        market_sync_failed: "无法同步。请检查访问令牌和网络后重试。",
        file_too_large: "文件过大。",
      },
    },
    en: {
      hint: "Connectors (MCP servers) give the character more tools, such as looking up maps, reading documents or managing tasks. Each call asks on a card in the chat first, by default; what a tool returns is only material, never taken as your instruction.",
      views: "Connector views",
      installedTab: "Installed",
      marketTab: "Market",
      count: (n) => `${n} ${n === 1 ? "connector" : "connectors"}`,
      addTitle: "Paste a configuration",
      addText: "For a server the market does not have: paste its mcpServers configuration (JSON), its address or the command that starts it, or choose its file.",
      pastePlaceholder: '{"mcpServers": {"time": {"command": "uvx", "args": ["mcp-server-time"]}}}\nhttps://mcp.example.com/mcp\nuvx mcp-server-time',
      add: "Add",
      pickFile: "Choose a file",
      adding: "Adding",
      added: "Added.",
      addedNames: (names) => `Added: ${names.join(", ")}`,
      secretNote: "Environment values and header values are kept in the system's credential store, not in a file; they are never shown again and never sent to the model.",
      listTitle: "Your connectors",
      none: "No connectors yet. Choose one in the Market, or import them from other apps.",
      bad: "These entries in mcp.json could not be read and were skipped: {names}",
      elevated: "Mibo is running as administrator, so it starts no program on this computer. Open Mibo again without administrator rights.",
      enable: (name) => `Use ${name}`,
      remove: (name) => `Remove ${name}`,
      removeAsk: "Remove the connector “{name}”?",
      removeText: "It stops, and its configuration, saved values and tool settings are deleted.",
      removeOk: "Remove",
      version: "Version {v}",
      unpinned: "Version not pinned",
      updateReady: "Version {v} available",
      update: "Update",
      updating: "Updating",
      updateAll: (n) => `Update all (${n})`,
      updated: "Updated. The new version asks before it first starts.",
      pill: { connected: "Connected", starting: "Starting", waiting: "Needs your OK", declined: "Not allowed", off: "Off", failed: "Not connected",
        signin: "Sign-in needed", signing_in: "Signing in" },
      state: {
        connected: "Connected with {n} tools.",
        starting: "Starting…",
        waiting: "Not started yet. A program on this computer starts the first time only once you allow this command.",
        declined: "You did not allow this program to start.",
        off: "Off: the character does not use its tools.",
        signin: "This service needs a sign-in. Sign in opens the service's own approval page in your browser.",
        signing_in: "Finish the approval in your browser.",
      },
      signinEnd: {
        expired: "The sign-in has run out. Sign in again.",
        denied: "Access was not approved on the server's page.",
        cancelled: "The sign-in was cancelled.",
        timeout: "The sign-in took longer than ten minutes and has expired.",
        registration: "This server does not let apps register themselves, so this sign-in cannot work. If it gives you an access token, enter it as an Authorization header.",
        failed: "The server did not complete the sign-in.",
      },
      signin: "Sign in",
      signinOpen: "If the browser did not open, open the approval page here",
      signinCancel: "Cancel",
      signout: "Sign out",
      notRevoked: "Signed out on this computer, but the server did not confirm that it ended the access. If needed, end it in your account settings there.",
      cut: "This server lists more than the limit of {n} tools; the rest were not loaded.",
      errors_state: {
        not_found: "The program “{what}” was not found. Install it, or check the command.",
        exited: "The program stopped.",
        refused: "The address could not be reached. Check it and the network.",
        timeout: "The server did not answer in time.",
        http: "The server answered with an error. Check the address and headers.",
        protocol: "The program did not answer as an MCP server. Check that the command starts one.",
        closed: "The connection closed.",
        missing_value: "These values are missing: {what}. Fill them in below.",
        elevated: "Mibo is running as administrator, so it starts no program here. Open Mibo again without administrator rights.",
        other: "It did not connect.",
      },
      runtimeNeed: "This command needs {title}, which this computer does not have. Mibo can download {title} {version} (about {size} MB, from {source}) into its own folder, used only by the servers Mibo starts; other programs are not affected.",
      runtimeGet: "Download {title}",
      runtimeGetting: "Downloading {title} (about {size} MB)…",
      runtimeErrors: { runtime_fetch_failed: "The download did not finish. Check the network connection and try again.",
        runtime_hash_mismatch: "The download did not match the checksum its maker published and was discarded. Try again later.",
        runtime_write_failed: "Mibo's folder could not be written to. Check the free disk space and try again." },
      retryIn: "Trying again in {s} s.",
      retry: "Retry",
      output: "Server output",
      once: "Allow this time",
      always: "Always allow",
      no: "Don't allow",
      allowAgain: "Allow it to start",
      alwaysStart: "This command is always allowed to start.",
      folder: "Working folder: {path}",
      values: "Saved values",
      addressKey: "Key in the address",
      valueSet: "Saved",
      valueMissing: "Not filled in",
      valueSave: "Save",
      valuePlaceholder: "New value",
      tools: "Tools ({n})",
      mode: { auto: "Auto review", always: "Always allow", never: "Never" },
      modeLabel: (name) => `Permission for ${name}`,
      readonlyHint: "Its maker marks it read-only",
      destructiveHint: "Its maker marks it as able to delete or overwrite data",
      changed: "Changed: asks again",
      groupRead: (n) => `Read-only tools (${n})`,
      groupReadText: "Tools their maker marks read-only, and not as deleting data. The maker writes that mark; Mibo cannot check it.",
      groupWrite: (n) => `Tools that write or delete (${n})`,
      groupWriteText: "The other tools, which may make, change or delete data. Tools their maker marks as deleting data ask every time.",
      logFold: (n) => (n ? `Call record (${n})` : "Call record"),
      logNone: "Nothing recorded yet.",
      logClear: "Clear the record",
      logClearAsk: "Clear the call record?",
      logClearText: "The record is deleted and cannot be brought back.",
      outcome: { done: "Done", error: "The tool reported an error", failed: "Failed", declined: "Declined", unanswered: "Not answered",
        never: "Blocked", changed: "The tool changed; not called", start_once: "Start allowed (this time)",
        start_always: "Start allowed (always)", start_no: "Start declined", refused: "Turned down by review" },
      how: { always: "always allowed", once: "allowed this time", chat: "allowed for this conversation",
        this_chat: "already allowed for this conversation", review: "passed by review" },
      importOpen: "Import from other apps",
      importTitle: "Connectors in other apps",
      importHelp: "These connectors are in the configurations of Claude, Cursor, VS Code and Cherry Studio on this computer. Importing copies them into Mibo and leaves the originals as they are; their values go to the system's credential store.",
      importLooking: "Looking",
      importNone: "No connectors were found in the configurations of Claude, Cursor, VS Code or Cherry Studio.",
      importGo: (n) => (n ? `Import selected (${n})` : "Import selected"),
      importing: "Importing",
      importClose: "Close",
      importHave: "Already added",
      importTaken: "A connector of this name is here; it gets another name",
      importOff: "Off in that app; it stays off",
      importValues: (n) => `${n} saved ${n === 1 ? "value" : "values"}`,
      importProblems: { unreadable: "The file could not be read", variables: "It uses that app's own variables and cannot be imported",
        tools_bad_snippet: "The configuration cannot be read", tools_bad_url: "The address is not valid", tools_bad_name: "The name is not valid" },
      search: "Search names or uses",
      allCategories: "All",
      shippedLine: (n) => `${n} chosen connectors`,
      noMatch: "No chosen connector matches “{q}”.",
      noMatchCategory: "No connectors in this category yet.",
      clear: "Clear the search",
      registryTitle: "Official MCP Registry",
      registryNote: "These come from the official MCP Registry, matched by name, and Mibo has not chosen them. Check the publisher before installing.",
      registryLoading: "Searching the official MCP Registry",
      registryNone: "No entry in the official MCP Registry has “{q}” in its name.",
      badgeRemote: "Remote",
      badgeLocal: "Runs here",
      badgeSignin: "Sign-in",
      badgeKey: "Needs a key",
      badgeSync: "Sync",
      install: "Install",
      installing: "Installing",
      installed: "Installed",
      installedDone: "Added “{name}”; its state is shown below.",
      back: "All connectors",
      way: "How it runs",
      ways: { remote: "Remote service", npm: "On this computer (Node.js)", pypi: "On this computer (Python)" },
      runs: "What it runs",
      needs: "Needs",
      needsSignin: "A sign-in to your account there, in the browser, once it is installed",
      needsNode: "Node.js; if this computer does not have it, it can be downloaded once installed",
      needsUv: "uv (runs Python programs); if this computer does not have it, it can be downloaded once installed",
      needsKey: "The key below",
      needsNothing: "Nothing else",
      publisher: "Publisher",
      source: "Source",
      licence: "Licence",
      localNote: "This is a program that runs on this computer. Before it first starts, Mibo asks you to allow its exact command.",
      remoteNote: "This is a service its provider runs; tool arguments and results pass through the provider.",
      form: "What it needs from you",
      required: "Required",
      optional: "Optional",
      getKey: "Get a key",
      secretSaved: "Keys are kept in the system's credential store; they are never shown again and never sent to the model.",
      syncTitle: "Sync ModelScope's hosted services",
      syncText: "With your ModelScope access token, Mibo adds every hosted service you turned on in ModelScope's MCP square. Sync again to add ones turned on later.",
      syncToken: "Access token",
      syncSaved: "The access token is saved; leave this empty to sync with it.",
      syncGo: "Sync",
      syncing: "Syncing",
      synced: (n) => `${n} hosted ${n === 1 ? "service" : "services"} synced.`,
      syncNone: "No hosted service is turned on at ModelScope yet. Turn one on in its MCP square, then sync here.",
      errors: {
        tools_bad_snippet: "That configuration cannot be read. Paste JSON with a command or a url, an https:// address, or the command that starts it.",
        tools_bad_name: "A name is 1 to 40 letters, digits, dots, dashes or underscores.",
        tools_bad_url: "The address must start with http:// or https:// and hold no user name or password.",
        tools_name_taken: "A connector with that name is already there.",
        tools_too_many: "No more connectors can be added.",
        tools_unknown_server: "That connector is no longer there.",
        tools_unknown_tool: "That tool is no longer there.",
        tools_write_failed: "The configuration file could not be written.",
        tools_file_damaged: "mcp.json in the home folder cannot be read. Correct it or remove it.",
        tools_required: "Fill in every required field.",
        market_gone: "The catalog no longer lists it.",
        market_search_failed: "The official MCP Registry could not be searched. Check the network and try again.",
        market_sync_failed: "It could not sync. Check the access token and the network, then try again.",
        file_too_large: "The file is too large.",
      },
    },
  };
  const MAX_FILE = 200_000;
  const SVG = "http://www.w3.org/2000/svg";
  const MARKS = {
    search: "M10.5 4a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM15.3 15.3L20 20",
    back: "M15 6l-6 6 6 6",
    plug: "M9 3.5v4M15 3.5v4M6.5 7.5h11v3a5.5 5.5 0 0 1-11 0zM12 16v4.5",
    check: "M5 12.5l4.2 4.2L19 7",
  };
  // A saved secret's reference in an address, as the page shows it.
  const HIDDEN = /\$\{MIBO_MCP_[A-Z0-9_]+\}/g;

  window.paSetup.part("tools", (kit) => {
    const { el } = kit;
    const t = kit.words(WORDS);
    const ctx = kit.ctx;
    const zh = kit.lang === "zh";
    const node = kit.section("tools", t.hint);
    let view = null;
    let market = null;
    let timer = null;
    let shown = "installed";
    const query = { text: "", category: "" };
    let detailOf = null;

    function mark(name, cls) {
      const svg = document.createElementNS(SVG, "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      svg.setAttribute("class", cls);
      const path = document.createElementNS(SVG, "path");
      path.setAttribute("d", MARKS[name]);
      svg.append(path);
      return svg;
    }
    function link(text, href) {
      const a = el("a", "setup-link", text);
      a.href = href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      return a;
    }
    const both = (pair) => (zh ? pair.zh || pair.en : pair.en || pair.zh) || "";

    // ---- The two views
    const head = el("div", "skm-head");
    const tabs = kit.choices(t.views, [["installed", t.installedTab], ["market", t.marketTab]], (value) => switchTo(value));
    head.append(tabs.group);
    const installedPane = el("div", "skm-pane");
    const marketPane = el("div", "skm-pane");
    node.append(head, installedPane, marketPane);

    // ================================================================ installed

    const toolbar = el("div", "skm-toolbar");
    const countLine = el("p", "skm-count");
    const updateAll = kit.button("setup-button-primary setup-button-sm", "", () => updateEvery());
    updateAll.hidden = true;
    const importOpen = kit.button("setup-button-secondary setup-button-sm", t.importOpen, () => lookElsewhere());
    const actions = el("div", "skm-toolbar-actions");
    actions.append(updateAll, importOpen);
    toolbar.append(countLine, actions);
    const installedNote = el("p", "setup-help skm-done");
    installedNote.setAttribute("role", "status");
    installedNote.hidden = true;

    const importCard = kit.card(t.importTitle);
    importCard.hidden = true;
    const importList = el("div", "setup-options");
    const importError = kit.errorLine();
    const importGo = kit.button("setup-button-primary setup-button-sm", t.importGo(0), () => importChosen());
    const importClose = kit.button("setup-button-ghost setup-button-sm", t.importClose, () => {
      importCard.hidden = true;
    });
    const importButtons = el("div", "skm-row");
    importButtons.append(importGo, importClose);
    importCard.append(el("p", "setup-help", t.importHelp), importList, importError, importButtons);

    const listCard = kit.card(t.listTitle);
    const notes = el("div");
    const list = el("div", "setup-options");
    const listError = kit.errorLine();
    listCard.append(notes, list, listError);

    // Pasting: a configuration, an address or a command, for a server the market does not have.
    const addFold = kit.fold(t.addTitle);
    addFold.node.classList.add("skm-add");
    const paste = el("textarea", "setup-input setup-textarea");
    paste.spellcheck = false;
    paste.placeholder = t.pastePlaceholder;
    paste.setAttribute("aria-label", t.addTitle);
    const picker = el("input");
    picker.type = "file";
    picker.hidden = true;
    picker.accept = ".json,application/json";
    const addButton = kit.button("setup-button-primary setup-button-sm", t.add, () => adding(addButton, { add: paste.value }));
    const pickButton = kit.button("setup-button-secondary setup-button-sm", t.pickFile, () => picker.click());
    const addRow = el("div", "setup-shell-add");
    addRow.append(addButton, pickButton, picker);
    const addError = kit.errorLine();
    const addDone = el("p", "setup-help", t.added);
    addDone.hidden = true;
    addFold.body.append(el("p", "setup-help", t.addText), paste, addRow, el("p", "setup-help", t.secretNote), addError, addDone);

    // The record.
    const record = kit.fold(t.logFold(0));
    const logList = el("div", "setup-shell-log");
    const clear = kit.button("setup-button-secondary setup-button-sm", t.logClear, clearLog);
    const logError = kit.errorLine();
    record.body.append(logList, clear, logError);
    record.node.addEventListener("toggle", () => {
      if (record.node.open) loadLog();
    });
    installedPane.append(toolbar, installedNote, importCard, listCard, addFold.node, record.node);

    // ---- Talking to the page.
    // Allowing a program to start is a press only this window's own page can give (panels/tools.py own_page).
    async function ownPost(body) {
      const response = await fetch("/api/mcp", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json",
          "X-Mibo-Setup-Token": sessionStorage.getItem("mibo-setup-token") || "",
          "X-Mibo-Window": sessionStorage.getItem("mibo-frame") || "" },
        body: JSON.stringify(body),
      });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.error || "generic");
      return value;
    }

    async function change(what, line = listError, own = false) {
      line.hidden = true;
      try {
        view = own ? await ownPost(what) : await ctx.api("mcp", what);
        render();
        return true;
      } catch (error) {
        kit.failed(line, error, t);
        return false;
      }
    }

    // The market's answer carries both views as they now are.
    function took(value) {
      view = value.view;
      market = value.market;
      render();
      if (shown === "market") renderMarket();
    }

    function said(text) {
      installedNote.textContent = text;
      installedNote.hidden = !text;
    }

    async function adding(button, what) {
      addError.hidden = addDone.hidden = true;
      const label = button.querySelector(".setup-button-label").textContent;
      kit.busy(button, true, t.adding);
      if (await change(what, addError)) {
        addDone.hidden = false;
        paste.value = "";
      }
      kit.busy(button, false, label);
    }

    picker.addEventListener("change", async () => {
      const file = picker.files[0];
      picker.value = "";
      if (!file) return;
      if (file.size > MAX_FILE) return kit.failed(addError, new Error("file_too_large"), t);
      adding(pickButton, { add: await file.text() });
    });

    // ---- One server.
    // A runtime Mibo can fetch, as the page says it: its release, size in MB and source.
    function runtime(row) {
      const r = view.runtimes[row.need];
      return { ...r, size: Math.round(r.size / 1048576) };
    }

    function stateLine(row) {
      if (row.need) {
        const r = runtime(row);
        if (r.state === "fetching") return kit.fill(t.runtimeGetting, r);
        return [t.runtimeErrors[r.error], kit.fill(t.runtimeNeed, r)].filter(Boolean).join(zh ? "" : " ");
      }
      if (row.state === "signin") return [t.signinEnd[row.detail], t.state.signin].filter(Boolean).join(zh ? "" : " ");
      if (row.state !== "failed") return kit.fill(t.state[row.state] || "", { n: row.tools.length });
      const what = row.error === "missing_value" ? row.detail.replace(/MIBO_MCP_[A-Z0-9_]+/g, t.addressKey) : row.command.split(" ")[0];
      return kit.fill(t.errors_state[row.error] || t.errors_state.other, { what });
    }

    function versionLine(row) {
      const meta = el("div", "skm-meta");
      if (row.version) meta.append(el("span", "", kit.fill(t.version, { v: row.version })));
      if (row.unpinned) meta.append(el("span", "", t.unpinned));
      if (row.latest) {
        meta.append(el("span", "skm-badge skm-badge-new", kit.fill(t.updateReady, { v: row.latest })));
        const go = kit.button("setup-button-link", t.update, async () => {
          said("");
          kit.busy(go, true, t.updating);
          try {
            took(await ctx.api("mcp/market", { update: row.name }));
            said(t.updated);
          } catch (error) {
            kit.busy(go, false, t.update);
            kit.failed(listError, error, t);
          }
        });
        meta.append(go);
      }
      return meta.childElementCount ? meta : null;
    }

    function serverRow(row) {
      const box = el("div", "setup-option setup-mcp-server");
      box.dataset.server = row.name;
      box.style.flexDirection = "column";
      box.style.alignItems = "stretch";
      const top = el("div", "setup-option");
      top.style.padding = "0";
      const words = el("div", "setup-option-words");
      const title = el("p", "setup-option-name", row.name);
      const pill = el("span", `setup-pill ${row.state === "connected" ? "setup-pill-up" : row.state === "failed" || row.state === "signin" ? "setup-pill-down" : ""}`);
      pill.append(el("span", "setup-pill-dot"), el("span", "", t.pill[row.state] || row.state));
      pill.style.marginLeft = "8px";
      title.append(pill);
      words.append(title, el("p", "setup-option-text", stateLine(row)));
      const meta = versionLine(row);
      if (meta) words.append(meta);
      const switches = el("div", "setup-option-actions");
      const on = el("input", "setup-switch");
      on.type = "checkbox";
      on.setAttribute("role", "switch");
      on.setAttribute("aria-label", t.enable(row.name));
      on.checked = !row.disabled;
      on.addEventListener("change", () => change({ server: row.name, disabled: !on.checked }));
      switches.append(on, kit.iconButton("setup-word-off", "cross", t.remove(row.name), () => removeServer(row)));
      top.append(words, switches);
      box.append(top);
      // What it runs, or where it is.
      box.append(el("code", "setup-command", row.kind === "program" ? row.command : row.url.replace(HIDDEN, "…")));
      if (row.kind === "program") box.append(el("p", "setup-help", kit.fill(t.folder, { path: row.folder })));
      if (row.cut) box.append(el("p", "setup-help", kit.fill(t.cut, { n: row.cut })));
      if (row.always_start) box.append(el("p", "setup-help", t.alwaysStart));
      // What to do now.
      const line = kit.errorLine();
      const buttons = el("div", "setup-shell-add");
      const add = (cls, text, run) => buttons.append(kit.button(`${cls} setup-button-sm`, text, run));
      if (row.state === "waiting" || row.state === "declined") {
        add("setup-button-primary", row.state === "declined" ? t.allowAgain : t.once, () => change({ start: row.name, answer: "once" }, line, true));
        add("setup-button-secondary", t.always, () => change({ start: row.name, answer: "always" }, line, true));
        if (row.state === "waiting") add("setup-button-ghost", t.no, () => change({ start: row.name, answer: "no" }, line, true));
      }
      if (row.state === "signin") add("setup-button-primary", t.signin, () => change({ signin: row.name, lang: kit.lang }, line, true));
      if (row.state === "signing_in") {
        if (row.signin_url) {
          const where = el("p", "setup-help");
          where.append(link(t.signinOpen, row.signin_url));
          box.append(where);
        }
        add("setup-button-secondary", t.signinCancel, () => change({ signin_cancel: row.name }, line));
      }
      if (row.state === "connected" && row.signed_in) add("setup-button-ghost", t.signout, () => signOut(row, line));
      // A missing npx or uvx: one press downloads Node.js or uv (runtimes.py), and the server goes on by itself.
      if (row.need) {
        if (runtime(row).state !== "fetching")
          add("setup-button-primary", kit.fill(t.runtimeGet, runtime(row)), () => change({ runtime: row.need }, line, true));
      } else if (row.state === "failed") {
        if (row.retry_in) box.append(el("p", "setup-help", kit.fill(t.retryIn, { s: row.retry_in })));
        if (row.detail && row.error !== "missing_value" && row.error !== "not_found") {
          const out = kit.fold(t.output);
          out.body.append(el("pre", "setup-shell-pre", row.detail));
          box.append(out.node);
        }
        add("setup-button-secondary", t.retry, () => change({ retry: row.name }, line));
      }
      if (buttons.childElementCount) box.append(buttons);
      // Saved values: the names only; a new value may be written, never read back.
      if (row.values.length) {
        const fold = kit.fold(t.values);
        fold.node.open = row.error === "missing_value";
        for (const value of row.values) {
          const input = kit.input();
          input.type = "password";
          input.placeholder = t.valuePlaceholder;
          const label = value.kind === "url" ? t.addressKey : value.name;
          input.setAttribute("aria-label", label);
          const save = kit.button("setup-button-secondary setup-button-sm", t.valueSave, () =>
            change({ value: { server: row.name, kind: value.kind, name: value.name, value: input.value } }, line));
          const r = kit.optionRow(label, value.set ? t.valueSet : t.valueMissing);
          const inline = el("div", "setup-shell-add");
          inline.append(input, save);
          r.words.append(inline);
          fold.body.append(r.row);
        }
        box.append(fold.node);
      }
      // Its tools in two groups, read-only and write or delete (Claude's connectors), each group set at once, and
      // each tool asking, always allowed or never; what writes is never always allowed as a group, nor a tool
      // marked destructive.
      if (row.tools.length) {
        const fold = kit.fold(kit.fill(t.tools, { n: row.tools.length }));
        for (const group of ["read", "write"]) {
          const tools = row.tools.filter((tool) => tool.group === group);
          if (!tools.length) continue;
          const name = group === "read" ? t.groupRead(tools.length) : t.groupWrite(tools.length);
          const entries = Object.entries(t.mode).filter(([mode]) => group === "read" || mode !== "always");
          const pickGroup = kit.choices(t.modeLabel(name), entries, (mode) =>
            change({ group: { server: row.name, group, mode } }, line));
          pickGroup.set(row.groups[group]);
          const head = kit.optionRow(name, group === "read" ? t.groupReadText : t.groupWriteText);
          head.words.append(pickGroup.group);
          fold.body.append(head.row);
          for (const tool of tools) {
            const marks = [tool.readonly ? t.readonlyHint : "", tool.destructive ? t.destructiveHint : "",
              tool.changed ? t.changed : ""].filter(Boolean).join(zh ? "；" : "; ");
            const pickMode = kit.choices(t.modeLabel(tool.name),
              Object.entries(t.mode).filter(([mode]) => !tool.destructive || mode !== "always"), (mode) =>
                change({ tool: { server: row.name, tool: tool.name, mode } }, line));
            pickMode.set(tool.mode);
            const r = kit.optionRow(tool.title && tool.title !== tool.name ? `${tool.title} · ${tool.name}` : tool.name, tool.description);
            if (marks) r.words.append(el("p", "setup-help", marks));
            r.words.append(pickMode.group);
            fold.body.append(r.row);
          }
        }
        box.append(fold.node);
      }
      box.append(line);
      return box;
    }

    function render() {
      countLine.textContent = t.count(view.servers.length);
      updateAll.hidden = !view.updates;
      kit.relabel(updateAll, t.updateAll(view.updates));
      notes.replaceChildren();
      if (view.elevated) notes.append(el("p", "setup-error", t.elevated));
      if (view.bad.length) notes.append(el("p", "setup-error", kit.fill(t.bad, { names: view.bad.join(", ") })));
      // A fold the person opened stays open across a refresh.
      const open = new Set([...list.querySelectorAll("details[open] > summary")].map((s) => s.closest("[data-server]").dataset.server + s.textContent));
      if (!view.servers.length) list.replaceChildren(el("p", "setup-help", t.none));
      else list.replaceChildren(...view.servers.map(serverRow));
      for (const summary of list.querySelectorAll("details > summary"))
        if (open.has(summary.closest("[data-server]").dataset.server + summary.textContent)) summary.parentElement.open = true;
      // While one is starting or about to retry, the page asks again.
      clearTimeout(timer);
      const fetching = Object.values(view.runtimes).some((r) => r.state === "fetching");
      if ((fetching || view.servers.some((row) => row.state === "starting" || row.state === "signing_in" || row.retry_in)) && !node.closest("[hidden]"))
        timer = setTimeout(load, 2000);
    }

    async function load() {
      try {
        view = await ctx.api("mcp");
        listError.hidden = true;
        render();
      } catch (error) {
        kit.failed(listError, error, t);
      }
    }

    // Signing out ends the access at the server first; a server that did not confirm it is said once.
    async function signOut(row, line) {
      if (await change({ signout: row.name }, line, true) && view.revoked === false)
        notes.append(el("p", "setup-help", t.notRevoked));
    }

    async function removeServer(row) {
      const sure = await ctx.confirm({ title: kit.fill(t.removeAsk, { name: row.name }), text: t.removeText, ok: t.removeOk });
      if (sure && await change({ remove: row.name })) market = null;
    }

    async function updateEvery() {
      said("");
      const label = updateAll.querySelector(".setup-button-label").textContent;
      kit.busy(updateAll, true, t.updating);
      try {
        took(await ctx.api("mcp/market", { update_all: true }));
        said(t.updated);
      } catch (error) {
        kit.failed(listError, error, t);
      } finally {
        kit.busy(updateAll, false, label);
        render();
      }
    }

    // ---- Servers other apps keep for this account: read only on the press.
    let found = [];
    const chosen = new Set();
    async function lookElsewhere() {
      importCard.hidden = false;
      importError.hidden = true;
      chosen.clear();
      importList.replaceChildren(el("p", "setup-help", t.importLooking));
      importGo.hidden = true;
      kit.busy(importOpen, true, t.importLooking);
      try {
        found = (await ctx.api("mcp/market", { apps: true })).found;
        renderFound();
      } catch (error) {
        importList.replaceChildren();
        kit.failed(importError, error, t);
      } finally {
        kit.busy(importOpen, false, t.importOpen);
      }
    }
    function renderFound() {
      importGo.hidden = !found.some((row) => row.key && !row.problem && !row.have);
      kit.relabel(importGo, t.importGo(0));
      if (!found.length) return importList.replaceChildren(el("p", "setup-help", t.importNone));
      importList.replaceChildren(...found.map((row, n) => {
        const line = kit.switchRow(`mcp-import-${n}`, row.original || row.app, row.where || row.path, (box) => {
          if (box.checked) chosen.add(row.key);
          else chosen.delete(row.key);
          kit.relabel(importGo, t.importGo(chosen.size));
        });
        // A tick each, not a switch: these are chosen, not turned on.
        line.box.className = "skm-check";
        line.box.removeAttribute("role");
        line.about.classList.add("skm-clamp", "skm-path");
        const facts = [row.app, row.values?.length ? t.importValues(row.values.length) : ""];
        if (row.off) facts.push(t.importOff);
        if (row.taken) facts.push(t.importTaken);
        line.words.append(el("p", "skm-meta", facts.filter(Boolean).join(" · ")));
        const why = row.problem ? t.importProblems[row.problem] || t.importProblems.tools_bad_snippet : row.have ? t.importHave : "";
        if (why) {
          line.box.disabled = true;
          line.words.append(el("p", row.problem ? "setup-error" : "skm-meta", why));
        }
        return line.row;
      }));
    }
    async function importChosen() {
      if (!chosen.size) return;
      importError.hidden = true;
      kit.busy(importGo, true, t.importing);
      try {
        const value = await ctx.api("mcp/market", { import: [...chosen] });
        took(value);
        importCard.hidden = true;
        said(t.addedNames(value.added));
      } catch (error) {
        kit.failed(importError, error, t);
      } finally {
        kit.busy(importGo, false, t.importGo(chosen.size));
      }
    }

    // ================================================================== market

    const controls = el("div", "skm-controls");
    const searchWrap = el("label", "skm-search");
    const search = kit.input("skm-search-input");
    search.type = "search";
    search.placeholder = t.search;
    search.setAttribute("aria-label", t.search);
    searchWrap.append(mark("search", "skm-search-icon"), search);
    const chips = el("div", "skm-chips");
    chips.setAttribute("role", "toolbar");
    const status = el("p", "skm-status");
    controls.append(searchWrap, chips, status);
    const marketError = kit.errorLine();
    const grid = el("div", "skm-grid");
    const registry = el("section", "skm-pane");
    const detail = el("div", "skm-detail");
    detail.hidden = true;
    marketPane.append(controls, marketError, grid, registry, detail);

    let typing = null;
    search.addEventListener("input", () => {
      clearTimeout(typing);
      query.text = search.value.trim();
      renderGrid();
      // The registry is asked once the typing stops.
      typing = setTimeout(searchRegistry, 450);
    });

    async function loadMarket() {
      marketError.hidden = true;
      try {
        market = await ctx.api("mcp/market");
        renderMarket();
      } catch (error) {
        kit.failed(marketError, error, t);
      }
    }

    function renderMarket() {
      if (detailOf) return renderDetail(market.servers.find((one) => one.id === detailOf.id) || detailOf);
      renderChips();
      renderGrid();
    }

    function renderChips() {
      // The categories skills use too, named in the locales.
      const names = kit.words("setup_skills").categories;
      const used = new Set(market.servers.map((one) => one.category));
      const keys = ["", ...Object.keys(market.categories || {}).filter((key) => used.has(key))];
      chips.replaceChildren(...keys.map((key) => {
        const chip = el("button", "skm-chip", key ? names[key] : t.allCategories);
        chip.type = "button";
        chip.setAttribute("aria-pressed", String(query.category === key));
        chip.addEventListener("click", () => {
          query.category = key;
          renderChips();
          renderGrid();
        });
        return chip;
      }));
    }

    // An entry fits when every word typed is in its names, uses, tags or publisher; a Chinese word not there
    // whole fits by any two characters of it ("查询地图" finds 地图).
    function fits(one, words) {
      const text = [one.name, one.id, one.title.zh, one.title.en, one.about.zh, one.about.en, one.publisher, ...one.tags]
        .join(" ").toLowerCase();
      return words.every((word) => text.includes(word)
        || (/[㐀-鿿]/.test(word) && [...word].some((_, i) => i + 1 < word.length && text.includes(word.slice(i, i + 2)))));
    }

    function listed() {
      const words = query.text.toLowerCase().split(/\s+/).filter(Boolean);
      return market.servers.filter((one) => (!query.category || one.category === query.category) && fits(one, words));
    }

    function badges(one) {
      const row = el("div", "skm-badges");
      const way = one.ways[0];
      if (one.sync) row.append(el("span", "skm-badge", t.badgeSync));
      else if (way) row.append(el("span", "skm-badge", way.kind === "remote" ? t.badgeRemote : t.badgeLocal));
      if (way?.signin) row.append(el("span", "skm-badge", t.badgeSignin));
      if (one.sync || way?.inputs.some((row) => row.secret && row.required)) row.append(el("span", "skm-badge", t.badgeKey));
      return row;
    }

    // Publishers' own pictures from their GitHub accounts, fetched once by the app; a plain mark until then.
    const pictures = new Map();
    function publisherMark(one, size) {
      const box = el("span", `skm-mark skm-mark-${size}`);
      box.append(mark("plug", "skm-mark-icon"));
      if (!one.owner) return box;
      if (!pictures.has(one.owner)) pictures.set(one.owner, ctx.picture(`/api/skills/market/mark/${encodeURIComponent(one.owner)}`).catch(() => ""));
      pictures.get(one.owner).then((url) => {
        if (!url) return;
        const img = el("img");
        img.alt = "";
        img.src = url;
        box.replaceChildren(img);
      });
      return box;
    }

    function installedMark() {
      const done = el("span", "skm-installed");
      done.append(mark("check", "skm-installed-icon"), el("span", "", t.installed));
      return done;
    }

    function cardOf(one) {
      const card = el("article", "skm-card");
      card.tabIndex = 0;
      card.setAttribute("role", "button");
      card.setAttribute("aria-label", both(one.title));
      const top = el("div", "skm-card-top");
      const names = el("div", "skm-card-names");
      names.append(el("h4", "skm-card-title", both(one.title)), el("p", "skm-card-by", one.publisher));
      top.append(publisherMark(one, "sm"), names);
      const foot = el("div", "skm-card-foot");
      foot.append(badges(one));
      if (one.installed) foot.append(installedMark());
      card.append(top, el("p", "skm-card-about", both(one.about)), foot);
      card.addEventListener("click", () => openDetail(one));
      card.addEventListener("keydown", (event) => {
        if ((event.key === "Enter" || event.key === " ") && event.target === card) {
          event.preventDefault();
          openDetail(one);
        }
      });
      return card;
    }

    function renderGrid() {
      detail.hidden = true;
      controls.hidden = grid.hidden = registry.hidden = false;
      const rows = listed();
      status.textContent = query.text ? "" : t.shippedLine(market.servers.length);
      if (!rows.length) {
        const empty = el("div", "skm-empty");
        empty.append(el("p", "", query.text ? kit.fill(t.noMatch, { q: query.text }) : t.noMatchCategory));
        if (query.text) {
          empty.append(kit.button("setup-button-link", t.clear, () => {
            search.value = query.text = "";
            renderGrid();
            searchRegistry();
          }));
        }
        return grid.replaceChildren(empty);
      }
      grid.replaceChildren(...rows.map(cardOf));
    }

    // Beyond the chosen list: the official registry, by name, for a Latin word of three letters or more (its least).
    let asked = "";
    async function searchRegistry() {
      const q = query.text;
      asked = q;
      if (q.trim().length < 3 || !/^[\x20-\x7e]+$/.test(q)) return registry.replaceChildren();
      registry.replaceChildren(el("h4", "skm-card-title", t.registryTitle), el("p", "setup-help", t.registryLoading));
      try {
        const rows = (await ctx.api(`mcp/market/search?q=${encodeURIComponent(q)}`)).servers;
        if (asked !== q) return;
        const box = el("div", "skm-grid");
        box.append(...rows.map(cardOf));
        registry.replaceChildren(el("h4", "skm-card-title", t.registryTitle),
          el("p", "setup-help", rows.length ? t.registryNote : kit.fill(t.registryNone, { q })), ...(rows.length ? [box] : []));
      } catch (error) {
        if (asked !== q) return;
        const line = kit.errorLine();
        kit.failed(line, error, t);
        registry.replaceChildren(el("h4", "skm-card-title", t.registryTitle), line);
      }
    }

    // ---- One entry, in full, with its form.
    function openDetail(one) {
      detailOf = one;
      renderDetail(one);
      node.closest(".setup-page")?.parentElement?.scrollTo({ top: 0 });
    }
    function closeDetail() {
      detailOf = null;
      renderGrid();
    }

    function fact(dl, name, ...values) {
      const dd = el("dd");
      for (const value of values) if (value) dd.append(typeof value === "string" ? el("p", "", value) : value);
      dl.append(el("dt", "", name), dd);
    }

    let wayAt = 0;
    function renderDetail(one) {
      controls.hidden = grid.hidden = registry.hidden = true;
      marketError.hidden = true;
      detail.hidden = false;
      if (wayAt >= one.ways.length) wayAt = 0;
      const back = el("button", "skm-back");
      back.type = "button";
      back.append(mark("back", "skm-back-icon"), el("span", "", t.back));
      back.addEventListener("click", closeDetail);

      const top = el("div", "skm-detail-top");
      const names = el("div", "skm-detail-names");
      names.append(el("h3", "skm-detail-title", both(one.title)));
      const by = el("p", "skm-detail-by");
      by.append(el("span", "", one.publisher), el("span", "", one.id));
      names.append(by);
      const act = el("div", "skm-detail-act");
      if (one.installed) act.append(installedMark());
      top.append(publisherMark(one, "lg"), names, act);

      const lead = el("div", "skm-detail-lead");
      if (!one.reviewed) lead.append(el("p", "skm-reason", t.registryNote));
      lead.append(el("p", "skm-detail-about", both(one.about)));

      const parts = [back, top, lead];
      if (one.sync) parts.push(...syncForm(one));
      else parts.push(...installForm(one));
      detail.replaceChildren(...parts);
    }

    function installForm(one) {
      const way = one.ways[wayAt];
      const dl = el("dl", "skm-facts");
      if (one.ways.length > 1) {
        const pickWay = kit.choices(t.way, one.ways.map((w, i) => [String(i), t.ways[w.kind]]), (value) => {
          wayAt = Number(value);
          renderDetail(one);
        });
        pickWay.set(String(wayAt));
        fact(dl, t.way, pickWay.group);
      } else {
        fact(dl, t.way, t.ways[way.kind]);
      }
      fact(dl, t.runs, el("code", "setup-command", way.where));
      const needs = [way.signin ? t.needsSignin : "", way.needs === "node" ? t.needsNode : way.needs === "uv" ? t.needsUv : "",
        way.inputs.some((row) => row.secret && row.required) ? t.needsKey : ""].filter(Boolean);
      fact(dl, t.needs, needs.length ? needs.join(zh ? "；" : "; ") : t.needsNothing);
      if (one.repo) fact(dl, t.source, link(one.repo.replace(/^https:\/\//, ""), one.repo));
      if (one.licence) fact(dl, t.licence, one.licence);
      const note = el("p", "setup-help", way.kind === "remote" ? t.remoteNote : t.localNote);

      // The form: one field per value the way asks for; secrets are typed and never shown again.
      const form = el("div", "skm-pane");
      const fields = way.inputs.map((row) => {
        const input = kit.input();
        if (row.secret) input.type = "password";
        if (row.default) input.placeholder = row.default;
        const help = [];
        const words = both(row.label);
        if (words && words !== row.name) help.push(el("span", "", words + " "));
        if (row.secret && one.get_key) help.push(link(t.getKey, one.get_key));
        const need = row.required ? t.required : t.optional;
        const field = kit.field(zh ? `${row.name}（${need}）` : `${row.name} (${need})`, input);
        if (help.length) field.help(help);
        return { row, field };
      });
      if (fields.length) {
        form.append(el("h4", "skm-card-title", t.form), ...fields.map((f) => f.field.wrap));
        if (way.inputs.some((row) => row.secret)) form.append(el("p", "setup-help", t.secretSaved));
      }
      const error = kit.errorLine();
      const go = kit.button("setup-button-primary", t.install, async () => {
        const values = {};
        for (const { row, field } of fields) {
          field.error("");
          values[row.id] = field.control.value;
          if (row.required && !field.control.value.trim()) field.error(t.errors.tools_required);
        }
        if (fields.some(({ row, field }) => row.required && !field.control.value.trim())) return;
        error.hidden = true;
        kit.busy(go, true, t.installing);
        try {
          const value = await ctx.api("mcp/market", { install: one.id, way: wayAt, values });
          detailOf = null;
          took(value);
          switchTo("installed");
          said(kit.fill(t.installedDone, { name: value.added[0] }));
        } catch (failure) {
          kit.busy(go, false, t.install);
          kit.failed(error, failure, t);
        }
      });
      const row = el("div", "skm-row");
      row.append(go);
      form.append(note, row, error);
      return [dl, form];
    }

    // ModelScope: its access token, and the services turned on there brought in.
    function syncForm(one) {
      const form = el("div", "skm-pane");
      const input = kit.input();
      input.type = "password";
      const help = [el("span", "", (market.synced ? t.syncSaved : "") + " ")];
      if (one.get_key) help.push(link(t.getKey, one.get_key));
      const field = kit.field(t.syncToken, input, help);
      const error = kit.errorLine();
      const done = el("p", "setup-help");
      done.hidden = true;
      const go = kit.button("setup-button-primary", t.syncGo, async () => {
        error.hidden = done.hidden = true;
        kit.busy(go, true, t.syncing);
        try {
          const value = await ctx.api("mcp/market", { modelscope: input.value });
          input.value = "";
          view = value.view;
          market = value.market;
          done.textContent = value.added.length ? t.synced(value.added.length) : t.syncNone;
          done.hidden = false;
        } catch (failure) {
          kit.failed(error, failure, t);
        } finally {
          kit.busy(go, false, t.syncGo);
        }
      });
      const row = el("div", "skm-row");
      row.append(go);
      form.append(el("h4", "skm-card-title", t.syncTitle), el("p", "setup-help", t.syncText), field.wrap,
        el("p", "setup-help", t.secretSaved), el("p", "setup-help", t.remoteNote), row, error, done);
      return [form];
    }

    // ---- The record.
    function saidRow(row) {
      const what = row.tool ? `${row.server} · ${row.tool}` : row.server;
      const how = row.how && t.how[row.how] ? ` (${t.how[row.how]})` : "";
      return `${t.outcome[row.outcome] || row.outcome}${how} · ${what}`;
    }

    async function loadLog() {
      try {
        const rows = (await ctx.api("mcp/log?n=60")).rows;
        record.summary.lastChild.textContent = t.logFold(rows.length);
        logError.hidden = true;
        clear.hidden = !rows.length;
        if (!rows.length) return logList.replaceChildren(el("p", "setup-help", t.logNone));
        logList.replaceChildren(...rows.map((row) => {
          const item = el("div", "setup-shell-entry");
          const at = row.at ? new Date(row.at * 1000).toLocaleString(zh ? "zh-CN" : "en") : "";
          item.append(el("p", "setup-option-name", saidRow(row)), el("p", "setup-help", at));
          if (row.args || row.out) {
            const more = kit.fold(t.output);
            more.body.append(el("pre", "setup-shell-pre", [row.args, row.out].filter(Boolean).join("\n\n")));
            item.append(more.node);
          }
          return item;
        }));
      } catch (error) {
        kit.failed(logError, error, t);
      }
    }

    async function clearLog() {
      const sure = await ctx.confirm({ title: t.logClearAsk, text: t.logClearText, ok: t.logClear });
      if (!sure) return;
      try {
        await ctx.api("mcp/log", { clear: true });
        loadLog();
      } catch (error) {
        kit.failed(logError, error, t);
      }
    }

    // ================================================================= views

    function switchTo(value) {
      shown = value;
      tabs.set(value);
      installedPane.hidden = value !== "installed";
      marketPane.hidden = value !== "market";
      if (value === "market" && !market) loadMarket();
      else if (value === "market") renderMarket();
    }

    // `place`: "market" or "market/<entry id>", as a link into this section names it.
    async function show(asked, place = "") {
      const [where, ...rest] = String(place || "").split("/");
      if (where === "market") {
        detailOf = null;
        if (!market) await loadMarket().catch(() => null);
        const one = market?.servers.find((entry) => entry.id === rest.join("/"));
        switchTo("market");
        if (one) openDetail(one);
      } else {
        switchTo(shown);
      }
      load();
      if (record.node.open) loadLog();
    }

    return { node, show };
  });
})();
