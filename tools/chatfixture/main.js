// A small chat program on Electron, shaped like a desktop chat client: a side bar with the program's own agent as a
// large round avatar on top, four named chats under it, a message box and a Send button. Every message sent is
// written to the log as one JSON line {"chat", "text"}, so a check reads what the program received.
//
//   electron tools/chatfixture --variant=named|bare --log=<file> --profile=<folder> [--chat=<name>|main] [--corner]
//                              [--title=<window title>]
//
// named: the avatar is a button with the accessible name "Main agent". bare: it is an image with no name in a
// clickable box, with the program's name under it. --corner shows the window small in a corner without taking the
// focus (showInactive), for reading its accessibility tree on someone's own desktop. --chat opens with that chat
// shown (main is the agent's), so each state can be read without pressing anything. --title names the window (the
// program a request names, as tools/everyday.py's Lumen).
const { app, BrowserWindow, ipcMain, screen } = require('electron');
const fs = require('fs');
const path = require('path');

const arg = (name, fallback) => {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
};
const variant = arg('variant', 'named');
const chat = arg('chat', 'Daily notes');
const log = arg('log', path.join(process.cwd(), 'chatfixture.log'));
const corner = process.argv.includes('--corner');
app.setPath('userData', arg('profile', path.join(path.dirname(log), 'chatfixture-profile')));

app.whenReady().then(() => {
  const area = screen.getPrimaryDisplay().workArea;
  const [wide, high] = corner ? [480, 340] : [760, 500];
  const win = new BrowserWindow({
    width: wide, height: high, x: area.x + area.width - wide, y: area.y + area.height - high, show: false,
    title: arg('title', 'Mibo test fixture'), autoHideMenuBar: true,
    webPreferences: { nodeIntegration: true, contextIsolation: false },
  });
  win.on('page-title-updated', (event) => event.preventDefault());
  win.loadFile(path.join(__dirname, 'index.html'), { query: { variant, chat } });
  win.once('ready-to-show', () => {
    if (corner) win.showInactive(); else win.show();
    process.stdout.write(JSON.stringify({ ready: true, pid: process.pid }) + '\n');
  });
  ipcMain.on('sent', (_event, chat, text) => fs.appendFileSync(log, JSON.stringify({ chat, text }) + '\n'));
});
app.on('window-all-closed', () => app.quit());
