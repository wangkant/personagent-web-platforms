"""Where skills live, how each is read and checked, and how one is added or removed.

A skill is a folder holding SKILL.md: YAML front matter (name, description, and optional license,
compatibility, metadata, allowed-tools, and Claude Code's disable-model-invocation and user-invocable) between two
`---` lines, then the instructions. Built-in skills ship in
builtin/; the person's own are the folders of <home>/skills, one level deep, as other agents keep them. Mibo's
own records (which are off, where each came from, Chinese titles) are in runtime/skills.json, never in a skill's
folder, so the folder stays as its author wrote it. A skill that does not read is listed with what is wrong and
never offered to the model. Nothing in a skill's folder is run when it is added.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import threading
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from ..storage import atomic_write_text

SKILL_FILE = "SKILL.md"
BUILTIN = Path(__file__).with_name("builtin")
# The spec's limits.
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
# A SKILL.md larger than this is not instructions.
MAX_SKILL_FILE = 512 * 1024
# What one added archive may hold.
MAX_MEMBERS = 2000
MAX_UNPACKED = 64 * 1024 * 1024
MAX_MEMBER = 16 * 1024 * 1024
MAX_DEPTH = 8
# What reading a damaged or crafted archive's member raises.
_BROKEN = (zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error)
RECORDS = "skills.json"
# The card line for a command that runs a file in a skill's folder (shell/panel.py).
FROM_SKILL = {"zh": "此命令来自技能「{name}」", "en": "This command comes from the skill “{name}”"}

_lock = threading.Lock()
_parsed: dict[tuple, tuple] = {}


@dataclass
class Skill:
    name: str
    description: str
    folder: Path
    builtin: bool
    on: bool = True
    # Codes of what is wrong; any makes the skill unusable. Warnings are shown but do not stop it.
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    origin: str = ""
    source: str = ""
    added: float = 0.0
    # The Chinese title and description Settings shows: the built-in ones' own, or made when added.
    title_zh: str = ""
    description_zh: str = ""
    digest: str = ""

    @property
    def usable(self) -> bool:
        return self.on and not self.problems

    @property
    def model_invocable(self) -> bool:
        # disable-model-invocation: true (Claude Code's field): started only when the person types /name.
        return self.meta.get("disable-model-invocation") != "true"

    @property
    def user_invocable(self) -> bool:
        # user-invocable: false: loaded by the model alone, never by /name.
        return self.meta.get("user-invocable") != "false"

    @property
    def file(self) -> Path:
        return self.folder / SKILL_FILE


def skills_dir(home: Path) -> Path:
    return Path(home) / "skills"


def _records_path(home: Path) -> Path:
    from ..paths import runtime_dir_for
    return runtime_dir_for(Path(home), os.getenv("AGENT_RUNTIME_DIR", "")) / RECORDS


def records(home: Path) -> dict:
    try:
        data = json.loads(_records_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(home: Path, data: dict) -> None:
    path = _records_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=1))


def _front(text: str) -> tuple[dict, str]:
    """(front matter, body) of a SKILL.md; ValueError with a problem code when it does not read."""
    text = text.lstrip("﻿").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("no_front_matter")
    end = next((n for n in range(1, len(lines)) if lines[n].strip() == "---"), 0)
    if not end:
        raise ValueError("no_front_matter")
    head, body = "\n".join(lines[1:end]), "\n".join(lines[end + 1:]).strip()
    try:
        data = yaml.safe_load(head)
    except yaml.YAMLError:
        # Skills written for other hosts often leave a colon in an unquoted value ("description: Use when: ...");
        # their parsers take it, so it is quoted here and read again, as the integration guide suggests.
        fixed = re.sub(r"^([A-Za-z0-9_-]+):[ \t]+(?![\"'|>\[{])(.*:.*)$",
                       lambda m: f"{m.group(1)}: '" + m.group(2).replace("'", "''") + "'", head, flags=re.M)
        try:
            data = yaml.safe_load(fixed)
        except yaml.YAMLError:
            raise ValueError("bad_yaml") from None
    if not isinstance(data, dict):
        raise ValueError("bad_yaml")
    return data, body


def read(folder: Path, builtin: bool = False) -> tuple[Skill, str]:
    """The skill in `folder` and its instructions, with what is wrong with it noted rather than raised."""
    path = folder / SKILL_FILE
    try:
        info = path.stat()
    except OSError:
        return Skill(folder.name, "", folder, builtin, problems=["no_skill_file"]), ""
    key = (str(path), info.st_mtime_ns, info.st_size)
    with _lock:
        hit = _parsed.get(key)
    if hit is None:
        hit = _read_file(path, info.st_size)
        with _lock:
            if len(_parsed) > 400:
                _parsed.clear()
            _parsed[key] = hit
    name, description, meta, body, problems, warnings, digest = hit
    return Skill(name or folder.name, description, folder, builtin, problems=list(problems),
                 warnings=list(warnings), meta=dict(meta), digest=digest), body


def _read_file(path: Path, size: int) -> tuple:
    folder = path.parent
    if size > MAX_SKILL_FILE:
        return "", "", {}, "", ["too_large"], [], ""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "", "", {}, "", ["not_text"], [], digest
    try:
        data, body = _front(text)
    except ValueError as e:
        return "", "", {}, "", [str(e)], [], digest
    problems, warnings = [], []
    name = data.get("name")
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        problems.append("no_name")
    elif len(name) > MAX_NAME or not NAME.match(name):
        # The name is what the model calls the skill by and what "/name" names, so it must be the spec's kind.
        problems.append("bad_name")
    elif name != folder.name:
        warnings.append("name_folder")
    description = data.get("description")
    description = " ".join(description.split()) if isinstance(description, str) else ""
    if not description:
        problems.append("no_description")
    elif len(description) > MAX_DESCRIPTION:
        warnings.append("long_description")
        description = description[:MAX_DESCRIPTION]
    if not body:
        problems.append("no_body")
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    meta = {str(k): str(v) for k, v in meta.items() if isinstance(v, (str, int, float))}
    for key in ("license", "compatibility", "allowed-tools"):
        if isinstance(data.get(key), str):
            meta[key] = data[key][:500]
    for key in ("disable-model-invocation", "user-invocable"):
        if isinstance(data.get(key), bool):
            meta[key] = str(data[key]).lower()
    return name, description, meta, body, problems, warnings, digest


def _folders(root: Path) -> list[Path]:
    try:
        return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
                      and not p.is_symlink())
    except OSError:
        return []


def all_skills(home: Path) -> list[Skill]:
    """Every skill, the person's own first, each with its switch and where it came from; a built-in one whose
    name the person's own takes is marked replaced."""
    kept = records(home)
    off = set(kept.get("off") or [])
    sources = kept.get("sources") or {}
    titles = kept.get("titles") or {}
    found: list[Skill] = []
    seen: set[str] = set()
    for builtin, root in ((False, skills_dir(home)), (True, BUILTIN)):
        for folder in _folders(root):
            skill, _ = read(folder, builtin)
            if skill.name in seen:
                skill.problems.append("replaced" if builtin else "duplicate")
            seen.add(skill.name)
            skill.on = skill.name not in off
            row = sources.get(folder.name) if not builtin else None
            if isinstance(row, dict):
                skill.origin, skill.source = str(row.get("origin") or ""), str(row.get("source") or "")
                skill.added = float(row.get("at") or 0)
            elif builtin:
                skill.origin = "builtin"
            else:
                skill.origin = "folder"
            made = titles.get(skill.name) if isinstance(titles.get(skill.name), dict) else {}
            if builtin or made.get("digest") != skill.digest:
                made = {}
            skill.title_zh = skill.meta.get("mibo-title-zh", "") or str(made.get("title_zh") or "")
            skill.description_zh = skill.meta.get("mibo-description-zh", "") or str(made.get("description_zh") or "")
            found.append(skill)
    return found


