"""Reading a command before it runs, and sorting what it would do.

PowerShell's own parser reads it (parse.ps1, in a PowerShell kept for the
purpose): the syntax tree, every command at any depth (pipeline stages, sub-
expressions, script blocks, function bodies), its parameters bound the way
PowerShell binds them (`StaticParameterBinder`: names, abbreviations,
positions), aliases resolved through PowerShell's own command table, file
redirections, method calls, type names and assignments. Here every command is
sorted into acts (`Act`), its paths made absolute with variables, `~`, `..`,
links and junctions followed. `judge` then holds the acts against the person's
grants and the never list. Anything the tables below do not know is "unknown":
it is never run without the person's press.
"""
from __future__ import annotations

import base64
import fnmatch
import functools
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import run as running
from .grants import Grants, known_folders, norm, under

COMMAND_MAX = 4000
READ_TIMEOUT_S = 20

# Kinds of act, mildest first. The never kinds are refused whatever was granted.
NEVER_KINDS = ("elevate", "system", "secrets", "hidden", "shutdown")
ASK_KINDS = ("delete", "overwrite", "outside", "move_out", "network", "install", "program", "unknown")

# ---------------------------------------------------------------- tables

PURE = {
    "select-object", "sort-object", "where-object", "foreach-object", "group-object", "measure-object", "format-table",
    "format-list", "format-wide", "format-custom", "out-string", "out-null", "out-default", "out-host", "write-output",
    "write-host", "write-verbose", "write-warning", "write-error", "write-information", "write-debug", "write-progress",
    "compare-object", "convertto-json", "convertfrom-json", "convertto-csv", "convertfrom-csv", "convertto-html",
    "convertto-xml", "convertfrom-stringdata", "get-member", "get-unique", "new-timespan", "get-random", "start-sleep",
    "get-date", "get-location", "split-path", "join-path", "get-variable", "set-variable", "new-variable",
    "clear-variable", "remove-variable", "get-culture", "get-uiculture", "get-host", "measure-command", "add-member",
    "new-guid", "get-command", "get-help", "test-json", "get-verb", "set-clipboard", "convertto-securestring", "get-error", "join-string", "get-psprovider", "get-alias",
}
# Reading files: the parameters that name what is read, and whether no path means the folder it runs in.
READS = {
    "get-childitem": ("list", True), "get-item": ("look", False), "get-itemproperty": ("look", False),
    "get-itempropertyvalue": ("look", False), "test-path": ("look", False), "resolve-path": ("look", False),
    "convert-path": ("look", False), "get-content": ("read", False), "get-filehash": ("read", False),
    "select-string": ("search", False), "import-csv": ("read", False), "get-acl": ("look", False),
    "select-xml": ("read", False), "import-clixml": ("read", False), "import-powershelldatafile": ("read", False),
    "format-hex": ("read", False),
}
# The reads that give a file's contents, not only its name.
CONTENT_READS = ("read", "search")
# Commands that set variables by name, and the common parameters that do: a constant may not stay one.
VARIABLE_CMDLETS = {"set-variable", "new-variable", "clear-variable", "remove-variable", "get-variable"}
# Readers and the file system's own value-less switches, which the binder leaves unbound after a quoted full path.
FS_READERS = {"get-childitem", "get-item", "get-content"}
FS_SWITCHES = {"file", "directory", "hidden", "readonly", "system", "raw", "followsymlink", "af", "ad", "ah", "as"}
VARIABLE_PARAMS = ("OutVariable", "PipelineVariable", "ErrorVariable", "WarningVariable", "InformationVariable")
# Members read off what comes down a pipeline (`ForEach-Object Name`) that only give a value back.
SAFE_MEMBERS = {
    "name", "fullname", "basename", "extension", "length", "lastwritetime", "creationtime", "lastaccesstime",
    "directoryname", "directory", "parent", "mode", "attributes", "exists", "count", "value", "values", "key", "keys",
    "path", "id", "processname", "workingset", "workingset64", "privatememorysize64", "cpu", "ws", "pm", "status",
    "displayname", "starttype", "size", "freespace", "used", "free", "root", "description", "line", "linenumber",
    "filename", "hash", "algorithm", "group", "psiscontainer", "isreadonly", "versioninfo", "productversion",
    "fileversion", "mainwindowtitle", "starttime", "handles", "sum", "average", "maximum", "minimum", "deviceid",
    "volumename", "drivetype", "label", "driveletter", "filesystem", "sizeremaining", "healthstatus", "matches",
    "lastwritetimeutc", "creationtimeutc", "company", "product", "linktype", "target", "pspath", "psparentpath",
    "pschildname", "ipaddress", "interfacealias", "caption", "version", "buildnumber",
}
# Methods that give a value back on a string but change files on a file (FileInfo.Replace): only on strings.
STRING_ONLY_METHODS = {"replace"}
STRING_MEMBERS = {
    "name", "basename", "fullname", "extension", "directoryname", "processname", "displayname", "description",
    "path", "line", "filename", "hash", "mainwindowtitle", "tostring", "trim", "trimstart", "trimend", "toupper",
    "tolower", "toupperinvariant", "tolowerinvariant", "substring", "replace", "normalize", "padleft", "padright",
    "remove", "insert", "format", "join", "getfilename", "getfilenamewithoutextension", "getextension",
    "getdirectoryname", "combine", "changeextension", "string", "system.string", "pschildname",
}
# Methods of Windows' management and shell objects that turn the computer off, change services or run things.
METHOD_KINDS = {"win32shutdown": "shutdown", "win32shutdowntracker": "shutdown", "reboot": "shutdown",
                "shutdown": "shutdown", "stopservice": "system", "startservice": "system", "changestartmode": "system",
                "pauseservice": "system", "resumeservice": "system", "setpowerstate": "system",
                "shellexecute": "hidden", "invokeverb": "hidden", "invokeverbex": "hidden"}
# [IO.File] and [IO.Directory] methods that only read what they are given.
FILE_READ_METHODS = {"readalltext", "readalllines", "readallbytes", "readlines", "exists", "getattributes",
                     "getlastwritetime", "getcreationtime", "getlastaccesstime", "getfiles", "getdirectories",
                     "enumeratefiles", "enumeratedirectories", "getfilesystementries", "enumeratefilesystementries"}
FILE_TYPES = ("io.file", "system.io.file", "io.directory", "system.io.directory")
READ_PARAMS = ("Path", "LiteralPath", "FilePath")
# Stages whose output is the files they list, and stages that only pass some of them on.
ITEM_SOURCES = {"get-childitem", "get-item", "resolve-path", "get-itemproperty"}
ITEM_FILTERS = {"where-object", "sort-object", "select-object", "out-null"}
# A listing's parameters that leave its items exactly as Python finds them; those that make a listing or a
# Select-Object hand on something else (names, streams, relative paths, picked properties).
LISTING_EXACT = {"path", "literalpath", "recurse", "force", "erroraction", "file", "directory"}
LISTING_RESHAPED = {"name", "stream", "relative"}
SELECT_RESHAPED = {"property", "expandproperty", "excludeproperty"}
# Modules that come with Windows, whose Get- and Test- commands only read.
INBOX_MODULES = {"storage", "nettcpip", "netadapter", "dnsclient", "netsecurity", "pnpdevice", "international",
                 "scheduledtasks", "defender", "bitlocker", "appx", "psreadline", "cimcmdlets", "printmanagement",
                 "smbshare", "netconnection", "packagemanagement", "powershellget", "dism", "netqos", "wifi",
                 "startlayout", "hyper-v", "netlbfo", "vpnclient", "windowsupdate", "configci", "trustedplatformmodule"}
# Facts about this computer, named by no path.
FACTS = {
    "get-psdrive": "disk", "get-volume": "disk", "get-disk": "disk", "get-physicaldisk": "disk",
    "get-partition": "disk", "get-computerinfo": "info", "get-process": "apps", "get-service": "info",
    "get-hotfix": "info", "get-timezone": "info", "get-netipaddress": "info", "get-netipconfiguration": "info",
    "get-netadapter": "info", "get-printer": "info", "get-pnpdevice": "info", "get-counter": "info",
    "get-startapps": "apps", "get-appxpackage": "apps", "get-package": "apps", "get-module": "info",
    "get-installedmodule": "apps", "get-winevent": "info", "get-eventlog": "info", "get-localuser": "info",
    "get-localgroup": "info", "get-executionpolicy": "info", "get-mppreference": "info",
    "get-mpcomputerstatus": "info", "get-netfirewallprofile": "info", "get-netfirewallrule": "info",
    "get-scheduledtask": "info", "get-uptime": "info", "get-ciminstance": "info", "get-wmiobject": "info",
    "get-cimclass": "info", "get-storagesubsystem": "disk", "get-psreadlineoption": "info",
}
DISK_CLASSES = ("win32_logicaldisk", "win32_volume", "win32_diskdrive", "msft_volume", "win32_diskpartition")
# File changes, each with its PowerShell parameters: (what, sources, destination).
CHANGES = {
    "new-item": ("new", ("Path",), ""), "mkdir": ("new", ("Path",), ""),
    "copy-item": ("copy", ("Path", "LiteralPath"), "Destination"),
    "move-item": ("move", ("Path", "LiteralPath"), "Destination"),
    "rename-item": ("rename", ("Path", "LiteralPath"), "NewName"),
    "remove-item": ("delete", ("Path", "LiteralPath"), ""),
    "clear-content": ("empty", ("Path", "LiteralPath"), ""),
    "set-content": ("write", ("Path", "LiteralPath"), ""), "add-content": ("append", ("Path", "LiteralPath"), ""),
    "out-file": ("write", ("FilePath", "LiteralPath"), ""), "export-csv": ("write", ("Path", "LiteralPath"), ""),
    "export-clixml": ("write", ("Path", "LiteralPath"), ""), "tee-object": ("write", ("FilePath", "LiteralPath"), ""),
    "set-itemproperty": ("attr", ("Path", "LiteralPath"), ""),
    "compress-archive": ("zip", ("Path", "LiteralPath"), "DestinationPath"),
    "expand-archive": ("unzip", ("Path", "LiteralPath"), "DestinationPath"),
}
NETWORK = {"invoke-webrequest": "Uri", "invoke-restmethod": "Uri", "start-bitstransfer": "Source",
           "send-mailmessage": "SmtpServer", "test-connection": "ComputerName", "test-netconnection": "ComputerName",
           "resolve-dnsname": "Name"}
INSTALL = {"install-module", "install-package", "install-script", "uninstall-module", "uninstall-package",
           "uninstall-script", "update-module", "update-script", "install-packageprovider", "add-appxpackage",
           "remove-appxpackage", "save-module", "save-package", "update-help"}
