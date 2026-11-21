"""Running a command that was read and allowed.

Every command runs in a Windows PowerShell of its own with no profile, not
elevated, in a granted folder, with an environment rebuilt from a short list
(none of the app's keys and tokens), a time limit, and its whole process tree
in a job object that is ended on stop or time out. Output is capped.

That PowerShell is started ahead and waits with the preamble loaded (`Spare`),
as terminals and agents keep a warm shell: the command is piped in when it
comes and another one is started to wait for the next. A command so begins at
once instead of after PowerShell's own start, and since each still has its own
process, nothing one command leaves (variables, location, environment, loaded
types, a program it started) reaches the next or is ended by the next one's
time out.

Remove-Item, by any of its aliases, is a function of the preamble's that moves
what it is given to the Recycle Bin and says each path it moved, so a delete
by command can be taken back as one by delete_files can.
"""
from __future__ import annotations

import base64
import collections
import html
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .grants import SECRET_NAMES, SECRET_SUFFIXES

POWERSHELL = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                          r"System32\WindowsPowerShell\v1.0\powershell.exe")
# Seconds a command may run, unless the tool asks for less.
TIME_LIMIT_S = 60
TIME_MAX_S = 300
# Characters of output kept: the head and the tail of what is longer, cut between lines; of error text.
OUTPUT_HEAD = 9000
OUTPUT_TAIL = 3000
ERRORS_KEPT = 2500
# The only variables a command's PowerShell gets from this process.
ENV_KEPT = ("SystemRoot", "windir", "SystemDrive", "ComSpec", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
            "HOMEPATH", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "APPDATA", "LOCALAPPDATA", "PUBLIC", "ProgramData",
            "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles", "CommonProgramFiles(x86)",
            "CommonProgramW6432", "OS", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OneDrive")
# After dsh's pwsh-local (MIT, see NOTICE): Windows PowerShell writes the OEM code page unless told. Programs
# it starts still write that code page and are read in it; what PowerShell prints goes out through a UTF-8
# writer of its own, and file cmdlets read and write UTF-8, not the ANSI code page.
PREAMBLE = ("$__out = [System.IO.StreamWriter]::new([Console]::OpenStandardOutput(), "
            "[System.Text.UTF8Encoding]::new($false)); $__out.AutoFlush = $true; "
            "$__err = [System.IO.StreamWriter]::new([Console]::OpenStandardError(), "
            "[System.Text.UTF8Encoding]::new($false)); $__err.AutoFlush = $true; "
            "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            "$ProgressPreference = 'SilentlyContinue'; "
            "[System.Threading.Thread]::CurrentThread.CurrentUICulture = 'en-US'; "
            "$PSDefaultParameterValues = @{'Format-Table:AutoSize' = $true; "
            + "; ".join(f"'{c}:Encoding' = 'UTF8'" for c in ("Get-Content", "Set-Content", "Add-Content", "Out-File",
                                                               "Select-String", "Import-Csv", "Export-Csv"))
            + "}; "
            # Modules load only from Windows' own folders, never from one a command could have written to.
            "$env:PSModulePath = \"$env:ProgramFiles\\WindowsPowerShell\\Modules;$PSHOME\\Modules\"\n")
# The command runs inside this, so a table is as wide as its text and not cut at a console's width, each error
# is one line apart from the output, and the exit code is a program's own or the command's `exit`.
WRAP = ("& {{\n{command}\n}} 2>&1 | ForEach-Object {{ if ($_ -is [System.Management.Automation.ErrorRecord]) {{ "
        "$__err.WriteLine(($(if ($_.InvocationInfo.MyCommand.Name) {{ $_.InvocationInfo.MyCommand.Name + ': ' }})"
        " + $_.Exception.Message) -replace '\\s*\\r?\\n\\s*', ' ') }} else {{ $_ }} }} "
        "| Out-String -Width 2048 -Stream | ForEach-Object {{ $__out.WriteLine($_.TrimEnd()) }}\n"
        "if ($LASTEXITCODE) {{ exit $LASTEXITCODE }}\n")
# Remove-Item as the Recycle Bin: a function comes before the cmdlet, and the aliases rm, del, ri and rd reach it
# too. Each item goes to the bin by Windows' own SHFileOperation with undo, silent and without an error dialog (as
# files.recycle does; FileSystem.DeleteFile would show one on screen for a file in use), a link is removed as
# itself, and each path moved is written to $__bin for the turn's list of changes (checkpoint.py). The type is
# compiled while the PowerShell waits, so a delete does not wait for it.
BIN = r"""Add-Type -Namespace Mibo -Name Bin -MemberDefinition @'
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct Op { public IntPtr hwnd; public uint wFunc; public string pFrom; public string pTo; public ushort fFlags;
  public bool fAnyOperationsAborted; public IntPtr hNameMappings; public string lpszProgressTitle; }
[DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern int SHFileOperationW(ref Op op);
public static bool Recycle(string path) { var op = new Op(); op.wFunc = 3; op.pFrom = path + "\0\0";
  op.fFlags = 0x0004 | 0x0010 | 0x0040 | 0x0400; return SHFileOperationW(ref op) == 0 && !op.fAnyOperationsAborted; }
'@
function Remove-Item {
  [CmdletBinding()]
  param([Parameter(Position = 0, ValueFromPipeline = $true)][string[]]$Path,
        [Parameter(ValueFromPipelineByPropertyName = $true)][Alias('PSPath', 'LP')][string[]]$LiteralPath,
        [string]$Filter, [string[]]$Include, [string[]]$Exclude, [switch]$Recurse, [switch]$Force)
  process {
    $more = @{ Force = $Force }
    foreach ($key in 'Filter', 'Include', 'Exclude') { if ($PSBoundParameters[$key]) { $more[$key] = $PSBoundParameters[$key] } }
    $items = if ($LiteralPath) { Get-Item -LiteralPath $LiteralPath -Force } else { Get-Item -Path $Path @more }
    foreach ($item in $items) {
      $full = $item.FullName
      if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        if ($item.PSIsContainer) { [IO.Directory]::Delete($full) } else { [IO.File]::Delete($full) }
        continue
      }
      if ($item.PSIsContainer -and -not $Recurse -and @(Get-ChildItem -LiteralPath $full -Force).Count) {
        Write-Error "$full has items in it and -Recurse was not given"
        continue
      }
      if ([Mibo.Bin]::Recycle($full)) { [IO.File]::AppendAllText($__bin, $full + "`n") }
      else { Write-Error "Windows did not move $full to the Recycle Bin" }
    }
  }
}
"""
# What the waiting PowerShell does with a command: one line, the command's script in UTF-8 and base64, run in the
# top scope as -EncodedCommand runs a script. No line (the app went away) ends it.
WAIT = ("$__line = [Console]::In.ReadLine()\nif (-not $__line) { exit }\n"
        ". ([ScriptBlock]::Create([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($__line))))\n")
# Error records as Windows PowerShell writes them to a redirected stderr (CLIXML).
CLIXML_ERROR = re.compile(r'<S S="(?:Error|warning)">(.*?)</S>', re.DOTALL)
# A path or a file name whose last part is a key file's, as grants.Places.blocked tells one. In a command's output it
# becomes HIDDEN, so no route names a file list_files hides. A name that is also a plain word (credentials, cookies,
# login data) counts only as a path's last part, or alone at the end of a listing's row, so prose keeps it.
_PART = r"[^\s\\/\"'<>|*?]"
_END = r"(?=$|[\s\"'<>|,;:)\]])"
_WORDS = "(?:" + "|".join(re.escape(n) for n in SECRET_NAMES if not re.search(r"[._]", n)) + ")"
_KEY = (rf"(?:{'|'.join(re.escape(n) for n in SECRET_NAMES if re.search(r'[._]', n))}"
        rf"|(?:\.env|id_rsa|id_ed25519|id_ecdsa){_PART}*|{_PART}*(?:{'|'.join(map(re.escape, SECRET_SUFFIXES))}))")
SECRET_PATH = re.compile(rf"(?<![^\s\"'(\[<>|=,;])(?:{_PART}*[\\/])*{_KEY}{_END}|(?:{_PART}*[\\/])+{_WORDS}{_END}"
                         rf"|(?:^|(?<=\d))(?P<lead>[ \t]*){_WORDS}[ \t]*$", re.IGNORECASE | re.MULTILINE)
HIDDEN = "[hidden: key file]"


def hide_secret_names(text: str) -> str:
    """`text` with every key file's name or path as HIDDEN."""
    return SECRET_PATH.sub(lambda m: (m.group("lead") or "") + HIDDEN, text)


def environment() -> dict:
    """The environment a command gets: the listed variables and a PATH of system folders and the person's own."""
    env = {k: v for k, v in os.environ.items() if k in ENV_KEPT or k.upper() in {e.upper() for e in ENV_KEPT}}
    root = env.get("SystemRoot") or r"C:\Windows"
    path = [os.path.join(root, "System32"), root, os.path.join(root, r"System32\Wbem"),
            os.path.join(root, r"System32\WindowsPowerShell\v1.0"), os.path.join(root, r"System32\OpenSSH")]
    apps = os.path.join(env.get("LOCALAPPDATA", ""), r"Microsoft\WindowsApps")
    # The person's own program folders (git, ffmpeg, 7-Zip), as their terminal finds them; not this app's Python.
    own = os.path.normcase(os.path.realpath(sys.prefix))
    theirs = [p for p in os.environ.get("PATH", "").split(os.pathsep) if os.path.isabs(p) and os.path.isdir(p)
              and not os.path.normcase(os.path.realpath(p)).startswith(own)]
    env["PATH"] = ";".join(dict.fromkeys(path + ([apps] if env.get("LOCALAPPDATA") else []) + theirs))
    env["NO_COLOR"] = "1"
    return env


def elevated() -> bool:
    """This app itself runs as administrator, which a child would inherit."""
    if sys.platform != "win32":
        return False
    import ctypes
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


class _Job:
    """A Windows job object holding a process and everything it starts, so the whole tree can be ended."""

    def __init__(self, process: subprocess.Popen):
        import ctypes
        self.k32 = ctypes.windll.kernel32
        self.k32.CreateJobObjectW.restype = ctypes.c_void_p
        self.k32.OpenProcess.restype = ctypes.c_void_p
        self.handle = self.k32.CreateJobObjectW(None, None)
        proc = self.k32.OpenProcess(0x0001 | 0x0100, False, process.pid)  # PROCESS_TERMINATE | SET_QUOTA
        self.k32.AssignProcessToJobObject(ctypes.c_void_p(self.handle), ctypes.c_void_p(proc))
        self.k32.CloseHandle(ctypes.c_void_p(proc))

    def end(self) -> None:
        import ctypes
        self.k32.TerminateJobObject(ctypes.c_void_p(self.handle), 1)

    def close(self) -> None:
        import ctypes
        self.k32.CloseHandle(ctypes.c_void_p(self.handle))


class Spare:
    """The PowerShell waiting for the next command, in its own job object from the start."""

    def __init__(self):
        self.lock = threading.Lock()
        self.waiting: tuple[subprocess.Popen, _Job] | None = None

    @staticmethod
    def _start() -> tuple[subprocess.Popen, _Job]:
        encoded = base64.b64encode((PREAMBLE + BIN + WAIT).encode("utf-16-le")).decode("ascii")
        # It waits in Windows' own folder, so it holds no granted folder open.
        process = subprocess.Popen(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            cwd=os.environ.get("SystemRoot") or None, env=environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return process, _Job(process)

    def warm(self) -> None:
        """One is waiting from now on, started if none is."""
        with self.lock:
            if self.waiting is None or self.waiting[0].poll() is not None:
                self.waiting = self._start()

    def take(self) -> tuple[subprocess.Popen, _Job]:
        """The one waiting (or a new one when none is), and another started to wait in its place."""
        with self.lock:
            taken, self.waiting = self.waiting, self._start()
        if taken is not None and taken[0].poll() is None:
            return taken
        if taken is not None:
            taken[1].close()
        return self._start()

    def close(self) -> None:
        """Ends the one waiting: the app is closing."""
        with self.lock:
            if self.waiting is not None:
                self.waiting[1].end()
                self.waiting[1].close()
                self.waiting = None


SPARE = Spare()


def _line(raw: bytes) -> str:
    """One line of output: UTF-8 from PowerShell's own writer, else the console code page it wrote itself in."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("oem" if sys.platform == "win32" else "latin-1", errors="replace")


class _Kept:
    """Output kept to a bound as it comes: the first lines, a rolling tail, and a count of the lines between."""

    def __init__(self):
        self.head: list[str] = []
        self.tail: collections.deque = collections.deque()
        self.head_chars = self.tail_chars = self.dropped = 0
        self.last = ""

    def add(self, line: str) -> None:
        line = line[:4000]
        if line.strip():
            self.last = line.strip()
        if self.head_chars + len(line) < OUTPUT_HEAD and not self.tail:
            self.head.append(line)
            self.head_chars += len(line) + 1
            return
        self.tail.append(line)
        self.tail_chars += len(line) + 1
        while self.tail_chars > OUTPUT_TAIL and len(self.tail) > 1:
            self.tail_chars -= len(self.tail.popleft()) + 1
            self.dropped += 1

    def text(self) -> str:
        cut = [f"[... {self.dropped} lines left out ...]"] if self.dropped else []
        return "\n".join(self.head + cut + list(self.tail)).strip("\n")


def errors_of(raw: bytes) -> str:
    """The error and warning text in what Windows PowerShell wrote to stderr (CLIXML), as plain lines."""
    text = "\n".join(_line(line) for line in raw.splitlines())
    own, _, xml = text.partition("#< CLIXML")
    plain = own + "".join(html.unescape(re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), part))
                          for part in CLIXML_ERROR.findall(xml))
    plain = "\n".join(line.rstrip() for line in plain.replace("\r\n", "\n").split("\n") if line.strip())
    return plain if len(plain) <= ERRORS_KEPT else plain[:ERRORS_KEPT] + f"\n[... {len(plain) - ERRORS_KEPT} characters left out ...]"


def run(command: str, cwd: str, *, limit_s: float = TIME_LIMIT_S, stop: threading.Event | None = None,
        progress: dict | None = None) -> dict:
    """Runs `command` in `cwd`: {"code", "out", "err", "clipped", "timed_out", "stopped", "seconds", "recycled"}.
    `stop` set from another thread ends the whole tree at once; `progress` gets the last line printed as it runs;
    `recycled` lists what Remove-Item moved to the Recycle Bin."""
    handle, bin_log = tempfile.mkstemp(prefix="mibo-bin-", suffix=".txt")
    os.close(handle)
    quoted = "'" + cwd.replace("'", "''") + "'"
    # It moves into `cwd` first, and runs nothing anywhere else when that is gone.
    script = ("$__bin = '" + bin_log.replace("'", "''") + "'\n"
              f"try {{ Set-Location -LiteralPath {quoted} -ErrorAction Stop }} catch {{ $__err.WriteLine($_); exit 1 }}\n"
              f"[Environment]::CurrentDirectory = {quoted}\n" + WRAP.format(command=command))
    process, job = SPARE.take()
    started = time.monotonic()
    # One line, then stdin is closed: what the command reads from stdin ends there, as it did with no stdin.
    process.stdin.write(base64.b64encode(script.encode("utf-8")) + b"\n")
    process.stdin.close()
    kept = _Kept()
    err: list[bytes] = []

    def drain() -> None:
        for raw in iter(process.stdout.readline, b""):
            kept.add(hide_secret_names(_line(raw.rstrip(b"\r\n"))))
            if progress is not None:
                progress["last"] = kept.last

    def drain_err() -> None:
        for chunk in iter(lambda: process.stderr.read(4096), b""):
            if sum(map(len, err)) < 200_000:
                err.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    err_reader = threading.Thread(target=drain_err, daemon=True)
    err_reader.start()
    timed_out = stopped = False
    while process.poll() is None:
        if time.monotonic() - started > limit_s:
            timed_out = True
        elif stop is not None and stop.is_set():
            stopped = True
        else:
            time.sleep(0.1)
            continue
        job.end()
        break
    process.wait()
    reader.join(5)
    err_reader.join(5)
    job.close()
    recycled = [line for line in Path(bin_log).read_text(encoding="utf-8").splitlines() if line]
    os.unlink(bin_log)
    return {"code": process.returncode, "out": kept.text(), "err": hide_secret_names(errors_of(b"".join(err))),
            "clipped": bool(kept.dropped), "timed_out": timed_out, "stopped": stopped,
            "seconds": round(time.monotonic() - started, 1), "recycled": recycled}


def listing(path: str, most: int = 2000) -> dict:
    """What a delete of `path` removes: {"files": n, "folders": n, "bytes": n, "names": [first few], "links": [...]},
    walking without following links (a link inside is named, not walked)."""
    files = folders = size = 0
    names: list[str] = []
    links: list[str] = []
    if os.path.islink(path) or not os.path.isdir(path):
        return {"files": 1, "folders": 0, "bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
                "names": [os.path.basename(path)], "links": [path] if os.path.islink(path) else []}
    stack = [path]
    while stack and files + folders < most:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink() or (entry.is_dir(follow_symlinks=False) and _junction(entry.path)):
                links.append(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                folders += 1
                stack.append(entry.path)
            else:
                files += 1
                try:
                    size += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
                if len(names) < 12:
                    names.append(os.path.relpath(entry.path, os.path.dirname(path)))
    return {"files": files, "folders": folders, "bytes": size, "names": names, "links": links,
            "more": bool(stack)}


def _junction(path: str) -> bool:
    is_junction = getattr(os.path, "isjunction", None)
    if is_junction is not None:
        return is_junction(path)
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x400)  # REPARSE_POINT
    except (OSError, AttributeError):
        return False
