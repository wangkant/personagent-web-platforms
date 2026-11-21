"""Find colloquial Chinese in the app's interface text: python tools/copy_check.py

The app's own text is written, plain Chinese, the way a mainstream product
writes: no spoken verbs or particles, no "先问你", "做了什么" or "弄完了", and
the app does not speak as "我". This lists every string of the Chinese catalog
(locales/zh.json) and every Chinese string in the modules that hold text a
person sees which carries one of the spoken forms below, so it can be
rewritten. What the character itself says (its persona, its prompts, its lines)
and what the person says (suggested messages) is not interface text: in the
catalog it sits under VOICE_KEYS, in a module it is marked with `copy: voice`
in a comment on it or on the line above, and it is not read here.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "mibo"
# Where interface text lives; prompts and persona files are the character's voice.
PLACES = ("static/welcome", "panels", "web", "desk", "shell", "see", "skills", "review.py", "browsed.py", "browser.py",
          "window.py", "pacing.py", "endpoints.py", "abilities.py", "spend.py", "snip.py")
# Not interface text: marks, shipped skills, and the modules whose Chinese is words for the model or
# patterns that match what a person types.
SKIP = ("providermarks.js", "/builtin/",
        "desk/tool.py", "shell/tool.py", "skills/tool.py")
SPOKEN = (
    # Particles, and verbs of speech rather than of writing.
    r"[吧啦呀哦嘛呗]", r"呢[。？！”\"]", r"一下", r"看看|试试|瞧瞧|问问", r"看一眼|看不出|看不懂|读不懂", r"做了什么|干了什么|干什么|干嘛",
    r"[搞弄]", r"啥|咋", r"没找到|找不到", r"打不开|连不上|用不了|看不清|看不明白|看不见", r"没法", r"没有?做成|做完了?|做好了",
    r"记下|停下|留着|改回来|找回|忘掉", r"接着[做来]|再试", r"好了", r"照样|压根|反正", r"过一会儿|一会儿|这会儿",
    r"东西", r"不碰|碰到", r"亲口", r"(?<![识区类告级特性分个差辨])别[^人的名处墅]", r"要不要|行不行|好不好|有没有|都行", r"没问题", r"够多|很多|太[多大]",
    r"就(?:停|行|好)", r"不算", r"少看|想看|看过|看的(?:网页|内容)", r"画出来|说法|原样|自己挑|你自己", r"[说聊]点什么|回消息|刚回来",
    r"没有回应|不通", r"没有?人回答|没人", r"没有(?:做|成功|同意|允许|打开|等到)", r"还没有?", r"一小段|一两[条个]", r"来了几",
    r"口味",
    # The app speaking as a person to a person.
    r"先问你|问你", r"替你|帮你", r"等你", r"不用问|不用管", r"交给你|交还给你", r"我来|我[能在]", r"想在|想让", r"你在用|在用这个",
    r"它[会们]|给它|等它", r"点下面|下面的按钮|点这里", r"怎么对待",
    # Spoken pointing words where a written form exists.
    r"这台电脑", r"这(?:张|条|项|一栏)", r"这个(?:网站|程序|文件夹|网页|窗口)", r"(?:聊天|文件夹|浏览器|记事本|记录|设置|程序)里", r"这里|那里",
    r"收回", r"(?<![更修])改(?:名|账号|设置)", r"停在了|运行了|做了一次", r"做事|要做的事",
    # The product's name, and no Latin word glued to Chinese.
    r"觅波|覓波", r"(?:Mibo|OpenRouter|DeepSeek|Windows|PowerShell)[一-鿿]|[一-鿿](?:Mibo|OpenRouter|DeepSeek|Windows|PowerShell)",
)
CATALOG = ROOT / "locales" / "zh.json"
# Catalog keys the character or the person says, and the namespaces of the command line, which is not interface text here.
VOICE_KEYS = ("activity.goOnSaid", "chat.ideas", "chat.ideasPlain", "onboard.saying", "onboard.sayingNoModel",
              "onboard.bye", "onboard.findsSaying", "onboard.findsLooking", "onboard.heard", "petbox.asks")
NOT_PAGES = ("cli", "start", "tray", "petshot", "catalog")
CJK = re.compile(r"[一-鿿]")
VOICE = "copy: voice"
RULES = [re.compile(p) for p in SPOKEN]
QUOTES = "\"'`"


def files():
    for place in PLACES:
        path = ROOT / place
        found = [path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []
        for f in found:
            rel = f.relative_to(ROOT).as_posix()
            if f.suffix in (".js", ".py") and not any(s in rel for s in SKIP):
                yield f, rel


def strings(text: str):
    """(line number, literal) for each quoted literal with Chinese in it. One pass over the text, character by
    character: no pattern that can back up. A quote opens a literal; a backslash takes the next character with it;
    a single or double quote ends at its line's end at the latest, a backtick may run over lines."""
    lines = text.splitlines()
    line, i, n = 1, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        elif text.startswith(('"""', "'''"), i):
            # A Python docstring or a long text for the model: not a label on a page.
            end = text.find(text[i:i + 3], i + 3)
            end = n if end < 0 else end + 2
            line += text.count("\n", i, end)
            i = end
        elif ch in QUOTES:
            start_line, j, out = line, i + 1, []
            while j < n and text[j] != ch and (ch == "`" or text[j] != "\n"):
                if text[j] == "\\" and j + 1 < n:
                    out.append(text[j:j + 2])
                    j += 1
                else:
                    out.append(text[j])
                if text[j] == "\n":
                    line += 1
                j += 1
            literal = "".join(out)
            here = lines[start_line - 1] if start_line <= len(lines) else ""
            above = lines[start_line - 2] if start_line > 1 else ""
            comment = here.lstrip().startswith(("#", "//", "*", "/*"))
            if CJK.search(literal) and not comment and VOICE not in here and VOICE not in above:
                yield start_line, literal
            # An unclosed quote (an apostrophe in a comment) stops at the line's end: leave that newline to be counted.
            i = j - 1 if j < n and text[j] == chr(10) and ch != "`" else j
        i += 1


def catalog(tree, key=""):
    """(key, text) for each string of the catalog; a list's items share its key."""
    if isinstance(tree, dict):
        for name, value in tree.items():
            yield from catalog(value, f"{key}.{name}" if key else name)
    elif isinstance(tree, list):
        for value in tree:
            yield from catalog(value, key)
    elif isinstance(tree, str):
        yield key, tree


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    hits = 0
    for key, text in catalog(json.loads(CATALOG.read_text(encoding="utf-8"))):
        if key.split(".")[0] in NOT_PAGES or key.startswith(VOICE_KEYS):
            continue
        found = [m.group(0) for rule in RULES for m in [rule.search(text)] if m]
        if found:
            hits += 1
            print(f"locales/zh.json:{key}: [{' '.join(found)}] {' '.join(text.split())[:110]}")
    for f, rel in files():
        for number, literal in strings(f.read_text(encoding="utf-8", errors="replace")):
            found = [m.group(0) for rule in RULES for m in [rule.search(literal)] if m]
            if found:
                hits += 1
                print(f"{rel}:{number}: [{' '.join(found)}] {' '.join(literal.split())[:110]}")
    print(f"\n{hits} strings to rewrite")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