NEVER_CMDLETS = {
    "invoke-expression": "hidden", "add-type": "hidden", "set-alias": "hidden", "new-alias": "hidden",
    "import-alias": "hidden", "register-objectevent": "hidden", "register-engineevent": "hidden",
    "register-wmievent": "hidden", "start-job": "hidden", "start-threadjob": "hidden", "new-psdrive": "hidden",
    "new-module": "hidden", "register-scheduledjob": "system", "new-pssession": "system",
    "enter-pssession": "system", "invoke-wmimethod": "system", "invoke-cimmethod": "system",
    "set-wmiinstance": "system", "remove-wmiobject": "system", "set-ciminstance": "system",
    "new-ciminstance": "system", "remove-ciminstance": "system", "set-executionpolicy": "system",
    "unblock-file": "system", "set-acl": "system", "set-authenticodesignature": "system",
    "get-credential": "secrets", "convertfrom-securestring": "secrets", "export-pfxcertificate": "secrets",
    "export-certificate": "secrets", "get-pfxcertificate": "secrets", "get-storedcredential": "secrets",
    "restart-computer": "shutdown", "stop-computer": "shutdown", "new-itemproperty": "system",
    "remove-itemproperty": "system", "rename-itemproperty": "system", "copy-itemproperty": "system",
    "move-itemproperty": "system", "clear-itemproperty": "system", "clear-item": "system",
    "enable-psremoting": "system", "disable-psremoting": "system", "new-webserviceproxy": "system",
}
SYSTEM_NOUNS = ("service", "scheduledtask", "scheduledjob", "firewall", "mppreference", "mpthreat", "mpscan",
                "mpsignature", "executionpolicy", "computer", "localuser", "localgroup", "partition", "disk", "volume",
                "bitlocker", "windowsoptionalfeature", "windowscapability", "windowspackage", "netip", "netadapter",
                "dnsclient", "netroute", "netconnection", "tpm", "secureboot", "applocker", "processmitigation",
                "eventlog", "acl", "authenticode", "certificate", "psremoting", "pssession", "wsman", "date",
                "timezone", "culture", "winsystemlocale", "winuserlanguage", "winhomelocation", "computerrestore",
                "ciminstance", "wmi", "deliveryoptimization", "gpo", "gpregistry", "networkadapter", "vpn",
                "proxy", "smb", "printer", "windowsdefender", "uac")
READ_VERBS = ("get", "test", "measure", "select", "find", "search", "resolve", "compare", "convertto", "convertfrom",
              "format", "group", "sort", "split", "join", "confirm")
# Programs by file name.
EXE_READ = {"hostname", "whoami", "systeminfo", "tasklist", "where", "getmac", "driverquery", "chcp", "vol", "quser",
            "netstat", "nvidia-smi", "gpresult", "ver", "findstr", "tree", "fc", "comp", "more", "sort", "ipconfig",
            "wmic", "fsutil"}
# Those that can reach another computer with /s.
EXE_REMOTE = {"systeminfo", "tasklist", "getmac", "driverquery", "gpresult", "whoami", "quser"}
# nvidia-smi only asked, never told: anything else sets clocks, power or modes.
NVIDIA_READ = ("-q", "--query", "-l", "--list", "--format", "-i", "--id", "-d", "--display", "-h", "--help")
INTERPRETERS = ("python", "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php", "java", "javaw")
EXE_OPEN = {"notepad", "mspaint", "calc", "explorer", "write", "charmap"}
EXE_NET = {"ping", "tracert", "pathping", "nslookup", "curl", "wget", "ftp", "tftp", "nc", "ncat", "git"}
# git's subcommands that reach another computer; the rest work in the folder.
GIT_NET = {"clone", "fetch", "pull", "push", "ls-remote", "remote", "submodule", "send-email", "request-pull", "svn",
           "p4", "daemon", "http-backend", "instaweb", "credential"}
EXE_INSTALL = {"winget", "choco", "scoop", "msiexec", "pip", "pip3", "npm", "pnpm", "yarn", "gem", "cargo", "conda",
               "dotnet", "appinstaller"}
EXE_STOP = {"taskkill", "tskill"}
EXE_NEVER = {
    "cmd": "hidden", "powershell": "hidden", "pwsh": "hidden", "powershell_ise": "hidden", "wscript": "hidden",
    "cscript": "hidden", "mshta": "hidden", "rundll32": "hidden", "regsvr32": "hidden", "regsvcs": "hidden",
    "regasm": "hidden", "installutil": "hidden", "msbuild": "hidden", "bash": "hidden", "wsl": "hidden",
    "forfiles": "hidden", "pcalua": "hidden", "cmstp": "hidden", "mavinject": "hidden", "odbcconf": "hidden",
    "infdefaultinstall": "hidden", "msdt": "hidden", "certutil": "hidden", "bitsadmin": "hidden", "subst": "hidden",
    "conhost": "hidden", "scriptrunner": "hidden", "syncappvpublishingserver": "hidden", "msxsl": "hidden",
    "ieexec": "hidden", "presentationhost": "hidden", "hh": "hidden", "csc": "hidden", "vbc": "hidden", "jsc": "hidden",
    "atbroker": "hidden", "extrac32": "hidden", "finger": "hidden", "appvlp": "hidden", "xwizard": "hidden",
    "diskshadow": "system",
    "runas": "elevate", "gsudo": "elevate", "sudo": "elevate", "fodhelper": "elevate", "computerdefaults": "elevate",
    "sdclt": "elevate", "eventvwr": "elevate", "psexec": "elevate", "psexec64": "elevate",
    "shutdown": "shutdown", "logoff": "shutdown", "tsdiscon": "shutdown",
    "reg": "system", "regedit": "system", "regedt32": "system", "regini": "system", "bcdedit": "system",
    "bcdboot": "system", "bootrec": "system", "bootsect": "system", "diskpart": "system", "format": "system",
    "chkdsk": "system", "chkntfs": "system", "sfc": "system", "dism": "system", "schtasks": "system", "at": "system",
    "sc": "system", "net": "system", "net1": "system", "netsh": "system", "vssadmin": "system", "wbadmin": "system",
    "manage-bde": "system", "cipher": "system", "takeown": "system", "icacls": "system", "cacls": "system",
    "xcacls": "system", "setx": "system", "wevtutil": "system", "auditpol": "system", "secedit": "system",
    "gpupdate": "system", "gpedit": "system", "mmc": "system", "msconfig": "system", "winrm": "system",
    "winrs": "system", "ssh": "system", "scp": "system", "sftp": "system", "telnet": "system", "route": "system",
    "arp": "system", "mountvol": "system", "fdisk": "system", "defrag": "system", "esentutl": "system",
    "wusa": "system", "pnputil": "system", "devcon": "system", "powercfg": "system", "w32tm": "system",
    "control": "system", "services": "system", "taskschd": "system", "compmgmt": "system", "ntdsutil": "system",
    "nltest": "system", "ksetup": "system", "klist": "secrets", "cmdkey": "secrets", "vaultcmd": "secrets",
    "mimikatz": "secrets", "procdump": "secrets",
}
RUNNABLE = (".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh",
            ".scr", ".pif", ".hta", ".cpl", ".lnk", ".jar", ".py", ".pyw", ".sh", ".url", ".library-ms",
            ".settingcontent-ms", ".diagcab", ".inf", ".dll", ".application", ".appref-ms")
INSTALLERS = (".msi", ".msp", ".msix", ".msixbundle", ".appx", ".appxbundle", ".appinstaller", ".msu")
# Opening these in their own program runs code, changes settings or installs something: asked like a program.
OPEN_RISKY = RUNNABLE + INSTALLERS + (
    ".reg", ".msc", ".chm", ".website", ".xbap", ".jnlp", ".wsc", ".sct", ".scf", ".theme", ".themepack",
    ".deskthemepack", ".search-ms", ".searchconnector-ms", ".cer", ".crt", ".der", ".p7b", ".xll", ".iqy", ".slk",
    ".docm", ".dotm", ".xlsm", ".xltm", ".xlam", ".pptm", ".potm", ".ppam", ".mht", ".mhtml", ".gadget", ".ps1xml",
    ".psc1", ".ws", ".vb", ".mof", ".job", ".lnk")
# Methods with no effect outside the command, and types whose static members are such.
PURE_METHODS = {
    "tostring", "trim", "trimend", "trimstart", "substring", "replace", "split", "toupper", "tolower",
    "toupperinvariant", "tolowerinvariant", "contains", "startswith", "endswith", "indexof", "lastindexof", "padleft",
    "padright", "equals", "compareto", "gettype", "add", "addrange", "adddays", "addhours", "addminutes",
    "addseconds", "addmonths", "addyears", "toshortdatestring", "tolongdatestring", "toshorttimestring", "insert",
    "remove", "removeat", "containskey", "containsvalue", "join", "format", "round", "floor", "ceiling", "abs", "max",
    "min", "pow", "sqrt", "log", "log10", "truncate", "parse", "tryparse", "toint32", "toint64", "todouble",
    "todecimal", "toboolean", "getfilename", "getfilenamewithoutextension", "getextension", "getdirectoryname",
    "combine", "changeextension", "isnullorempty", "isnullorwhitespace", "concat", "getdrives", "getfolderpath",
    "getenvironmentvariable", "normalize", "tochararray", "toarray", "reverse", "match", "matches", "ismatch",
    "escape", "unescape", "getenumerator", "sort", "clear", "getvalue", "where", "foreach", "sum", "average",
    "count", "distinct", "first", "last", "tolist", "now", "today", "fromfiletime", "tofiletime", "getbytes",
    "getstring", "frombase10", "newguid", "getrandomfilename", "gettemppath", "gettempfilename", "exists",
    "tolocaltime", "touniversaltime", "getpathroot", "ispathrooted", "hasextension", "totalgigabytes",
    "getinvalidfilenamechars", "getinvalidpathchars", "tryadd", "trygetvalue", "copy", "indexofany",
}
SAFE_TYPES = {"math", "system.math", "string", "system.string", "datetime", "system.datetime", "timespan",
              "system.timespan", "io.path", "system.io.path", "io.driveinfo", "system.io.driveinfo", "char",
              "system.char", "int", "int32", "int64", "long", "double", "decimal", "system.int32", "system.int64",
              "system.double", "system.decimal", "regex", "system.text.regularexpressions.regex", "guid",
              "system.guid", "array", "system.array", "environment", "system.environment", "bool", "system.boolean",
              "convert", "system.convert", "text.encoding", "system.text.encoding", "version", "system.version",
              "linq.enumerable", "system.linq.enumerable", "bitconverter", "system.bitconverter", "uri", "system.uri",
              "io.file", "system.io.file", "io.directory", "system.io.directory"}
NEVER_TYPES = ("scriptblock", "system.management.automation", "powershell", "runspacefactory", "reflection.",
               "system.reflection", "activator", "system.activator", "runtime.interopservices",
               "system.runtime.interopservices", "appdomain", "system.appdomain", "codedom", "microsoft.csharp",
               "wmiclass", "wmi", "wmisearcher", "system.management.", "adsi", "adsisearcher",
               "security.cryptography.protecteddata", "system.security.cryptography.protecteddata",
               "windows.security.credentials", "microsoft.win32.registry", "system.diagnostics.process",
               "diagnostics.process", "type", "system.type")
NET_TYPES = ("net.webclient", "system.net.webclient", "net.http", "system.net.http", "net.sockets", "system.net.sockets",
             "net.webrequest", "system.net.webrequest", "net.httpwebrequest", "system.net.httpwebrequest",
             "net.mail", "system.net.mail")
