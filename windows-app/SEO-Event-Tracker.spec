# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for SEO Event Tracker — Windows .exe
#
# Run from the PROJECT ROOT:
#   pyinstaller windows-app/SEO-Event-Tracker.spec --clean --noconfirm
#
# Output: dist/SEO-Event-Tracker.exe

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# SPECPATH = directory containing this .spec file (windows-app/)
PROJECT_ROOT = str(Path(SPECPATH).parent)

# Collect the entire playwright package — this pulls in the bundled
# Node.js runtime and driver that power "playwright install chromium"
# from within the frozen .exe.
pw_datas, pw_binaries, pw_hiddenimports = collect_all('playwright')

a = Analysis(
    [os.path.join(SPECPATH, 'launcher.py')],
    pathex=[PROJECT_ROOT, SPECPATH],   # SPECPATH = windows-app/ so version.py is importable
    binaries=pw_binaries,
    datas=[
        # App assets
        (os.path.join(PROJECT_ROOT, 'templates'), 'templates'),
        (os.path.join(PROJECT_ROOT, 'static'),    'static'),
        (os.path.join(PROJECT_ROOT, 'src'),       'src'),
        # Build-time version file (written by GitHub Actions)
        (os.path.join(SPECPATH, 'version.py'),   '.'),
    ] + pw_datas,  # + playwright driver, node.exe, etc.
    hiddenimports=pw_hiddenimports + [
        # Flask ecosystem
        'flask_sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.pool',
        'sqlalchemy.event',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        # Data processing
        'pandas',
        'openpyxl',
        'openpyxl.workbook',
        'openpyxl.reader.excel',
        'openpyxl.styles',
        # Image processing
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        # Async
        'asyncio',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
        'IPython',
        'notebook',
        'pytest',
        'setuptools',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='SEO-Event-Tracker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # No black terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(SPECPATH, 'app.ico'),
)
