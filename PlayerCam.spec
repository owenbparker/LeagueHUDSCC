# PyInstaller build for SCC Player Cam, the players' webcam app.
#
#   pip install -r requirements-windows.txt pyinstaller
#   pyinstaller --noconfirm PlayerCam.spec
#
# Produces dist/PlayerCam/PlayerCam.exe (plus its _internal folder). Only the
# cam page and what it uses are bundled, not the HUD.
import os
import sys

root = SPECPATH
icon = os.path.join(root, "packaging", "playercam.ico")
web = os.path.join(root, "web")
datas = [(os.path.join(web, "cam.html"), "web"), (os.path.join(web, "update.js"), "web"),
         (os.path.join(web, "fonts", "Inter.ttf"), os.path.join("web", "fonts")),
         (os.path.join(web, "assets", "playercam_icon.png"), os.path.join("web", "assets")),
         (os.path.join(web, "assets", "games", "lol.svg"), os.path.join("web", "assets", "games")),
         (os.path.join(web, "assets", "games", "valorant.svg"), os.path.join("web", "assets", "games")),
         (os.path.join(web, "assets", "games", "cod.svg"), os.path.join("web", "assets", "games"))]
hidden = []
if sys.platform == "win32":
    # pywebview's Windows window (Edge WebView2) is loaded at runtime.
    hidden += ["webview.platforms.winforms", "webview.platforms.edgechromium", "clr"]

a = Analysis(
    [os.path.join(root, "PlayerCam.py")],
    pathex=[root],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PIL", "psd_tools", "winrt", "numpy", "cryptography"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PlayerCam",
    console=False,
    icon=icon if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PlayerCam")
