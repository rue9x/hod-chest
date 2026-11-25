"""hod-chest as one app: starts the watcher with the live web page and opens it in your browser.

This is what hod-chest.exe runs. From source it's the same as:

  python3 chest_watch.py --serve 8765      (then open http://localhost:8765/)

Any chest_watch.py options can be added, e.g. hod-chest.exe --want "you: Valmanway +1".
Close the window (or press Stop watcher on the page) to quit.
"""
import sys
import threading
import webbrowser

import chest_watch

PORT = 8765


def main():
    args = sys.argv[1:]
    if '--serve' not in args:
        args = ['--serve', str(PORT)] + args
    port = args[args.index('--serve') + 1]
    url = f'http://localhost:{port}/'
    print(f'hod-chest: the live table is at {url} (opening it in your browser)', flush=True)
    threading.Timer(1.5, webbrowser.open, (url,)).start()
    sys.argv = [sys.argv[0]] + args
    chest_watch.main()


if __name__ == '__main__':
    main()
