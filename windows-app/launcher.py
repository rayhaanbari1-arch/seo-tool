"""
Windows launcher for SEO Event Tracker.

Opens as a self-contained native window by launching the bundled Chromium
browser in --app mode (no address bar, own taskbar icon, no browser chrome).
The same Chromium binary is used for both the UI window and screenshot capture.
"""
import sys
import os
import glob
import threading
import webbrowser
import time
import subprocess
import urllib.request
import json

# ── Path setup (must run before any app imports) ──────────────────────────────
_IS_BUNDLED = getattr(sys, 'frozen', False)

if _IS_BUNDLED:
    _BUNDLE_DIR = sys._MEIPASS
    sys.path.insert(0, _BUNDLE_DIR)

    # Use %LOCALAPPDATA% directly — more reliable than computing from sys.executable
    _LOCALAPPDATA = os.environ.get('LOCALAPPDATA', os.path.expanduser('~'))
    _INSTALL_ROOT = os.path.join(_LOCALAPPDATA, 'SEO-Event-Tracker')

    # User data (DB, Chromium UI profile) — lives in %APPDATA%, survives reinstalls
    _DATA_DIR = os.path.join(
        os.environ.get('APPDATA', os.path.expanduser('~')),
        'SEO-Event-Tracker'
    )
    os.makedirs(_DATA_DIR, exist_ok=True)

    _db_file = os.path.join(_DATA_DIR, 'seo_tracker.db')
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + _db_file)

    # Point Playwright to the browsers bundled by the installer
    _browsers_dir = os.path.join(_INSTALL_ROOT, 'browsers')
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _browsers_dir  # set regardless; Playwright checks existence
else:
    # Dev mode — project root is one level above windows-app/
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _PROJECT_ROOT)
    _DATA_DIR     = os.path.join(_PROJECT_ROOT, 'instance')
    _browsers_dir = None

# ── Version (written at build time by GitHub Actions) ─────────────────────────
try:
    from version import COMMIT_SHA
except ImportError:
    COMMIT_SHA = 'dev'

REPO         = 'rayhaanbari1-arch/seo-tool'
RELEASES_URL = f'https://github.com/{REPO}/releases/latest'

# ── Flask app ─────────────────────────────────────────────────────────────────
from app import app, db  # noqa: E402

PORT = 5000
URL  = f'http://127.0.0.1:{PORT}'


# ── Chromium window ───────────────────────────────────────────────────────────

def _find_chromium() -> str | None:
    """
    Find chrome.exe inside the installer-bundled playwright browsers folder.
    Playwright stores it as: browsers/chromium-REVISION/chrome-win/chrome.exe
    Writes a debug log to %APPDATA%\SEO-Event-Tracker\launcher.log
    """
    browsers_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '')
    pattern = os.path.join(browsers_path, 'chromium-*', 'chrome-win', 'chrome.exe')
    matches = glob.glob(pattern)
    result = matches[0] if matches else None

    # Write a small debug log so we can diagnose path issues
    try:
        log_path = os.path.join(_DATA_DIR, 'launcher.log')
        with open(log_path, 'w') as f:
            f.write(f'PLAYWRIGHT_BROWSERS_PATH = {browsers_path}\n')
            f.write(f'browsers dir exists      = {os.path.isdir(browsers_path)}\n')
            f.write(f'glob pattern             = {pattern}\n')
            f.write(f'matches                  = {matches}\n')
            f.write(f'chromium_exe             = {result}\n')
            f.write(f'sys.executable           = {sys.executable}\n')
            f.write(f'_IS_BUNDLED              = {_IS_BUNDLED}\n')
    except Exception:
        pass

    return result


def _launch_app_window(chromium_exe: str) -> subprocess.Popen:
    """
    Open the app in Chromium's --app mode: native-looking window,
    no address bar, no bookmarks, own taskbar icon.
    """
    profile_dir = os.path.join(_DATA_DIR, 'ui-profile')
    os.makedirs(profile_dir, exist_ok=True)

    return subprocess.Popen([
        chromium_exe,
        f'--app={URL}',
        '--window-size=1280,820',
        '--no-first-run',
        '--disable-default-apps',
        '--no-default-browser-check',
        '--disable-extensions',
        f'--user-data-dir={profile_dir}',
    ])


# ── Update checker ────────────────────────────────────────────────────────────

def _msgbox(text: str, title: str, flags: int) -> int:
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        return 0


def _check_for_update() -> bool:
    if COMMIT_SHA == 'dev':
        return False
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{REPO}/commits/main',
            headers={'User-Agent': 'SEO-Event-Tracker-Updater'}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data   = json.loads(resp.read())
            latest = data.get('sha', '')
            return bool(latest) and latest != COMMIT_SHA
    except Exception:
        return False


def _handle_update():
    if not _check_for_update():
        return
    choice = _msgbox(
        (
            "A new version of SEO Event Tracker is available!\n\n"
            "Download the latest installer now?"
        ),
        "SEO Event Tracker — Update Available",
        0x24,  # MB_YESNO | MB_ICONQUESTION
    )
    if choice == 6:  # IDYES
        webbrowser.open(RELEASES_URL)


# ── Flask runner ──────────────────────────────────────────────────────────────

def _run_flask():
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with app.app_context():
        db.create_all()

    # Update check runs in background — never blocks startup
    threading.Thread(target=_handle_update, daemon=True).start()

    chromium_exe = _find_chromium()

    if chromium_exe:
        # ── Installed mode ────────────────────────────────────────────────────
        # Flask runs on a daemon thread; bundled Chromium provides the UI window.
        # Process exits cleanly when the user closes the window.
        threading.Thread(target=_run_flask, daemon=True).start()
        time.sleep(1.0)  # Give Flask a moment to bind the port
        proc = _launch_app_window(chromium_exe)
        proc.wait()      # Block until the window is closed

    else:
        # ── Dev / fallback mode ───────────────────────────────────────────────
        # Open in the system browser and run Flask on the main thread.
        threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
        _run_flask()


if __name__ == '__main__':
    main()
