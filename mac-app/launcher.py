"""
macOS launcher for SEO Event Tracker.

Opens as a self-contained native window by launching the bundled Chromium
browser in --app mode (no address bar, own dock icon, no browser chrome).
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

    # Inside the .app bundle:
    #   sys.executable = .../SEO-Event-Tracker.app/Contents/MacOS/SEO-Event-Tracker
    #   Resources       = .../SEO-Event-Tracker.app/Contents/Resources/
    _MACOS_DIR  = os.path.dirname(sys.executable)
    _CONTENTS   = os.path.dirname(_MACOS_DIR)
    _RESOURCES  = os.path.join(_CONTENTS, 'Resources')
    _browsers_dir = os.path.join(_RESOURCES, 'browsers')

    # User data lives in ~/Library/Application Support — survives app updates
    _DATA_DIR = os.path.join(
        os.path.expanduser('~'), 'Library', 'Application Support', 'SEO-Event-Tracker'
    )
    os.makedirs(_DATA_DIR, exist_ok=True)

    _db_file = os.path.join(_DATA_DIR, 'seo_tracker.db')
    os.environ.setdefault('DATABASE_URL', 'sqlite:///' + _db_file)
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = _browsers_dir

else:
    # Dev mode
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, _PROJECT_ROOT)
    _DATA_DIR     = os.path.join(_PROJECT_ROOT, 'instance')
    _browsers_dir = None

# ── Version ───────────────────────────────────────────────────────────────────
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


# ── Chromium helpers ──────────────────────────────────────────────────────────

def _find_chromium() -> str | None:
    """
    Find the Chromium binary inside the bundled browsers folder.
    On macOS, Playwright stores it as:
      chromium-REVISION/chrome-mac/Chromium.app/Contents/MacOS/Chromium
    """
    browsers_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '')
    pattern = os.path.join(
        browsers_path, 'chromium-*', 'chrome-mac',
        'Chromium.app', 'Contents', 'MacOS', 'Chromium'
    )
    matches = glob.glob(pattern)
    result = matches[0] if matches else None

    # Debug log
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
    """Open the app in Chromium --app mode: no address bar, own dock icon."""
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


# ── Native macOS dialogs via osascript ────────────────────────────────────────

def _dialog(message: str, title: str, buttons: list[str]) -> str:
    """Show a native macOS dialog. Returns the button label clicked."""
    buttons_applescript = ', '.join(f'"{b}"' for b in buttons)
    script = (
        f'display dialog "{message}" '
        f'with title "{title}" '
        f'buttons {{{buttons_applescript}}} '
        f'default button "{buttons[-1]}"'
    )
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip().replace('button returned:', '')
    return buttons[-1]


# ── Update checker ────────────────────────────────────────────────────────────

def _check_for_update() -> tuple[bool, str]:
    """Returns (update_available, dmg_download_url)."""
    if COMMIT_SHA == 'dev':
        return False, ''
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{REPO}/releases/latest',
            headers={'User-Agent': 'SEO-Event-Tracker-Updater'}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            release = json.loads(resp.read())

        # Tag format: v2026-08-16-abc1234-mac — if our 7-char SHA is in it, we're current
        if COMMIT_SHA[:7] in release.get('tag_name', ''):
            return False, ''

        for asset in release.get('assets', []):
            if asset['name'].endswith('.dmg'):
                return True, asset['browser_download_url']

        return True, ''  # Update available but no DMG asset found yet
    except Exception:
        return False, ''


def _download_dmg(url: str) -> str | None:
    """Download DMG to ~/Downloads. Returns local path or None on failure."""
    dest = os.path.join(os.path.expanduser('~'), 'Downloads', 'SEO-Event-Tracker-update.dmg')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'SEO-Event-Tracker-Updater'})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(dest, 'wb') as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        return dest
    except Exception:
        return None


def _handle_update():
    update_available, dmg_url = _check_for_update()
    if not update_available:
        return

    clicked = _dialog(
        'A new version of SEO Event Tracker is available!\\n\\nDownload and install now?',
        'Update Available',
        ['Later', 'Download Now'],
    )
    if clicked != 'Download Now':
        return

    if not dmg_url:
        # No DMG asset yet (build still running) — fall back to releases page
        webbrowser.open(RELEASES_URL)
        return

    subprocess.run([
        'osascript', '-e',
        'display notification "Downloading update, please wait…" with title "SEO Event Tracker"'
    ])

    dmg_path = _download_dmg(dmg_url)

    if not dmg_path:
        _dialog(
            'Download failed. Opening releases page instead.',
            'Update Failed',
            ['OK'],
        )
        webbrowser.open(RELEASES_URL)
        return

    subprocess.run(['open', dmg_path])
    _dialog(
        'Update downloaded!\\n\\nDrag SEO Event Tracker to Applications to complete the update, then relaunch the app.',
        'Ready to Install',
        ['OK'],
    )


# ── Flask runner ──────────────────────────────────────────────────────────────

def _run_flask():
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with app.app_context():
        db.create_all()

    threading.Thread(target=_handle_update, daemon=True).start()

    chromium_exe = _find_chromium()

    if chromium_exe:
        # Bundled Chromium found — native app window
        threading.Thread(target=_run_flask, daemon=True).start()
        time.sleep(1.0)
        proc = _launch_app_window(chromium_exe)
        proc.wait()
    else:
        # Dev / fallback — system browser
        threading.Timer(1.5, lambda: webbrowser.open(URL)).start()
        _run_flask()


if __name__ == '__main__':
    main()
