"""Where files live, run from source or as a packaged app (hod-chest.exe, built with PyInstaller).

RES:    files shipped inside the app (the web page). In a one-file build they're unpacked to a
        temporary folder for each run, so nothing the user makes may be saved there.
DATA:   files the app makes and keeps (item names, the chest log, saved positions): next to the
        .exe when packaged, else this folder.
CHEATS: the cheats' saved files (positions.json, loot_backup.json), in DATA/cheats.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = bool(getattr(sys, 'frozen', False))
RES = getattr(sys, '_MEIPASS', HERE)
DATA = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else HERE
CHEATS = os.path.join(DATA, 'cheats')


def cheat_file(name):
    """A cheat's saved file, making the cheats folder when packaged (it may not exist yet)."""
    os.makedirs(CHEATS, exist_ok=True)
    return os.path.join(CHEATS, name)
