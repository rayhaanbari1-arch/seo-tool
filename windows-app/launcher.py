"""
Windows launcher for SEO Event Tracker.
- Starts the Flask server and opens the browser automatically.
- On first run: detects missing Playwright Chromium and offers to install it.
- On every run: checks GitHub for updates and shows a popup if one is available.
"""
import sys
import os
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

    _DATA_DIR = os.path.join(
        os.environ.get('APPDATA', os.path.expanduser('~')),
        'SEO-Event-Tracker'
    )
    os.makedirs(_DATA_DIR, exist_ok=True)

    _db_file = os.path.join(_DATA_DIR, 'seo_tracker.db')
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + _db_file)
else:
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _PROJECT_ROOT)

# ── Version (written at build time by GitHub Actions) ─────────────────────────
try:
    from version import COMMIT_SHA
except ImportError:
    COMMIT_SHA = 'dev'

REPO          = 'rayhaanbari1-arch/seo-tool'
RELEASES_URL  = f'https://github.com/{REPO}/releases/latest'
API_LATEST    = f'https://api.github.com/repos/{REPO}/commits/main'

# ── Import Flask app ──────────────────────────────────────────────────────────
from app import app, db  # noqa: E402

PORT = 5000
URL  = f'http://127.0.0.1:{PORT}'


# ── Windows helpers ───────────────────────────────────────────────────────────

def _msgbox(text: str, title: str, flags: int) -> int:
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        return 0


# ── Playwright install ────────────────────────────────────────────────────────

def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
        return True
    except Exception:
        return False


def _install_chromium() -> bool:
    """
    Run 'playwright install chromium' using the bundled Node driver.
    Uses cmd.exe /c so .cmd files execute correctly on Windows.
    Opens a visible console window showing download progress.
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver = str(compute_driver_executable())
        env    = get_driver_env()

        # .cmd files must be invoked via cmd.exe on Windows
        cmd = ['cmd.exe', '/c', driver, 'install', 'chromium']

        result = subprocess.run(
            cmd,
            env=env,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return result.returncode == 0
    except Exception as exc:
        print(f'[launcher] Chromium install error: {exc}')
        return False


def _handle_missing_chromium():
    MB_YESNO        = 0x04
    MB_ICONQUESTION = 0x20
    MB_ICONINFO     = 0x40
    MB_ICONERROR    = 0x10
    IDYES           = 6

    choice = _msgbox(
        (
            "Playwright Chromium is required for screenshot capture "
            "but is not installed on this machine.\n\n"
            "Install it now?  (~130 MB download)\n\n"
            "• Yes — download and install automatically\n"
            "• No  — skip for now (screenshots won't work)"
        ),
        "SEO Event Tracker — First-time Setup",
        MB_YESNO | MB_ICONQUESTION,
    )

    if choice != IDYES:
        return

    success = _install_chromium()

    if success:
        _msgbox(
            "Chromium installed successfully!\nScreenshot capture is now ready.",
            "SEO Event Tracker — Setup Complete",
            MB_ICONINFO,
        )
    else:
        _msgbox(
            (
                "Chromium installation failed.\n\n"
                "Please open a terminal (cmd / PowerShell) and run:\n"
                "    python -m playwright install chromium"
            ),
            "SEO Event Tracker — Installation Failed",
            MB_ICONERROR,
        )


# ── Auto-update checker ───────────────────────────────────────────────────────

def _check_for_update() -> bool:
    """Return True if the remote main branch is ahead of this build."""
    if COMMIT_SHA == 'dev':
        return False
    try:
        req = urllib.request.Request(
            API_LATEST,
            headers={'User-Agent': 'SEO-Event-Tracker-Updater'}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data    = json.loads(resp.read())
            latest  = data.get('sha', '')
            return bool(latest) and latest != COMMIT_SHA
    except Exception:
        return False


def _handle_update():
    if not _check_for_update():
        return

    MB_YESNO   = 0x04
    MB_ICONINFO = 0x40
    IDYES       = 6

    choice = _msgbox(
        (
            "A new version of SEO Event Tracker is available!\n\n"
            "Click Yes to open the download page.\n"
            "Click No to continue with the current version."
        ),
        "SEO Event Tracker — Update Available",
        MB_YESNO | MB_ICONINFO,
    )

    if choice == IDYES:
        webbrowser.open(RELEASES_URL)


# ── Browser open ──────────────────────────────────────────────────────────────

def _open_browser():
    time.sleep(1.5)
    webbrowser.open(URL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Both checks run in background threads — server starts immediately
    if not _chromium_ok():
        threading.Thread(target=_handle_missing_chromium, daemon=True).start()

    threading.Thread(target=_handle_update, daemon=True).start()

    with app.app_context():
        db.create_all()

    threading.Thread(target=_open_browser, daemon=True).start()

    print(f'SEO Event Tracker running at {URL}')
    print('Close this window to stop the app.')
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