def usable(home: Path) -> list[Skill]:
    return [s for s in all_skills(home) if s.usable]


def get(home: Path, name: str) -> tuple[Skill, str] | None:
    """A usable skill by name, with its instructions."""
    for skill in usable(home):
        if skill.name == name:
            return read(skill.folder, skill.builtin)
    return None


def set_on(home: Path, name: str, on: bool) -> None:
    data = records(home)
    off = [n for n in data.get("off") or [] if n != name]
    data["off"] = off if on else [*off, name]
    _save(home, data)


def skill_in(command: str, home: Path) -> str:
    """The name of the skill whose folder a command names, or "": what the command card says it comes from."""
    text = os.path.normcase(command.replace("/", "\\"))
    for skill in all_skills(home):
        place = os.path.normcase(str(skill.folder.resolve()))
        if place in text:
            return skill.name
    return ""


# ------------------------------------------------------------------ adding

def _member_path(info: zipfile.ZipInfo) -> tuple[PurePosixPath, bool]:
    """A member's path inside the archive and whether it can be written as a plain file here. A path that would
    land outside the archive stops the whole add."""
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in path.parts:
        raise ValueError("skill_bad_archive")
    plain = not (stat.S_ISLNK(info.external_attr >> 16) or len(path.parts) > MAX_DEPTH + 3 or any(
        re.match(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", p, re.I) or p.endswith((" ", "."))
        or re.search(r'[<>:"|?*\x00-\x1f]', p) for p in path.parts))
    return path, plain


def install_zip(home: Path, data: bytes, origin: str, source: str, within: str = "", ref: str = "",
                market: dict | None = None) -> list[str]:
    """Adds every skill in a zip (a folder holding SKILL.md, at any depth, at or under `within` when given);
    returns the folder names added. Only files are written: nothing in it is run. A skill already there from the
    same place is replaced; one from elsewhere stops the add. `market` is the catalog entry it came from (id,
    commit, hash), kept so an update can be told (market.py)."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("skill_bad_archive") from None
    members = archive.infolist()
    if len(members) > 50 * MAX_MEMBERS:
        raise ValueError("skill_too_large")
    files: dict[PurePosixPath, tuple[zipfile.ZipInfo, bool]] = {}
    for info in members:
        path, plain = _member_path(info)
        if not info.is_dir():
            files[path] = (info, plain)
    inside = PurePosixPath(within.strip("/")) if within.strip("/") else PurePosixPath()
    # A skill is the topmost folder holding SKILL.md; one nested in another belongs to it.
    roots = sorted({p.parent for p in files if p.name == SKILL_FILE}, key=lambda p: len(p.parts))
    wanted = [r for r in roots if _under(r, inside)]
    if not wanted:
        # The address names a file or folder inside one skill: that skill.
        wanted = [r for r in roots if _under(inside, r)][-1:]
    chosen: list[PurePosixPath] = []
    for root in wanted:
        if not any(_under(root, c) for c in chosen):
            chosen.append(root)
    if not chosen:
        raise ValueError("skill_none_found")
    taken = [(p, info, plain) for p, (info, plain) in files.items() if any(_under(p, c) for c in chosen)]
    if any(not plain for _, _, plain in taken):
        raise ValueError("skill_bad_archive")
    if len(taken) > MAX_MEMBERS or sum(i.file_size for _, i, _ in taken) > MAX_UNPACKED \
            or any(i.file_size > MAX_MEMBER for _, i, _ in taken):
        raise ValueError("skill_too_large")
    target_root = skills_dir(home)
    target_root.mkdir(parents=True, exist_ok=True)
    kept = records(home)
    sources = kept.setdefault("sources", {})
    added: list[str] = []
    # Each folder is named as its skill, else as it was in the archive.
    plans = []
    for root in chosen:
        try:
            meta, _ = _front(archive.read(files[root / SKILL_FILE][0]).decode("utf-8", "replace"))
        except ValueError:
            meta = {}
        except _BROKEN:
            raise ValueError("skill_bad_archive") from None
        name = meta.get("name") if isinstance(meta.get("name"), str) and NAME.match(meta["name"].strip()) else ""
        folder = name.strip() if name else re.sub(r"[^a-z0-9-]+", "-", (root.name or "skill").lower()).strip("-")
        folder = folder[:MAX_NAME] or "skill"
        if (BUILTIN / folder).is_dir() and not (target_root / folder).exists():
            raise ValueError("skill_builtin_name")
        there = target_root / folder
        row = sources.get(folder) if isinstance(sources.get(folder), dict) else None
        if there.exists() and not (row and row.get("source") == source and row.get("within", "") == str(root)):
            raise ValueError("skill_exists")
        plans.append((root, folder))
    # Every skill is unpacked before any is put in place; a member that does not unpack as its directory says (a
    # wrong checksum, a size it lied about, entries sharing one member's bytes) stops the add with nothing kept.
    incoming = {folder: target_root / f".incoming-{folder}-{os.getpid()}" for _, folder in plans}
    try:
        for root, folder in plans:
            shutil.rmtree(incoming[folder], ignore_errors=True)
            incoming[folder].mkdir()
            for path, info, _ in taken:
                if not _under(path, root):
                    continue
                dest = incoming[folder].joinpath(*path.relative_to(root).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 16)
    except _BROKEN:
        for place in incoming.values():
            shutil.rmtree(place, ignore_errors=True)
        raise ValueError("skill_bad_archive") from None
    for root, folder in plans:
        there = target_root / folder
        if there.exists():
            shutil.rmtree(there)
        incoming[folder].rename(there)
        sources[folder] = {"origin": origin, "source": source, "within": str(root), "ref": ref,
                           "sha256": hashlib.sha256(data).hexdigest(), "at": time.time(),
                           **({"market": market} if market else {})}
        added.append(folder)
    _save(home, kept)
    return added


def _under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return not root.parts or path.parts[:len(root.parts)] == root.parts


def remove(home: Path, folder_name: str) -> None:
    """Deletes one of the person's own skills; built-in ones are switched off instead, never removed."""
    root = skills_dir(home)
    there = root / folder_name
    if not folder_name or folder_name.startswith(".") or there.parent != root or not there.is_dir() \
            or there.is_symlink():
        raise ValueError("skill_gone")
    shutil.rmtree(there)
    data = records(home)
    (data.get("sources") or {}).pop(folder_name, None)
    _save(home, data)


def keep_titles(home: Path, skill: Skill, made: dict) -> None:
    """The Chinese title and description made for a skill, kept by its file's digest."""
    data = records(home)
    data.setdefault("titles", {})[skill.name] = {"digest": skill.digest, **{
        k: " ".join(str(made.get(k) or "").split())[:limit]
        for k, limit in (("title_zh", 40), ("description_zh", 300))}}
    _save(home, data)


# ---------------------------------------------------------------- addresses

# A public git host's page for a repository, or a folder in it, and the archive that holds it.
_HOSTS = (
    (re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$"),
     lambda o, r, ref: f"https://codeload.github.com/{o}/{r}/zip/{ref or 'HEAD'}"),
    (re.compile(r"^https://gitlab\.com/((?:[\w.-]+/)+?)([\w.-]+?)(?:\.git)?(?:/-/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$"),
     lambda o, r, ref: f"https://gitlab.com/{o}{r}/-/archive/{ref or 'HEAD'}/{r}-{ref or 'HEAD'}.zip"),
    (re.compile(r"^https://codeberg\.org/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/src/(?:branch|tag|commit)/([^/]+)(?:/(.*))?)?/?$"),
     lambda o, r, ref: f"https://codeberg.org/{o}/{r}/archive/{ref or 'main'}.zip"),
    (re.compile(r"^https://gitee\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?/?$"),
     lambda o, r, ref: f"https://gitee.com/{o}/{r}/repository/archive/{ref or 'master'}.zip"),
)


def archive_for(address: str) -> tuple[str, str, str]:
    """(archive address, folder inside the repository, ref) for a repository or folder page on GitHub, GitLab,
    Codeberg or Gitee; ValueError for anything else."""
    address = address.strip().split("#", 1)[0].split("?", 1)[0]
    if not address.startswith("http"):
        address = "https://" + address.removeprefix("//")
    for pattern, archive in _HOSTS:
        m = pattern.match(address)
        if m:
            owner, repo, ref, inner = m.group(1), m.group(2), m.group(3) or "", (m.group(4) or "").strip("/")
            if inner.endswith(SKILL_FILE):
                inner = inner[:-len(SKILL_FILE)].strip("/")
            return archive(owner, repo, ref), inner, ref
    raise ValueError("skill_bad_address")


def archive_top(data: bytes) -> str:
    """The single top folder a host's archive puts everything in ("skills-main"), or ""."""
    try:
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    except zipfile.BadZipFile:
        return ""
    tops = {n.replace("\\", "/").split("/", 1)[0] for n in names}
    return tops.pop() if len(tops) == 1 and all("/" in n for n in names) else ""


def archive_commit(data: bytes) -> str:
    """The commit GitHub writes as an archive's comment, for the record of what was added."""
    try:
        comment = zipfile.ZipFile(io.BytesIO(data)).comment.decode("ascii", "ignore")
    except (zipfile.BadZipFile, ValueError):
        return ""
    return comment if re.fullmatch(r"[0-9a-f]{40}", comment) else ""
