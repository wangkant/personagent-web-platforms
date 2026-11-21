# Security policy

## Reporting a vulnerability

Report privately through GitHub: open the [Security tab](https://github.com/wangkant/mibo/security) and choose **Report a vulnerability**. Do not open a public issue.

Include the version (Settings › About, or `mibo --version`), how Mibo was installed, the operating system and the shortest steps that reproduce the problem. Do not include keys, tokens or real conversations.

A first reply follows within about a week. A confirmed problem is fixed in a release and credited in the advisory and the changelog, unless you prefer not to be named. Please allow time for the fix to ship before publishing details.

Only the latest release receives fixes.

## In scope

A way around any of the following is a vulnerability:

- **Approval.** An action with an effect outside the app (on a site, in a program, in files, by command or through an MCP server) that runs without its confirmation card or outside the rules set in Settings.
- **Secrets.** The character reading or typing a password, payment detail or verification code; a key or token reaching a log, a page, the model or another program.
- **The local page.** It listens on 127.0.0.1 and requires the token in the link the app opens. A request from another site, device or user that reaches its API.
- **Granted scope.** Reading or writing outside the folders, programs or sites the person granted.
- **Fetches.** A link or picture fetch that reaches a private network address.

## Out of scope

- A model saying something wrong.
- An attack that needs control of the computer or the home folder.
- Flaws in a model service, a website, a skill, an MCP server or another dependency. Report those to their authors.

## Data and network

| Destination | What is sent | When |
|---|---|---|
| The model service the person chose | Chat messages, relevant memory, and the text or pictures of pages and files used for a reply | Each model call |
| Fallback, vision and embedding endpoints | Parts of the same | Only when set in Settings or `settings.json` |
| Websites and public feeds | Ordinary page requests: signed out on walks, with the person's session in the built-in browser | When a page is read or operated |
| Tavily, or DuckDuckGo without a Tavily key | A short search query | When a reply needs a search |
| GitHub (`api.github.com`) | A request for the latest release; it carries only the version number | At most once a day; `UPDATE_CHECK_ENABLED=false` turns it off |
| GitHub, npm, PyPI, the MCP Registry | Catalog and version lookups | When the skill or connector market is opened, at most once a day |
| The skill's host; nodejs.org or GitHub for Node.js and uv | Downloads | When the person installs a skill or a connector that needs them |
| MCP servers the person added | What the character passes to their tools | When a tool is used |

| Kept | Where |
|---|---|
| Settings | `settings.json` in the home folder, readable only by its user; it never holds a key |
| Model, search, MCP and connector keys and tokens | The system credential store (Windows Credential Manager, macOS Keychain, Secret Service); on a system with none, `credentials.json` in the home folder, readable only by its user, and Settings › 本机 says so |
| Built-in browser sign-ins | A browser profile in the home folder (`runtime/desktop/browser`); saved passwords stay in the browser's own store and are never read by the app |
| Memory, chats and history | `runtime/` in the home folder |
