"""
Windows launcher for SEO Event Tracker.
Starts the Flask server and opens the browser automatically.
On first run, detects missing Playwright Chromium and offers to install it.
"""
import sys
import os
import threading
import webbrowser
import time
import subprocess

# ── Path setup (must run before any app imports) ──────────────────────────────
_IS_BUNDLED = getattr(sys, 'frozen', False)

if _IS_BUNDLED:
    # Resources extracted to _MEIPASS by PyInstaller
    _BUNDLE_DIR = sys._MEIPASS
    sys.path.insert(0, _BUNDLE_DIR)

    # Persist database in %APPDATA%\SEO-Event-Tracker (writable, survives updates)
    _DATA_DIR = os.path.join(
        os.environ.get('APPDATA', os.path.expanduser('~')),
        'SEO-Event-Tracker'
    )
    os.makedirs(_DATA_DIR, exist_ok=True)

    _db_file = os.path.join(_DATA_DIR, 'seo_tracker.db')
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + _db_file)
else:
    # Dev mode — go up one level to project root
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _PROJECT_ROOT)

# ── Import Flask app (after path/env setup) ───────────────────────────────────
from app import app, db  # noqa: E402

PORT = 5000
URL = f'http://127.0.0.1:{PORT}'


# ── Playwright helpers ────────────────────────────────────────────────────────

def _chromium_ok() -> bool:
    """Return True if Playwright can launch Chromium."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _install_chromium() -> bool:
    """
    Run 'playwright install chromium' using Playwright's bundled Node driver.
    Opens a visible console window so the user can watch the download progress.
    Returns True on success.
    """
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        driver = compute_driver_executable()
        env = get_driver_env()

        result = subprocess.run(
            [str(driver), 'install', 'chromium'],
            env=env,
            # Open a separate console window so the user can see download progress
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return result.returncode == 0
    except Exception as exc:
        print(f'[launcher] Chromium install error: {exc}')
        return False


def _msgbox(text: str, title: str, flags: int) -> int:
    """Thin wrapper around the Windows MessageBox API."""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        return 0


def _handle_missing_chromium():
    """
    Prompt the user to install Chromium. Called in a background thread so
    the Flask server can start while the dialog is open.
    """
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

    # A separate console window will open showing download progress
    success = _install_chromium()

    if success:
        _msgbox(
            "Chromium installed successfully!\n\nScreenshot capture is now ready.",
            "SEO Event Tracker — Setup Complete",
            MB_ICONINFO,
        )
    else:
        _msgbox(
            (
                "Chromium installation failed.\n\n"
                "You can try manually — open a terminal (cmd / PowerShell) and run:\n"
                "    playwright install chromium"
            ),
            "SEO Event Tracker — Installation Failed",
            MB_ICONERROR,
        )


# ── Browser auto-open ─────────────────────────────────────────────────────────

def _open_browser():
    time.sleep(1.5)
    webbrowser.open(URL)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Check Chromium in a background thread — don't block server startup
    if not _chromium_ok():
        threading.Thread(target=_handle_missing_chromium, daemon=True).start()

    with app.app_context():
        db.create_all()

    threading.Thread(target=_open_browser, daemon=True).start()

    print(f'SEO Event Tracker running at {URL}')
    print('Close this window to stop the app.')
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    main()
