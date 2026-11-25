# PyInstaller build for hod-chest: one file, hod-chest.exe on Windows (hod-chest elsewhere).
#   pip install pyinstaller
#   pyinstaller hod-chest.spec          -> dist/hod-chest(.exe)
# The GitHub Actions workflow (.github/workflows/build.yml) runs this on Windows.

a = Analysis(
    ['hod_chest.py'],
    pathex=['.'],
    datas=[('web', 'web')],                         # the page, served from inside the app
    # Loaded by name at run time (cheats from the page; item names on first run), so list them.
    hiddenimports=['cheats.positions', 'cheats.loot', 'cheats.mastery', 'cheats.health',
                   'gen_item_names', 'build_names', 'txrc', 'pool'],
    excludes=['tkinter'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='hod-chest',
    console=True,                                   # the window shows the text view; close it to quit
    upx=False,
)