NEVER_METHODS = {"invoke", "invokereturnasis", "create", "frombase64string", "load", "loadfrom", "loadfile",
                 "getdelegateforfunctionpointer", "createinstance", "invokescript", "newscriptblock", "expandstring",
                 "addscript", "begininvoke", "invokemember", "setenvironmentvariable", "dynamicinvoke"}
NET_METHODS = {"downloadstring", "downloadfile", "downloaddata", "uploadstring", "uploadfile", "uploaddata",
               "openread", "openwrite", "getasync", "postasync", "putasync", "sendasync", "getstringasync",
               "getbytearrayasync", "getresponse", "send", "connect"}
# The registry keys a command may read: installed programs and the Windows version.
REGISTRY_READ = (r"hklm\software\microsoft\windows\currentversion\uninstall",
                 r"hklm\software\wow6432node\microsoft\windows\currentversion\uninstall",
                 r"hkcu\software\microsoft\windows\currentversion\uninstall",
                 r"hklm\software\microsoft\windows nt\currentversion")
# And the values it may change, after asking: light or dark, and file name extensions shown.
REGISTRY_WRITE = (r"hkcu\software\microsoft\windows\currentversion\themes\personalize",
                  r"hkcu\software\microsoft\windows\currentversion\explorer\advanced")
HIDDEN_VARS = {"executioncontext", "psdefaultparametervalues"}
# Names that would change how deletes and confirmations work, or hide what runs: never set, never passed.
DRY_DEFEATERS = re.compile(r"whatif|confirmpreference|psdefaultparametervalues", re.IGNORECASE)
VERBED = {"copy": "copies", "move": "moves", "rename": "renames", "zip": "zips", "unzip": "unzips"}
LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}


@dataclass
class Act:
    """One thing a command would do."""
    kind: str
    what: str
    why: str
    paths: list[str] = field(default_factory=list)
    dest: str = ""
    name: str = ""
    write: bool = False
    recurse: bool = False
    wild: bool = False
    unresolved: bool = False
    sends: bool = False
    host: str = ""
    raw: list[str] = field(default_factory=list)
    globs: list[str] = field(default_factory=list)
    dest_unresolved: bool = False
    # The places a listing before named, with `raw`, which stand for its items in the plain words.
    listed: list[str] = field(default_factory=list)
    # A program run by its own file (paths[0]): its arguments as written, for the person's always-allow rules.
    args: list[str] | None = None


@dataclass
class Listing:
    """What a stage that lists files hands down the pipeline."""
    paths: list[str]
    raw: list[str]
    globs: list[str]
    recurse: bool
    # A parameter past the path (-Filter, -Include, -Depth) picks among the items.
    narrowed: bool
    # The items themselves, found in Python; None when they cannot be listed first.
    items: list[str] | None


@dataclass
class Reading:
    command: str
    cwd: str
    acts: list[Act]
    errors: list[str]


# ---------------------------------------------------------------- the parser process

