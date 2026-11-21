# PyInstaller: mibo as one folder with a program and no console.
# build.ps1 runs it on Windows; the release workflow runs it on macOS too,
# where the folder becomes mibo.app. It freezes the installed copy
# (pip install ".[app,web]"), so mibo/_bundled is there.
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

import mibo

MACOS = sys.platform == "darwin"
package = Path(mibo.__file__).resolve().parent
static = package / "static"

# The package's own files (pages, locales, portraits); _bundled (the seed data) is
# added whole below.
datas = [d for d in collect_data_files("mibo") if "_bundled" not in Path(d[1]).parts]
for folder, dirs, files in os.walk(package / "_bundled"):
    dirs[:] = [d for d in dirs if d != "__pycache__"]
    for name in files:
        datas.append((os.path.join(folder, name), str(Path("mibo") / Path(folder).relative_to(package))))
# The time zone database zoneinfo reads on Windows, for scheduled tasks (schedule.py).
datas += collect_data_files("tzdata")
# keyring finds the credential store through its own entry points (secretstore.py).
datas += copy_metadata("keyring")
# trafilatura reads its settings.cfg on import and jusText its stop-word lists (web/fetch.py).
datas += collect_data_files("trafilatura") + collect_data_files("justext")

# Imported by name: the commands, the panels, the chat languages;
# uvicorn's protocols, ddgs's engines and keyring's backends the same way.
hiddenimports = (collect_submodules("mibo") + collect_submodules("uvicorn")
                 + collect_submodules("ddgs") + collect_submodules("keyring.backends"))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    datas=datas,
    hiddenimports=hiddenimports,
    # Test and build tools a dependency may name; nothing at run time imports them.
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
# Windows: the program draws at each monitor's own scale (per-monitor v2) from before Python
# starts, as window.py asks too; otherwise a monitor scaled unlike the main one shows it
# stretched and blurred. PyInstaller adds the rest it always puts in (common controls, asInvoker).
MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{e2011457-1546-43c5-a5fe-008deee3d3f0}"></supportedOS>
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}"></supportedOS>
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}"></supportedOS>
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}"></supportedOS>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"></supportedOS>
    </application>
  </compatibility>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
      <dpiAware xmlns="http://schemas.microsoft.com/SMI/2005/WindowsSettings">true/pm</dpiAware>
      <dpiAwareness xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">PerMonitorV2</dpiAwareness>
    </windowsSettings>
  </application>
</assembly>
"""
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mibo",
    console=False,
    icon=str(static / ("app.png" if MACOS else "app.ico")),
    manifest=None if MACOS else MANIFEST,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="mibo", upx=False)

if MACOS:
    app = BUNDLE(
        coll,
        name="mibo.app",
        icon=str(static / "app.png"),
        bundle_identifier="com.github.wangkant.mibo",
        info_plist={
            "CFBundleName": "mibo",
            "CFBundleShortVersionString": mibo.__version__,
            "CFBundleVersion": mibo.__version__,
            "NSHighResolutionCapable": True,
        },
    )