class Parser:
    """A Windows PowerShell kept to read commands (parse.ps1), started on first use and again if it dies."""

    def __init__(self):
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None

    def _start(self) -> subprocess.Popen:
        script = (Path(__file__).with_name("parse.ps1")).read_text(encoding="utf-8")
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return subprocess.Popen(
            [running.POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            cwd=os.environ.get("TEMP") or None, env=running.environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, command: str, cwd: str) -> dict | None:
        """The parse of `command` as parse.ps1 answers it, or None when the parser gave no answer in time."""
        request = json.dumps({"b64": base64.b64encode(command.encode("utf-8")).decode("ascii"), "cwd": cwd}) + "\n"
        with self.lock:
            for _ in range(2):
                if self.process is None or self.process.poll() is not None:
                    self.process = self._start()
                answer: list[bytes] = []
                process = self.process

                def ask(process=process, answer=answer) -> None:
                    process.stdin.write(request.encode("utf-8"))
                    process.stdin.flush()
                    answer.append(process.stdout.readline())

                worker = threading.Thread(target=ask, daemon=True)
                worker.start()
                worker.join(READ_TIMEOUT_S)
                if answer and answer[0].strip():
                    try:
                        return json.loads(answer[0].decode("utf-8"))
                    except ValueError:
                        pass
                process.kill()
                self.process = None
        return None

    def close(self) -> None:
        with self.lock:
            if self.process is not None:
                self.process.kill()
                self.process = None


PARSER = Parser()


# ---------------------------------------------------------------- paths

def _items(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class Paths:
    """Turns a parsed argument into absolute places, knowing the folder it runs in and the constants assigned."""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.stack: list[tuple] = []
        self.consts: dict[str, dict] = {}
        # (provider, place) after Set-Location into the registry or another provider; relative paths go there.
        self.where: tuple[str, str] | None = None

    def strings(self, el) -> list[str] | None:
        """The literal strings an argument stands for, or None when it is computed at run time."""
        if not isinstance(el, dict):
            return None
        if el.get("k") in ("s", "n"):
            return [str(el.get("v") or "")]
        if el.get("k") == "a":
            out: list[str] = []
            for item in _items(el.get("items")):
                got = self.strings(item)
                if got is None:
                    return None
                out += got
            return out
        if el.get("k") == "v" and str(el.get("v") or "").lower() in self.consts:
            return self.strings(self.consts[str(el["v"]).lower()])
        return None

    def place(self, raw: str) -> tuple[str, str]:
        """(provider, absolute path) of one path as PowerShell would take it."""
        text = raw.strip().strip('"').strip("'")
        # "Module\Provider::path", as PowerShell takes any provider by its qualified name.
        qualified = re.match(r"^(?:[\w.]+\\)?(\w+)::(.*)$", text, re.DOTALL)
        if qualified:
            kind, text = qualified.group(1).lower(), qualified.group(2)
            if kind == "registry":
                text = "registry::" + text
            elif kind != "filesystem":
                named = {"certificate": "cert", "environment": "env", "variable": "variable", "function": "function",
                         "alias": "alias", "wsman": "wsman"}.get(kind, "provider")
                return named, f"{named}:{text}".lower()
        low = text.lower()
        if self.where and not re.match(r"^(?:[a-zA-Z]:|[\\/~]|\w+::|(?:env|variable|function|alias|cert|wsman|hk\w*):)",
                                       text, re.IGNORECASE):
            # Relative to a location in another provider.
            return self.where[0], self.where[1] + "\\" + low
        if low.startswith(("registry::", "hklm:", "hkcu:", "hkcr:", "hku:", "hkcc:", "hkey_")):
            key = re.sub(r"^registry::", "", low)
            key = re.sub(r"^hkey_local_machine", "hklm", re.sub(r"^hkey_current_user", "hkcu", key))
            return "registry", key.replace(":", "").replace("/", "\\").rstrip("\\")
        provider = re.match(r"^(env|variable|function|alias|cert|wsman):", low)
        if provider:
            return provider.group(1), low
        if low.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
            return "device", text
        if low.startswith(("\\\\", "//")):
            # This computer's own drives through their admin share: the drive itself.
            share = re.match(r"^[\\/]{2}([^\\/]+)[\\/]([a-zA-Z])\$(?:[\\/](.*))?$", text)
            if share and _local_host(share.group(1)):
                return "file", os.path.normpath(share.group(2) + ":\\" + (share.group(3) or ""))
            return "unc", text
        if text.startswith("~"):
            text = os.path.join(os.environ.get("USERPROFILE", ""), text[1:].lstrip("\\/"))
        if re.match(r"^[a-zA-Z]:(?![\\/])", text):
            # "C:name" is relative to that drive's current folder: read as the drive's root, the wider case.
            text = text[:2] + "\\" + text[2:]
        return "file", os.path.normpath(os.path.join(self.cwd, text))


def _base(path: str) -> tuple[str, bool]:
    """The part of a path before its first wildcard, and whether it had one."""
    parts = re.split(r"[\\/]", path)
    for n, part in enumerate(parts):
        if any(c in part for c in "*?["):
            base = "\\".join(parts[:n]) or path[:3]
            return (base + "\\" if re.match(r"^[a-zA-Z]:$", base) else base), True
    return path, False


def _local_host(host: str) -> bool:
    """A host name that is this computer: localhost in any spelling, a loopback address, its own name."""
    host = host.strip("[]").rstrip(".").lower()
    if host in LOOPBACK or host.endswith(".localhost") or host in ("", ".", "?", "wsl$", "wsl.localhost") or \
            host == os.environ.get("COMPUTERNAME", "").lower():
        return True
    try:
        address = ipaddress.ip_address(host)
        mapped = getattr(address, "ipv4_mapped", None)
        return address.is_loopback or address.is_unspecified or bool(mapped and mapped.is_loopback)
    except ValueError:
        pass
    try:
        # The old numeric forms a URL may use: 2130706433, 0x7f.1, 127.1.
        return socket.inet_aton(host)[0] in (0, 127)
    except OSError:
        return False


def _matches(pattern: str, most: int = 5000):
    """The places a wildcard path matches now, as PowerShell's wildcards would (dot names included)."""
    parts = re.split(r"[\\/]", pattern)
    found = [parts[0] + "\\"]
    for n, part in enumerate(parts[1:], 1):
        if not part:
            continue
        nxt = []
        for place in found:
            if not any(c in part for c in "*?["):
                nxt.append(os.path.join(place, part))
                continue
            try:
                nxt += [e.path for e in os.scandir(place) if fnmatch.fnmatch(e.name, part)]
            except OSError:
                pass
            if len(nxt) > most:
                return nxt
        found = nxt
    return [p for p in found if os.path.lexists(p)]


def _walk(base: str, most: int = 50000):
    """Everything under `base`, not following links; None when there is more than `most`."""
    out, stack = [], [base]
    while stack:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            out.append(entry.path)
            if len(out) > most:
                return None
            if entry.is_dir(follow_symlinks=False) and not running._junction(entry.path):
                stack.append(entry.path)
    return out


def _listed(globs: list[str], children: bool, recurse: bool, keep) -> list[str] | None:
    """The items a listing hands on, as PowerShell lists them: a folder named to Get-ChildItem (`children`) gives
    what is in it, all under it with -Recurse; a pattern its matches, and with -Recurse also every item under its
    folder named like it and all a matching folder holds. Only those `keep` passes; None past 5000."""
    found: list[str] = []
    for glob in globs:
        if not any(c in glob for c in "*?["):
            if children and os.path.isdir(glob):
                try:
                    got = _walk(glob, 5000) if recurse else [e.path for e in os.scandir(glob)]
                except OSError:
                    got = []
            else:
                got = [glob] if os.path.lexists(glob) else []
        else:
            got = _matches(glob)
            if children and recurse:
                every = _walk(_base(glob)[0], 5000)
                if every is None:
                    return None
                leaf = os.path.basename(glob).lower()
                got += [p for p in every if fnmatch.fnmatchcase(os.path.basename(p).lower(), leaf)]
                for hit in [p for p in got if os.path.isdir(p) and not _is_link(p)]:
                    inside = _walk(hit, 5000)
                    if inside is None:
                        return None
                    got += inside
        if got is None:
            return None
        found += got
    found = [p for p in dict.fromkeys(found) if keep(p)]
    return found if len(found) <= 5000 else None


def _some(paths: list[str]) -> str:
    """Paths for an act's reason, the first five of a long list."""
    return ", ".join(paths[:5]) + (f" and {len(paths) - 5} more" if len(paths) > 5 else "")


def _is_link(path: str) -> bool:
    return os.path.islink(path) or (os.path.isdir(path) and running._junction(path))


# ---------------------------------------------------------------- sorting

def _bound(node: dict, name: str):
    got = (node.get("bound") or {}).get(name)
    return got.get("el") if isinstance(got, dict) else None


def _switch(node: dict, name: str) -> bool:
    got = (node.get("bound") or {}).get(name)
    return isinstance(got, dict) and str(got.get("cv") or "True").lower() != "false"


def _named(node: dict) -> tuple[str, str, str]:
    """(canonical lowercased name, command type, program path) of a command after aliases."""
    res = node.get("res") or {}
    name = str(res.get("n") or node.get("name") or "")
    return name.lower(), str(res.get("ct") or ""), str(res.get("path") or "")


def _plain_args(node: dict, paths: Paths) -> list[str] | None:
    out = []
    for el in _items(node.get("args")):
        if isinstance(el, dict) and el.get("k") == "p":
            out.append("-" + str(el.get("v") or ""))
            continue
        got = paths.strings(el)
        if got is None:
            return None
        out += got
    return out


class Sorter:
    def __init__(self, parsed: dict, cwd: str):
        self.parsed = parsed
        self.paths = Paths(cwd)
        self.acts: list[Act] = []
        self.funcs = {str(n.get("name") or "").lower() for n in _items(parsed.get("nodes")) if n.get("type") == "func"}
        # Each pipeline's listing so far, or None when what goes down it may be any path at all.
        self.pipes: dict[int, Listing | None] = {}
        self.last_raw: list[str] = []
        self.last_globs: list[str] = []
        self.last_listed: list[str] = []
        self.origin = cwd
        self.unstable, self.no_consts = self._unstable(_items(parsed.get("nodes")))

    @staticmethod
    def _unstable(nodes: list) -> tuple[set, bool]:
        """Variables that may hold more than one value as the command runs (assigned twice, looped over, set by
        name), and whether any variable may (set by a computed name, or through an object)."""
        seen: dict[str, int] = {}
        unstable: set[str] = set()
        anything = False
        strip = lambda v: re.sub(r"^(global|script|local|private):", "", str(v or "").lower().lstrip("+"))
        for node in nodes:
            if node.get("type") == "assign":
                if node.get("ltype") in ("MemberExpressionAst", "IndexExpressionAst"):
                    anything = True
                var = strip(node.get("var"))
                seen[var] = seen.get(var, 0) + 1
                if seen[var] > 1:
                    unstable.add(var)
            elif node.get("type") == "foreach":
                unstable.add(strip(node.get("var")))
            elif node.get("type") == "cmd":
                names = [_bound(node, p) for p in VARIABLE_PARAMS]
                if _named(node)[0] in VARIABLE_CMDLETS - {"get-variable"}:
                    names.append(_bound(node, "Name"))
                for el in names:
                    if el is None:
                        continue
                    if isinstance(el, dict) and el.get("k") == "s":
                        unstable.add(strip(el.get("v")))
                    else:
                        anything = True
        return unstable, anything

    def add(self, kind: str, what: str, why: str, **more) -> Act:
        # The paths as written, from the files() call just before, for the plain words.
        more.setdefault("raw", self.last_raw)
        more.setdefault("globs", self.last_globs)
        more.setdefault("listed", self.last_listed)
        self.last_raw = []
        self.last_globs = []
        self.last_listed = []
        act = Act(kind, what, why, **more)
        self.acts.append(act)
        return act

    def files(self, node: dict, params, write: bool, default_cwd: bool = False) -> tuple[list[str], bool, bool, bool]:
        """(absolute paths, any wildcard, any unresolved, from the pipeline) named by these parameters. A
        registry, certificate or other provider path is sorted on the spot."""
        out: list[str] = []
        raws: list[str] = []
        globs: list[str] = []
        wild = unresolved = False
        given = [el for el in (_bound(node, p) for p in params) if el is not None]
        if not given:
            if int(node.get("index") or 0) > 0:
                # Items from the stage before: known only when it listed files (ITEM_SOURCES) and what
                # came after only filtered them; anything else may hand on any path at all.
                upstream = self.pipes.get(node.get("pipe"))
                if upstream is None:
                    return [], False, True, True
                if upstream.items is None:
                    # More than can be listed first: the listing's own places stand for them.
                    self.last_raw, self.last_globs = list(upstream.raw), list(upstream.globs)
                    return list(upstream.paths), False, True, True
                self.last_globs = list(upstream.items)
                # Items picked among as it runs, or listed by patterns, are named by the listing in the plain words.
                if (upstream.narrowed or int(node.get("index") or 0) > 1
                        or (upstream.raw and all(any(c in r for c in "*?") for r in upstream.raw))):
                    self.last_raw, self.last_listed = list(upstream.raw), list(upstream.paths)
                return list(upstream.items), False, False, True
            if default_cwd and self.paths.where:
                self.provider(*self.paths.where, write, node)
                return [], False, False, False
            if default_cwd:
                self.last_globs = [self.paths.cwd]
            return ([self.paths.cwd] if default_cwd else []), False, False, False
        for el in given:
            strings = self.paths.strings(el)
            if strings is None:
                unresolved = True
                continue
            for raw in strings:
                provider, place = self.paths.place(raw)
                if provider == "file":
                    base, has = _base(place)
                    out.append(base)
                    raws.append(raw)
                    globs.append(place)
                    wild = wild or has
                else:
                    self.provider(provider, place, write, node)
        self.last_raw = raws
        self.last_globs = globs
        return out, wild, unresolved, False

    def keeps(self, node: dict):
        """Which items a listing's -File, -Directory, -Filter and -Include let through, read loosely so that no
        item PowerShell would hand on is left out: Windows also matches a three-letter extension against a longer
        one and *.* against a name with no dot, and a pattern computed, with brackets, escapes, the system's own
        wildcards (< > ") or a short name's ~ narrows nothing here."""
        file, folder = _switch(node, "File"), _switch(node, "Directory")
        groups = []
        for param in ("Filter", "Include"):
            got = self.paths.strings(_bound(node, param)) if _bound(node, param) is not None else None
            if got and not any(c in s for s in got for c in "[`~<>\""):
                groups.append([s.lower().replace("?", "*") for s in got])

        def keep(path: str) -> bool:
            if (file and not os.path.isfile(path)) or (folder and not os.path.isdir(path)):
                return False
            name = os.path.basename(path).lower()
            stem, ext = os.path.splitext(name)
            names = (name, stem + ext[:4], name + ".")
            return all(any(fnmatch.fnmatchcase(n, s) for n in names for s in group) for group in groups)
        return keep

    def unlisted_recurse(self, node: dict, piped: bool) -> bool:
        """Items from a recursive listing too large to list first: any file under its places."""
        upstream = self.pipes.get(node.get("pipe")) if piped else None
        return upstream is not None and upstream.items is None and upstream.recurse

    def provider(self, provider: str, place: str, write: bool, node: dict) -> None:
        name = _named(node)[0]
        if provider == "registry":
            if not write and any(place == k or place.startswith(k + "\\") for k in REGISTRY_READ):
                self.add("read", "info", f"reads the registry key {place}")
            elif write and name == "set-itemproperty" and any(place == k for k in REGISTRY_WRITE):
                self.add("unknown", "setting", f"changes the Windows setting at {place}", name=place)
            else:
                self.add("system", "registry", f"{'changes' if write else 'reads'} the registry ({place})")
        elif provider == "cert":
            self.add("secrets", "certs", "reaches the certificate store")
        elif provider in ("function", "alias"):
            self.add("hidden", "redefine", f"changes PowerShell's own {provider}s, which hides what later commands do")
        elif provider == "wsman":
            self.add("system", "remote", "changes remote management settings")
        elif provider == "device":
            self.add("system", "device", f"reaches a raw device path ({place[:60]})")
        elif provider == "unc":
            # A share on another computer: the path and the person's sign-in go out over the network.
            self.add("network", "share", f"reaches the network share {place[:80]}", paths=[],
                     host=re.split(r"[\\/]", place[2:])[0], sends=True)
        elif provider == "provider":
            self.add("system", "provider", f"reaches PowerShell's {place.split(':')[0]} provider")
        elif provider == "env" and write and re.match(r"env:(path|pathext|psmodulepath|comspec)\b", place):
            self.add("hidden", "redefine", "changes where programs are looked up, which hides what later commands run")

    def sort(self) -> None:
        for node in sorted(_items(self.parsed.get("nodes")), key=lambda n: int(n.get("off") or 0)):
            kind = node.get("type")
            if kind == "cmd":
                before = len(self.acts)
                self.command(node)
                name = _named(node)[0]
                pipe = node.get("pipe")
                bound = {str(k).lower() for k in (node.get("bound") or {})}
                if name in ITEM_SOURCES and not self.paths.where:
                    acts = self.acts[before:]
                    globs = [g for act in acts for g in act.globs]
                    recurse = any(act.recurse for act in acts)
                    # A place of another provider, or computed, or a listing that hands on something else.
                    known = bool(acts) and not bound & LISTING_RESHAPED and all(
                        act.globs and not act.unresolved for act in acts)
                    items = _listed(globs, name == "get-childitem", recurse or "depth" in bound,
                                    self.keeps(node)) if known else None
                    self.pipes[pipe] = Listing([p for act in acts for p in act.paths],
                                               [r for act in acts for r in act.raw], globs, recurse,
                                               bool(bound - LISTING_EXACT), items)
                elif (name not in ITEM_FILTERS or int(node.get("index") or 0) == 0
                      or (name == "select-object" and bound & SELECT_RESHAPED)):
                    self.pipes[pipe] = None
                self.redirects(node)
            elif kind == "expr":
                self.redirects(node)
            elif kind == "method":
                self.method(node)
            elif kind == "type":
                self.type_name(str(node.get("name") or ""))
            elif kind == "assign":
                self.assign(node)
            elif kind == "other":
                what = str(node.get("what"))
                if what in ("UsingStatementAst", "DynamicKeywordStatementAst"):
                    self.add("hidden", "hidden", f"uses {what[:-3]}, which loads or builds code")
                elif what == "TypeDefinitionAst":
                    self.add("unknown", "unknown", "defines a class")
        for name in _items(self.parsed.get("vars")):
            if re.sub(r"^(global|script|local|private):", "", str(name).lower()) in HIDDEN_VARS:
                self.add("hidden", "hidden", f"uses ${name}, which can run commands that cannot be read first")

    def redirects(self, node: dict) -> None:
        for redirect in _items(node.get("redirs")):
            el = redirect.get("file") or {}
            if el.get("var") == "null":
                continue
            if self.paths.where:
                self.add("system", "provider", "writes while working inside the registry or another provider")
                continue
            strings = self.paths.strings(el)
            if strings is None:
                self.add("unknown", "write", "writes its output to a file whose name is computed", unresolved=True,
                         write=True)
                continue
            for raw in strings:
                provider, place = self.paths.place(raw)
                if provider != "file":
                    self.provider(provider, place, True, node)
                    continue
                append = bool(redirect.get("append"))
                exists = os.path.isfile(place)
                self.add("overwrite" if exists and not append else "change", "append" if append else "write",
                         f"{'appends' if append else 'writes'} its output to {place}", paths=[place], write=True)

    def assign(self, node: dict) -> None:
        var = re.sub(r"^(global|script|local|private):", "", str(node.get("var") or "").lower())
        left = str(node.get("left") or "").lower().replace("{", "").replace("}", "")
        if var in ("whatifpreference", "confirmpreference") or left.startswith(("$function:", "$alias:")) or (
                node.get("ltype") != "VariableExpressionAst" and DRY_DEFEATERS.search(left)):
            self.add("hidden", "hidden", f"changes {node.get('left')}, which would change how commands delete or hide them")
        elif node.get("ltype") in ("MemberExpressionAst", "IndexExpressionAst"):
            # Setting a property is not a variable's new value.
            pass
        elif var.startswith("env:") and var[4:] in ("path", "pathext", "psmodulepath", "comspec"):
            self.add("hidden", "redefine", "changes where programs are looked up, which hides what later commands run")
        elif var and node.get("op") == "Equals" and isinstance(node.get("right"), dict) and not self.no_consts \
                and var not in self.unstable:
            self.paths.consts[var] = node["right"]
        elif var:
            self.paths.consts.pop(var, None)

    def type_name(self, name: str) -> None:
        low = name.lower()
        if any(low == t or low.startswith(t if t.endswith(".") else t + ".") or low == t.rstrip(".") for t in NEVER_TYPES):
            self.add("hidden", "hidden", f"uses the .NET type [{name}], which can run code that cannot be read first")
        elif any(low == t or low.startswith(t + ".") for t in NET_TYPES):
            self.add("network", "net", f"uses [{name}] to reach the network", sends=True)

    def method(self, node: dict) -> None:
        member = str(node.get("member") or "").lower()
        on = str(node.get("on") or "").lower()
        text = str(node.get("text") or "")[:80]
        args = _items(node.get("args"))
        first = args[0] if args else None
        if member in ("foreach", "where") and isinstance(first, dict) and first.get("type") not in (
                "ScriptBlockExpressionAst", "TypeExpressionAst"):
            # .ForEach('Delete') calls that method on every item.
            self.member(first, text)
            return
        if on in FILE_TYPES and member != "exists":
            self.file_method(member, node, first)
            return
        if on in ("environment", "system.environment") and member.startswith("getenvironmentvariable") and (
                int(node.get("nargs") or 0) >= 2 or member.endswith("s") and int(node.get("nargs") or 0) >= 1):
            self.add("secrets", "secrets", "reads the person's saved environment variables, where programs keep keys")
            return
        if not member:
            self.add("hidden", "hidden", f"calls a method whose name is computed ({text})")
        elif member in METHOD_KINDS:
            kind = METHOD_KINDS[member]
            self.add(kind, kind, f"calls .{node.get('member')}()")
        elif member in STRING_ONLY_METHODS and not self.stringy(node):
            self.add("unknown", "unknown", f"calls .{node.get('member')}() on something that may be a file ({text})")
        elif member in NEVER_METHODS:
            self.add("hidden", "hidden", f"calls .{node.get('member')}(), which runs or loads code that cannot be read first")
        elif member in NET_METHODS:
            url = " ".join(self.paths.strings(first) or []) if first is not None else ""
            host = (urlsplit(url).hostname or "").lower()
            if host and _local_host(host):
                self.add("system", "loopback", f"reaches this computer's own services at {host}")
            else:
                self.add("network", "net", f"calls .{node.get('member')}() to reach {host or 'the network'}",
                         host=host or "?", sends=True)
        elif node.get("static") and on and on not in SAFE_TYPES:
            self.add("unknown", "unknown", f"calls [{node.get('on')}]::{node.get('member')}()",
                     paths=self.literal_places(node), write=True)
        elif member not in PURE_METHODS or (on in ("io.file", "system.io.file", "io.directory", "system.io.directory")
                                            and member != "exists"):
            self.add("unknown", "unknown", f"calls .{node.get('member')}() ({text})",
                     paths=self.literal_places(node), write=True)

    def literal_places(self, node: dict) -> list[str]:
        """What cannot be sorted counts as the worst it could be: every literal argument that could be a path is
        taken as written to, so a key or the app's own files named there are still refused."""
        places = []
        for el in _items(node.get("args")):
            for raw in self.paths.strings(el) or []:
                if "://" in raw or not re.search(r"[\\/:]|\.\w{1,5}$|^\.", raw):
                    continue
                provider, place = self.paths.place(raw)
                if provider == "file":
                    places.append(_base(place)[0])
                else:
                    self.provider(provider, place, True, node)
        return places

    def stringy(self, node: dict) -> bool:
        """The method is called on a string: a string written out, a string member, or a constant string."""
        recv, rname = str(node.get("recv") or ""), str(node.get("rname") or "").lower()
        if recv in ("StringConstantExpressionAst", "ExpandableStringExpressionAst"):
            return True
        if recv in ("MemberExpressionAst", "InvokeMemberExpressionAst", "ConvertExpressionAst"):
            return rname in STRING_MEMBERS
        if recv == "VariableExpressionAst":
            const = self.paths.consts.get(rname)
            return isinstance(const, dict) and const.get("k") == "s"
        return False

    def member(self, el, text: str) -> None:
        """A member called by name on what comes down the pipeline (ForEach-Object Name, .ForEach('Name'))."""
        names = self.paths.strings(el)
        if names is None:
            self.add("hidden", "hidden", f"calls a member whose name is computed ({text[:60]})")
            return
        for name in (n.lower() for n in names):
            if name in SAFE_MEMBERS or (name in PURE_METHODS and name not in STRING_ONLY_METHODS):
                continue
            if name == "delete":
                self.add("delete", "skip_bin", "deletes what comes down the pipeline by .Delete(), past the Recycle Bin")
            elif name in ("kill", "closemainwindow"):
                self.add("program", "stop", "ends a running program", name=text[:120])
            elif name in METHOD_KINDS:
                self.add(METHOD_KINDS[name], METHOD_KINDS[name], f"calls .{name}() on what comes down the pipeline")
            else:
                self.add("unknown", "unknown", f"calls .{name}() on what comes down the pipeline")

    def file_method(self, member: str, node: dict, first) -> None:
        """[IO.File] and [IO.Directory]: the file it names, read or changed."""
        strings = self.paths.strings(first) if first is not None else None
        if not strings:
            self.add("unknown", "unknown", f"calls [{node.get('on')}]::{node.get('member')}() on a computed path")
            return
        provider, place = Paths(self.origin).place(strings[0])
        if provider != "file":
            self.provider(provider, place, member not in FILE_READ_METHODS, node)
            return
        if member in FILE_READ_METHODS:
            self.add("read", "read", f"reads {place}", paths=[place], raw=[strings[0]])
        elif member == "delete":
            self.add("delete", "skip_bin", f"deletes {place} by [{node.get('on')}]::Delete(), past the Recycle Bin",
                     paths=[place], write=True, raw=[strings[0]])
        else:
            self.add("unknown", "unknown", f"calls [{node.get('on')}]::{node.get('member')}() on {place}",
                     paths=[place], write=True, raw=[strings[0]])

    # ------------------------------------------------------------ one command

    def command(self, node: dict) -> None:
        first = node.get("first") or {}
        if first.get("type") == "ScriptBlockExpressionAst":
            return
        if first.get("k") != "s" or not node.get("name"):
            self.add("hidden", "hidden", f"runs a command whose name is computed ({str(node.get('text'))[:60]}), "
                                         "which cannot be read first")
            return
        name, ctype, program = _named(node)
        switches = [str(a.get("v") or "").lower() for a in _items(node.get("args")) if isinstance(a, dict) and a.get("k") == "p"]
        if any(DRY_DEFEATERS.search(k) for k in (node.get("bound") or {})) or any(
                DRY_DEFEATERS.search(str(a.get("v") or "")) for a in _items(node.get("args")) if isinstance(a, dict)) or any(
                p == "wi" or (len(p) >= 2 and "whatif".startswith(p)) for p in switches):
            self.add("hidden", "hidden", f"{node.get('name')} is given -WhatIf or a preference that changes how it "
                                         "deletes")
            return
        if name in self.funcs:
            return
        if name in VARIABLE_CMDLETS and _bound(node, "Name") is not None and self.paths.strings(_bound(node, "Name")) is None:
            # A variable set by a computed name may be a preference that changes deletes, or any constant here.
            self.add("unknown", "unknown", f"{node.get('name')} sets a variable whose name is computed")
            return
        if name == "foreach-object":
            # Script blocks are read where they stand; anything else names a member to call on every item.
            for param in ("MemberName", "Process", "Begin", "End", "RemainingScripts"):
                el = _bound(node, param)
                for item in (_items(el.get("items")) if isinstance(el, dict) and el.get("k") == "a" else [el]):
                    if item is not None and not (isinstance(item, dict) and item.get("type") == "ScriptBlockExpressionAst"):
                        self.member(item, str(node.get("text") or ""))
            return
        if any(isinstance(a, dict) and a.get("sp") for a in _items(node.get("args"))):
            self.add("unknown", "unknown", f"passes {node.get('name')} its parameters from a variable (@splat)")
            return
        unbound = [str(k).lower() for k in _items(node.get("unbound"))]
        if name in FS_READERS and unbound and set(unbound) <= FS_SWITCHES:
            # The file system's own switches (-File, -Raw) on a reader; the binder knows them only for some paths.
            unbound = []
        if ctype not in ("Application", "ExternalScript") and unbound:
            # A provider parameter such as -Raw after the path leaves the command unbound.
            self.add("unknown", "unknown", f"PowerShell could not tell what every part of {node.get('name')} is for",
                     paths=self.literal_places(node), write=True)
            return
        if ctype in ("Application", "ExternalScript") or (not ctype and re.search(r"[\\/]|\.\w{2,4}$", name)):
            self.program(node, name, program or str(first.get("v") or ""), ctype)
            return
        # A known program missing here is still sorted by its name (wmic is gone from new Windows, not from all).
        if not ctype and os.path.splitext(name)[0] in (*EXE_NEVER, *INTERPRETERS, *EXE_READ, *EXE_NET, *EXE_INSTALL,
                                                       *EXE_OPEN, *EXE_STOP):
            self.program(node, name, str(first.get("v") or ""), ctype)
            return
        if not ctype:
            self.add("unknown", "unknown", f"runs {node.get('name')}, which PowerShell does not know here",
                     paths=self.literal_places(node), write=True)
            return
        if name in NEVER_CMDLETS:
            kind = NEVER_CMDLETS[name]
            self.add(kind, kind, f"runs {node.get('res', {}).get('n') or name}")
            return
        verb, _, noun = name.partition("-")
        if verb not in READ_VERBS and any(s in noun for s in SYSTEM_NOUNS) and name not in CHANGES:
            self.add("system", "system", f"runs {node['res'].get('n')}, which changes the system")
            return
        if name == "get-help" and _switch(node, "Online"):
            self.add("network", "browse", "opens the help page online in the browser", host="docs.microsoft.com")
            return
        if name in PURE:
            if name == "tee-object" and _bound(node, "FilePath") is not None:
                pass
            else:
                return
        if name in ("set-location", "push-location", "pop-location"):
            self.location(node, name)
            return
        if name in FACTS:
            self.facts(node, name)
            return
        if name in READS:
            what, default_cwd = READS[name]
            paths, wild, unresolved, piped = self.files(node, READ_PARAMS, False, default_cwd)
            # Items from a recursive listing too large to list first are any file under it.
            recurse = _switch(node, "Recurse") or self.unlisted_recurse(node, piped)
            self.add("read", what, f"{what}s {_some(paths) or 'what comes down the pipeline'}", paths=paths,
                     wild=wild or piped, unresolved=unresolved, recurse=recurse)
            return
        if name in CHANGES:
            self.change(node, name)
            return
        if name == "get-clipboard":
            self.add("outside", "clipboard", "reads what is on the clipboard")
            return
        if name in NETWORK:
            self.network(node, name)
            return
        if name in INSTALL:
            self.add("install", "install", f"runs {node['res'].get('n')}", name=str(node.get("text"))[:120])
            return
        if name in ("start-process", "invoke-item"):
            self.start(node, name)
            return
        if name == "stop-process":
            self.add("program", "stop", "ends a running program", name=str(node.get("text"))[:120])
            return
        if name == "new-object":
            self.new_object(node)
            return
        if name == "import-module":
            self.import_module(node)
            return
        if name == "invoke-command":
            if any(_bound(node, p) is not None for p in ("ComputerName", "Session", "FilePath", "ConnectionUri", "VMName")):
                self.add("system", "remote", "runs commands on another computer or from a file")
            return
        if name == "set-item":
            paths, _, unresolved, _ = self.files(node, ("Path", "LiteralPath"), True)
            if paths or unresolved:
                self.add("unknown", "unknown", "sets an item", paths=paths, unresolved=unresolved, write=True)
            return
        module = str((node.get("res") or {}).get("m") or "").lower()
        if verb in READ_VERBS and (module.startswith("microsoft.") or module in INBOX_MODULES):
            params = [p for p in (node.get("bound") or {}) if "path" in p.lower() or p.lower() in ("file", "filepath")]
            paths, wild, unresolved, _ = self.files(node, params, False)
            if any(p.lower() in ("computername", "cimsession", "session") for p in (node.get("bound") or {})):
                self.add("system", "remote", f"runs {node['res'].get('n')} against another computer")
                return
            self.add("read", "info", f"runs {node['res'].get('n')}", paths=paths, wild=wild, unresolved=unresolved)
            return
        self.add("unknown", "unknown", f"runs {node['res'].get('n') or name}, which the app does not know",
                 paths=self.literal_places(node), write=True)

    def location(self, node: dict, name: str) -> None:
        if name == "pop-location":
            if self.paths.stack:
                self.paths.cwd, self.paths.where = self.paths.stack.pop()
            return
        el = _bound(node, "Path") or _bound(node, "LiteralPath")
        strings = self.paths.strings(el) if el is not None else [os.environ.get("USERPROFILE", "")]
        if not strings:
            self.add("unknown", "unknown", "moves to a folder whose name is computed")
            return
        provider, place = self.paths.place(strings[0])
        if name == "push-location":
            self.paths.stack.append((self.paths.cwd, self.paths.where))
        if provider != "file":
            self.provider(provider, place, False, node)
            self.paths.where = (provider, place)
            return
        self.paths.where = None
        self.paths.cwd = place
        self.add("read", "look", f"works in {place}", paths=[place])

    def facts(self, node: dict, name: str) -> None:
        if any(p.lower() in ("computername", "cimsession", "session") for p in (node.get("bound") or {})):
            self.add("system", "remote", f"runs {node['res'].get('n')} against another computer")
            return
        what = FACTS[name]
        if name in ("get-ciminstance", "get-wmiobject", "get-cimclass"):
            cls = " ".join(self.paths.strings(_bound(node, "ClassName") or _bound(node, "Class")) or []).lower()
            query = " ".join(self.paths.strings(_bound(node, "Query") or _bound(node, "Filter")) or []).lower()
            what = "disk" if any(c in cls or c in query for c in DISK_CLASSES) else "info"
        self.add("read", what, f"reads {node['res'].get('n')}")

    def change(self, node: dict, name: str) -> None:
        what, sources, dest_param = CHANGES[name]
        if self.paths.where:
            self.add("system", "provider", f"runs {node.get('name')} inside the registry or another provider")
            return
        if name in ("new-item", "mkdir"):
            typed = self.paths.strings(_bound(node, "ItemType")) if _bound(node, "ItemType") is not None else [""]
            item_type = _item_type(" ".join(typed or []) if typed is not None else None, name)
            if item_type in ("link", "unknown"):
                self.add("system", "link", "makes a link (or an item of a computed type), which can make a folder "
                                           "reach places outside it")
                return
            paths, wild, unresolved, piped = self.files(node, ("Path",), True, default_cwd=True)
            names = self.paths.strings(_bound(node, "Name")) if _bound(node, "Name") is not None else None
            if _bound(node, "Name") is not None and names is None:
                unresolved = True
            if names:
                paths = [os.path.join(p, n) for p in paths for n in names]
            exists = any(os.path.exists(p) for p in paths)
            force = _switch(node, "Force")
            self.add("overwrite" if exists and force and not item_type.startswith("dir") else "change",
                     "mkdir" if item_type.startswith("dir") else "newfile", f"creates {', '.join(paths)}",
                     paths=paths, write=True, unresolved=unresolved)
            return
        paths, wild, unresolved, piped = self.files(node, sources, what != "zip")
        recurse = _switch(node, "Recurse")
        if what == "delete":
            # Remove-Item is run.BIN's function, which moves to the Recycle Bin; named with its module, the cmdlet.
            # Items a listing narrows (-Filter, -File) or a stage after it picks among are known only as it runs.
            upstream = self.pipes.get(node.get("pipe")) if piped else None
            narrowed = upstream is not None and (upstream.narrowed or int(node.get("index") or 0) > 1)
            self.add("delete", "skip_bin" if "\\" in str(node.get("name") or "") else "delete",
                     f"deletes {_some(paths) or 'what comes down the pipeline'}", paths=paths, write=True,
                     wild=wild or piped, recurse=recurse or self.unlisted_recurse(node, piped),
                     unresolved=unresolved or narrowed
                     or any(_bound(node, k) is not None for k in ("Include", "Exclude", "Filter")))
            return
        if what == "empty":
            self.add("overwrite", "empty", f"empties {_some(paths)}", paths=paths, write=True, wild=wild,
                     unresolved=unresolved)
            return
        if what in ("write", "append", "attr"):
            append = what == "append" or _switch(node, "Append")
            exists = any(os.path.isfile(p) for p in paths)
            kind = "overwrite" if exists and not append and not _switch(node, "NoClobber") and what != "attr" else "change"
            self.add(kind, "append" if append else what, f"{'appends to' if append else 'writes'} {_some(paths)}",
                     paths=paths, write=True, wild=wild, unresolved=unresolved or (piped and what == "attr"))
            return
        dest = ""
        dest_unresolved = False
        if dest_param == "NewName":
            names = self.paths.strings(_bound(node, "NewName"))
            if names is None or len(names) != 1:
                dest_unresolved = True
            else:
                dest = os.path.join(os.path.dirname(paths[0]) if len(paths) == 1 else self.paths.cwd, names[0])
        else:
            el = _bound(node, dest_param)
            if el is None:
                dest = self.paths.cwd
            else:
                strings = self.paths.strings(el)
                if strings is None or len(strings) != 1:
                    dest_unresolved = True
                else:
                    provider, dest = self.paths.place(strings[0])
                    if provider != "file":
                        self.provider(provider, dest, True, node)
                        return
        overwrite = False
        if dest and not wild and not unresolved and not piped:
            force = _switch(node, "Force")
            for src in paths:
                if what == "unzip":
                    overwrite = overwrite or (force and _unzip_overwrites(src, dest))
                    continue
                target = os.path.join(dest, os.path.basename(src)) if os.path.isdir(dest) and what != "rename" else dest
                replaces = os.path.isfile(target) and os.path.normcase(target) != os.path.normcase(src) and (
                    what == "copy" or force or what == "zip")
                # A folder copied with -Recurse replaces any file of the same name already there.
                merges = what == "copy" and os.path.isdir(src) and recurse and _copy_overwrites(src, target)
                overwrite = overwrite or replaces or merges
        self.add("overwrite" if overwrite else "change", what,
                 f"{VERBED.get(what, what + 's')} {_some(paths) or 'items'} to {dest or '?'}",
                 paths=paths, dest=dest, write=what in ("move", "rename"), wild=wild or piped,
                 unresolved=unresolved or dest_unresolved, recurse=recurse,
                 dest_unresolved=dest_unresolved and not unresolved)

    def network(self, node: dict, name: str) -> None:
        targets = self.paths.strings(_bound(node, NETWORK[name]))
        target = " ".join(targets or [])
        host = (urlsplit(target).hostname or target.split("/")[0]).lower() if target else ""
        if target and _local_host(host):
            self.add("system", "loopback", f"reaches this computer's own services at {host}")
            return
        method = " ".join(self.paths.strings(_bound(node, "Method")) or ["get"]).lower()
        # A computed address, headers or sign-in can carry anything out.
        sends = (name == "send-mailmessage" or method not in ("get", "head", "default") or targets is None
                 or any(_bound(node, p) is not None for p in ("Body", "InFile", "Form", "Headers", "UserAgent",
                                                              "Credential", "UseDefaultCredentials", "WebSession",
                                                              "Certificate", "Proxy"))
                 or "?" in target)
        out = []
        if _bound(node, "OutFile") is not None or _bound(node, "Destination") is not None:
            out, _, unresolved, _ = self.files(node, ("OutFile", "Destination"), True)
            if unresolved:
                self.add("unknown", "write", "saves a download under a computed name", unresolved=True, write=True)
        if _bound(node, "InFile") is not None:
            files, _, _, _ = self.files(node, ("InFile",), False)
            self.add("read", "read", "reads a file to send", paths=files)
        self.add("network", "net", f"reaches {host or 'the network'}" + (" and sends data" if sends else ""),
                 host=host or "?", sends=sends, paths=out, write=bool(out))

    def program(self, node: dict, name: str, path: str, ctype: str) -> None:
        base = os.path.splitext(os.path.basename(path or name))[0].lower()
        args = _plain_args(node, self.paths)
        ext = os.path.splitext(path or name)[1].lower()
        if base in EXE_NEVER:
            self.add(EXE_NEVER[base], EXE_NEVER[base], f"runs {base}")
            return
        if ctype == "ExternalScript" or ext in RUNNABLE[2:]:
            self.add("program", "script", f"runs the script {path or name}", name=path or name, paths=[path] if path else [])
            return
        if ext in INSTALLERS:
            self.add("install", "install", f"runs the installer {path or name}", name=path or name)
            return
        words = [a.lower() for a in (args or [])]
        if base in INTERPRETERS:
            if args is None or any(a in ("-c", "-e", "-p", "--eval", "--print", "-command", "-r", "-jar") for a in words):
                self.add("hidden", "hidden", f"runs {base} with code given on the command line")
            elif words[:2] == ["-m", "pip"]:
                self.pip(words[2:], base)
            elif words and words[0] in ("--version", "-v", "-V".lower()):
                self.add("read", "info", f"asks {base} its version")
            else:
                self.add("program", "script", f"runs {base} {' '.join(args)[:80]}", name=f"{base} {' '.join(args)}"[:120])
            return
        if base in ("pip", "pip3"):
            self.pip(words, base)
            return
        if base in EXE_INSTALL:
            if words and words[0] in ("list", "ls", "--version", "-v", "freeze", "show", "info"):
                self.add("read", "apps", f"lists what {base} installed")
            elif base == "winget" and words and words[0] in ("search", "show"):
                self.add("network", "net", "searches the winget catalogue online", host="winget", sends=False)
            else:
                self.add("install", "install", f"runs {base} {' '.join(args or [])[:80]}",
                         name=f"{base} {' '.join(args or [])}"[:120])
            return
        if base in EXE_STOP:
            self.add("program", "stop", f"ends a running program ({' '.join(args or [])[:60]})", name=f"{base} {' '.join(args or [])}"[:120])
            return
        if base == "git" and args is not None and not (set(words) & GIT_NET) and not any(
                "://" in a or a.startswith("--remote") for a in words):
            # Local git work (status, log, add, commit, diff) is a program run in its folder, not the network.
            self.add("program", "program", f"runs git {' '.join(args)[:80]}", name=f"{path or name} {' '.join(args)}"[:160],
                     paths=[path] if path else [], args=args)
            return
        if base in EXE_NET:
            host = next((a for a in (args or []) if not a.startswith("-") and not a.startswith("/")), "")
            host = (urlsplit(host).hostname or host).lower()
            if host and _local_host(host):
                self.add("system", "loopback", f"reaches this computer's own services at {host}")
                return
            sends = base in ("curl", "wget", "ftp", "tftp", "nc", "ncat", "git") and (
                args is None or any(a in ("-d", "--data", "-f", "--form", "-t", "--upload-file", "-x", "--request",
                                          "push", "put", "send", "-h", "--header", "-u", "--user", "-b", "--cookie")
                                    or a.startswith(("--data", "-d", "-f", "-t", "-h", "-u", "-b")) for a in words))
            self.add("network", "net", f"runs {base} to reach {host or 'the network'}", host=host or base, sends=sends,
                     name=f"{base} {' '.join(args or [])}"[:120])
            return
        if base in EXE_READ:
            if args is None:
                self.add("unknown", "unknown", f"runs {base} with computed arguments")
            elif base == "ipconfig" and any(a not in ("/all", "/?", "-all") for a in words):
                self.add("system", "system", "changes the network setup with ipconfig")
            elif base == "wmic" and any(a.startswith("/format") for a in words):
                self.add("hidden", "hidden", "runs wmic with a style sheet, which can run code")
            elif base == "wmic" and any(a.startswith("/node") for a in words):
                self.add("system", "remote", "runs wmic against another computer")
            elif base == "wmic" and (any(a in ("call", "create", "delete", "set", "process") for a in words)
                                     or any(a.startswith("/") for a in words)):
                self.add("system", "system", "changes the system through wmic")
            elif base == "fsutil" and words[:2] not in (["volume", "diskfree"], ["fsinfo", "drives"]):
                self.add("system", "system", "runs fsutil, a disk tool")
            elif base in EXE_REMOTE and any(a in ("/s", "-s") for a in words):
                self.add("network", "net", f"runs {base} against another computer", host=base, sends=True,
                         name=f"{base} {' '.join(args)}"[:120])
            elif base == "nvidia-smi" and any(a.startswith("-") and not a.startswith(NVIDIA_READ) for a in words):
                self.add("system", "system", "changes the graphics card's settings with nvidia-smi")
            elif (base == "sort" and any(a.startswith(("/o", "/output")) for a in words)) or (
                    base == "gpresult" and any(a.startswith(("/h", "/x")) for a in words)):
                self.add("unknown", "write", f"runs {base}, which writes a file", name=f"{base} {' '.join(args)}"[:120])
            else:
                files = [a for a in args if a and not a.startswith(("/", "-"))
                         and base in ("findstr", "tree", "fc", "comp", "more", "sort")]
                if base == "findstr" and files:
                    files = files[1:]
                files += [a[3:] for a in args if base == "findstr" and a.lower().startswith(("/f:", "/g:"))]
                places = [self.paths.place(a)[1] for a in files]
                self.add("read", "search" if base == "findstr" else ("disk" if base == "fsutil" else "info"),
                         f"runs {base}", paths=[_base(p)[0] for p in places], globs=places,
                         wild=any(_base(p)[1] for p in places), recurse=base == "findstr" and "/s" in words)
            return
        if base in EXE_OPEN:
            if args is None:
                self.add("unknown", "unknown", f"opens {base} with computed arguments")
                return
            targets = [a for a in args if a and not a.startswith(("/", "-"))]
            for target in targets:
                if self.opened(target, base):
                    return
            files = [self.paths.place(a)[1] for a in targets]
            self.add("read", "open", f"opens {base}", name=base, paths=files)
            return
        self.add("program", "program", f"runs the program {path or name}", name=f"{path or name} {' '.join(args or [])}"[:160],
                 paths=[path] if path else [], args=args)

    def opened(self, target: str, base: str) -> bool:
        """A program given an address or a file it would run (explorer x.bat, explorer https://...): sorted as
        that, True; a plain file to show, False."""
        scheme = re.match(r"^([a-z][a-z0-9+.-]+):", target, re.IGNORECASE)
        if scheme and scheme.group(1).lower() in ("http", "https"):
            host = (urlsplit(target).hostname or "").lower()
            if _local_host(host):
                self.add("system", "loopback", f"opens this computer's own services at {host}")
            else:
                self.add("network", "browse", f"opens {target[:120]} in the browser", host=host or target[:40],
                         sends=True)
            return True
        if scheme:
            self.add("program", "program", f"{base} opens {target[:120]}", name=target[:160])
            return True
        if os.path.splitext(target)[1].lower() in OPEN_RISKY:
            place = self.paths.place(target)[1]
            self.add("program", "program", f"{base} runs {target}", name=target[:160], paths=[place])
            return True
        return False

    def pip(self, words: list[str], base: str) -> None:
        if words and words[0] in ("list", "show", "freeze", "check", "--version", "-v"):
            self.add("read", "apps", f"lists what {base} installed")
        else:
            self.add("install", "install", f"runs pip {' '.join(words)[:80]}", name=f"pip {' '.join(words)}"[:120])

    def start(self, node: dict, name: str) -> None:
        if name == "start-process":
            verbs = self.paths.strings(_bound(node, "Verb")) if _bound(node, "Verb") is not None else []
            verb = " ".join(verbs or []).lower()
            if verbs is None or "runas" in verb or _bound(node, "Credential") is not None:
                self.add("elevate", "elevate", "starts a program as administrator or as another user")
                return
            targets = self.paths.strings(_bound(node, "FilePath"))
            if targets is None:
                self.add("hidden", "hidden", "starts a program whose name is computed")
                return
            arguments = self.paths.strings(_bound(node, "ArgumentList")) if _bound(node, "ArgumentList") is not None else []
        else:
            targets = None if not any(_bound(node, p) is not None for p in ("Path", "LiteralPath")) else \
                self.paths.strings(_bound(node, "Path") or _bound(node, "LiteralPath"))
            arguments = []
        if targets is None or arguments is None:
            self.add("unknown", "program", "starts something whose name is computed")
            return
        for target in targets:
            scheme = re.match(r"^([a-z][a-z0-9+.-]+):", target, re.IGNORECASE)
            if scheme and scheme.group(1).lower() in ("http", "https"):
                host = (urlsplit(target).hostname or "").lower()
                if _local_host(host):
                    self.add("system", "loopback", f"opens this computer's own services at {host}")
                else:
                    self.add("network", "browse", f"opens {target[:120]} in the browser", host=host or target[:40],
                             sends=True)
                continue
            if scheme:
                self.add("program", "program", f"opens {target[:120]}", name=target[:160])
                continue
            base = os.path.splitext(os.path.basename(target))[0].lower()
            ext = os.path.splitext(target)[1].lower()
            place = self.paths.place(target)[1]
            exists = os.path.exists(place)
            if base in EXE_NEVER:
                self.add(EXE_NEVER[base], EXE_NEVER[base], f"starts {base}")
            elif ext in INSTALLERS:
                self.add("install", "install", f"starts the installer {target}", name=target, paths=[place] if exists else [])
            elif ext in OPEN_RISKY or (not exists and not ext):
                if base in EXE_OPEN and not arguments:
                    self.add("read", "open", f"opens {base}", name=base)
                else:
                    self.add("program", "program", f"starts {target} {' '.join(arguments)}".strip()[:160],
                             name=f"{target} {' '.join(arguments)}".strip()[:160], paths=[place] if exists else [])
            else:
                self.add("read", "open", f"opens {place} in its own program", paths=[place], name=os.path.basename(place))

    def new_object(self, node: dict) -> None:
        if _bound(node, "ComObject") is not None:
            # Shell, scripting and task objects run programs, elevate and schedule outside PowerShell's reading.
            self.add("hidden", "hidden", "makes a COM object, which can run things that cannot be read first")
            return
        names = self.paths.strings(_bound(node, "TypeName"))
        if names is None:
            self.add("hidden", "hidden", "makes an object of a computed type")
            return
        for name in names:
            low = name.lower()
            if any(low == t or low.startswith(t + ".") for t in NET_TYPES):
                self.add("network", "net", f"makes a {name} to reach the network", sends=True)
            elif any(low == t.rstrip(".") or low.startswith(t if t.endswith(".") else t + ".") for t in NEVER_TYPES):
                self.add("hidden", "hidden", f"makes a {name}, which can run code that cannot be read first")
            elif low not in ("psobject", "pscustomobject", "system.collections.arraylist", "collections.arraylist",
                             "system.text.stringbuilder", "text.stringbuilder", "datetime", "system.datetime",
                             "system.collections.hashtable", "hashtable", "timespan", "system.timespan",
                             "system.io.fileinfo", "io.fileinfo", "system.io.directoryinfo", "io.directoryinfo",
                             "system.io.driveinfo", "io.driveinfo") and not low.startswith(
                                 ("system.collections.generic.", "collections.generic.")):
                self.add("unknown", "unknown", f"makes a {name}")

    def import_module(self, node: dict) -> None:
        names = self.paths.strings(_bound(node, "Name"))
        if names is None:
            self.add("hidden", "hidden", "loads a module whose name is computed")
            return
        for name in names:
            if re.search(r"[\\/]|\.ps[md]?1$|\.dll$", name, re.IGNORECASE):
                self.add("program", "script", f"loads the module file {name}", name=name)
            elif name.lower() not in INBOX_MODULES and not name.lower().startswith("microsoft."):
                self.add("program", "script", f"loads the module {name}, whose code cannot be read first", name=name)


def _item_type(typed: str | None, name: str) -> str:
    """New-Item's -ItemType as PowerShell matches it (any leading part: "J" is a junction); "unknown" when
    computed."""
    if typed is None:
        return "unknown"
    typed = typed.strip().lower()
    if not typed:
        return "file" if name == "new-item" else "dir"
    for full, kind in (("directory", "dir"), ("container", "dir"), ("file", "file"), ("symboliclink", "link"),
                       ("junction", "link"), ("hardlink", "link")):
        if full.startswith(typed):
            return kind
    return "unknown"


def _unzip_overwrites(archive: str, dest: str) -> bool:
    """Expand-Archive -Force would replace files already in `dest`."""
    try:
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
    except (OSError, zipfile.BadZipFile):
        return True
    return any(os.path.isfile(os.path.join(dest, n)) for n in names)


def _copy_overwrites(src: str, target: str) -> bool:
    """Copy-Item -Recurse of the folder `src` to `target` would replace files already there."""
    found = _walk(src)
    if found is None:
        return True
    return any(os.path.isfile(os.path.join(target, os.path.relpath(p, src))) for p in found if os.path.isfile(p))


def read(command: str, cwd: str) -> Reading:
    """Reads and sorts one command to be run in `cwd`. A command that does not parse comes back with errors."""
    if len(command) > COMMAND_MAX:
        return Reading(command, cwd, [], [f"the command is longer than {COMMAND_MAX} characters"])
    parsed = PARSER.read(command, cwd)
    if parsed is None or parsed.get("fatal"):
        return Reading(command, cwd, [Act("unknown", "unknown", "the app could not read this command")], [])
    errors = [str(e) for e in _items(parsed.get("errors"))]
    sorter = Sorter(parsed, cwd)
    if not errors:
        sorter.sort()
    acts = sorter.acts
    if any(a.kind == "network" and a.paths for a in acts) and any(a.kind in ("program", "install") for a in acts):
        acts.append(Act("hidden", "download_run", "downloads something and runs it in the same command"))
    return Reading(command, cwd, acts, errors)


# ---------------------------------------------------------------- judging against the grants

@dataclass
class Verdict:
    """What to do with a command: "run", "ask", "folder" (grant a folder first), "refuse", "invalid" or "bin" (a
    delete past the Recycle Bin, to be written another way)."""
    action: str
    reasons: list[Act]
    folder: str = ""
    deletes: list[dict] = field(default_factory=list)
    overwrites: list[str] = field(default_factory=list)
    root: str = ""
    # For "folder": the folder must be one that may be changed, not only read.
    write: bool = False


@functools.lru_cache(maxsize=1)
def _known() -> tuple:
    return tuple(known_folders())


def folder_for(path: str) -> str:
    """The folder to ask for so that `path` is inside: a known folder holding it, else the nearest folder."""
    real = norm(path)
    for row in _known():
        if under(real, norm(row["path"])):
            return row["path"]
    place = path
    while place and not os.path.isdir(place):
        parent = os.path.dirname(place)
        if parent == place:
            return ""
        place = parent
    return place


def _escaping_links(folder: str, roots: list[str], most: int = 20000) -> list[str]:
    """Links and junctions under `folder` that lead outside the granted folders (a recursive walk would follow)."""
    found, seen, stack = [], 0, [folder]
    while stack and seen < most:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            if entry.is_symlink() or (entry.is_dir(follow_symlinks=False) and running._junction(entry.path)):
                if not any(under(norm(entry.path), r) for r in roots):
                    found.append(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                stack.append(entry.path)
    return found


def _pattern_reaches(pattern: str, root: str, deep: bool) -> bool:
    """A wildcard path matches `root` or something inside it (or, with `deep`, a folder above it)."""
    p = [c for c in re.split(r"[\\/]", os.path.normcase(pattern)) if c]
    r = [c for c in re.split(r"[\\/]", root) if c]
    n = min(len(p), len(r))
    if not all(fnmatch.fnmatch(r[i], p[i]) for i in range(n)):
        return False
    return len(p) >= len(r) or deep


def _reaches_protected(act: Act, places) -> str:
    """For a wildcard, or a recursive read of contents: a protected place it reaches ("" none, "?" too many files to
    tell). Named places are checked one by one in judge."""
    # Zipping and copying read whole files too; zipping a folder always takes everything in it.
    content = act.what in (*CONTENT_READS, "zip", "copy") or act.write
    deep = content and (act.recurse or act.what == "zip")
    for glob in act.globs:
        if any(c in glob for c in "*?["):
            literal = _base(glob)[0]
            for root in (places.home, places.package, places.python, places.appdata,
                         os.path.join(places.windows, "system32", "config")):
                if root == places.appdata and places.temp and under(norm(literal), places.temp):
                    continue
                if _pattern_reaches(glob, root, deep):
                    return root
            p = [c for c in re.split(r"[\\/]", os.path.normcase(glob)) if c]
            r = [c for c in re.split(r"[\\/]", places.profile) if c]
            if len(p) > len(r) and _pattern_reaches(glob, places.profile, False) and (
                    fnmatch.fnmatch(".x", p[len(r)]) or p[len(r)].startswith(".")) and (len(p) > len(r) + 1 or content):
                return places.profile + "\\."
            for place in _matches(glob):
                if places.blocked(norm(place), act.write):
                    return place
        elif deep:
            for root in (places.home, places.package, places.python, places.appdata):
                if under(root, norm(glob)):
                    return root
        if deep and os.path.isdir(_base(glob)[0]):
            found = _walk(_base(glob)[0])
            if found is None:
                return "?"
            for place in found:
                if places.blocked(os.path.normcase(place), act.write):
                    return place
    return ""


def _targets(act: Act) -> list[str] | None:
    """What a wildcard names now, found in Python as PowerShell would match it (a listing's items come already
    listed). None when there are more than can be told first."""
    found: list[str] = []
    for glob in act.globs:
        found += _matches(glob) if any(c in glob for c in "*?[") else [glob]
    found = [p for p in dict.fromkeys(found) if os.path.lexists(p)]
    # A folder taken whole leaves out what is inside it.
    found = [p for p in found if not any(under(norm(p), norm(q)) and norm(p) != norm(q) for q in found)]
    return found if len(found) <= 5000 else None


def judge(reading: Reading, grants: Grants) -> Verdict:
    """Holds a reading against the never list and the person's grants, with the real list of what a delete moves
    to the Recycle Bin and what a wildcard copy or move replaces, found in Python."""
    if reading.errors:
        return Verdict("invalid", [Act("unknown", "invalid", "; ".join(reading.errors)[:400])])
    places = grants.places
    refused = [a for a in reading.acts if a.kind in NEVER_KINDS]
    asks: list[Act] = []
    for act in reading.acts:
        reached = _reaches_protected(act, places)
        if reached == "?":
            asks.append(Act("unknown", "unresolved", f"{act.why}, over more files than can be checked first",
                            paths=act.paths))
        elif reached:
            refused.append(Act("secrets", "secrets", f"{act.why}, which reaches {reached}, which is protected",
                               paths=[reached]))
    for act in reading.acts:
        for path in act.paths + ([act.dest] if act.dest else []):
            why = places.blocked(norm(path), act.write or path == act.dest)
            # A program's own file (user installs live in AppData) is run, not read: only the app's own refuse.
            if why and why != "app" and act.args is not None and path == act.paths[0]:
                continue
            if why:
                whose = "this app's own files" if why == "app" else "protected"
                refused.append(Act("secrets" if why in ("app", "secrets") else "system", why,
                                   f"touches {path}, which is {whose}", paths=[path]))
    if refused:
        return Verdict("refuse", refused)
    past_bin = [a for a in reading.acts if a.what == "skip_bin"]
    if past_bin:
        return Verdict("bin", past_bin)
    roots, writable = grants.roots(), grants.roots(write=True)

    def folder_to_grant(path: str) -> str:
        # A read-only granted folder is asked for again, to change in; else the folder that would hold `path`.
        held = grants.root_of(norm(path))
        return grants.folder_of(held) if held else folder_for(path)

    asks += [a for a in reading.acts if a.kind in ASK_KINDS]
    # A place computed only as it runs cannot be checked first: asked (a new name in the same folder is the
    # exception, Rename-Item cannot move a file).
    asks += [Act("unknown", "unresolved", f"{a.why}, at places computed only when it runs", paths=a.paths)
             for a in reading.acts if a.unresolved and (a.kind == "read" or (
                 a.kind in ("change", "overwrite") and not (a.what == "rename" and a.dest_unresolved)))]
    verdict = Verdict("run", [])
    # A program's own file is outside the granted folders by nature; running it is asked as a program.
    every = [(act, p) for act in reading.acts for p in act.paths + ([act.dest] if act.dest else [])
             if not (act.args is not None and p == act.paths[0])]
    for act, path in every:
        write = act.write or path == act.dest
        if any(under(norm(path), r) for r in (writable if write else roots)):
            continue
        folder = folder_to_grant(path)
        if folder and not places.grantable(norm(folder)):
            return Verdict("folder", [act], folder=folder, write=write)
        asks.append(Act("outside", "outside", f"{act.why}; {path} is outside the folders they granted", paths=[path],
                        write=act.write))
    for act in reading.acts:
        if act.what == "move" and act.dest and act.paths:
            src_roots = {grants.root_of(norm(p)) for p in act.paths}
            if src_roots != {grants.root_of(norm(act.dest))}:
                asks.append(Act("move_out", "move_out", f"moves out of {', '.join(sorted(src_roots))}",
                                paths=act.paths, dest=act.dest))
        if act.recurse and act.kind in ("read", "change", "delete"):
            for path in act.paths:
                if os.path.isdir(path):
                    for link in _escaping_links(path, roots):
                        asks.append(Act("outside", "outside", f"{link} inside leads outside the granted folders",
                                        paths=[link]))
    for act in reading.acts:
        if act.unresolved or act.kind not in ("delete", "change", "overwrite"):
            continue
        found = _targets(act) if act.wild else list(act.paths)
        if found is None:
            asks.append(Act("unknown", "unresolved", f"{act.why}, over more files than can be listed first",
                            paths=act.paths))
            continue
        if act.kind == "delete":
            verdict.deletes += [{"path": p, **running.listing(p)} for p in found]
        elif act.dest and act.what in ("copy", "move"):
            # A wildcard copy or move: each item lands in the folder by its own name, where a file may be already.
            for src in found if act.wild else []:
                target = os.path.join(act.dest, os.path.basename(src)) if os.path.isdir(act.dest) else act.dest
                if os.path.isfile(target) and os.path.normcase(target) != os.path.normcase(src):
                    verdict.overwrites.append(target)
        elif act.wild and act.what in ("write", "attr", "empty"):
            verdict.overwrites += [p for p in found if os.path.isfile(p)]
    if verdict.overwrites and not any(a.kind == "overwrite" for a in asks):
        asks.append(Act("overwrite", "overwrite", f"overwrites {', '.join(verdict.overwrites[:5])}",
                        paths=verdict.overwrites))
    verdict.root = next((grants.root_of(norm(p)) for _, p in every if grants.root_of(norm(p))), "")
    if asks:
        verdict.action, verdict.reasons = "ask", asks
    return verdict
