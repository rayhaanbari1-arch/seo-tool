# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for SEO Event Tracker — macOS .app bundle
#
# Run from the PROJECT ROOT:
#   pyinstaller mac-app/SEO-Event-Tracker.spec --clean --noconfirm
#
# Output: dist/SEO-Event-Tracker.app

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

block_cipher = None

# SPECPATH = mac-app/,  PROJECT_ROOT = one level up
PROJECT_ROOT = str(Path(SPECPATH).parent)

pw_datas, pw_binaries, pw_hiddenimports = collect_all('playwright')

a = Analysis(
    [os.path.join(SPECPATH, 'launcher.py')],
    pathex=[PROJECT_ROOT, SPECPATH],
    binaries=pw_binaries,
    datas=[
        (os.path.join(PROJECT_ROOT, 'templates'), 'templates'),
        (os.path.join(PROJECT_ROOT, 'static'),    'static'),
        (os.path.join(PROJECT_ROOT, 'src'),       'src'),
        (os.path.join(SPECPATH, 'version.py'),    '.'),
    ] + pw_datas,
    hiddenimports=pw_hiddenimports + [
        'flask_sqlalchemy',
        'sqlalchemy.dialects.sqlite',
        'sqlalchemy.pool',
        'sqlalchemy.event',
        'jinja2',
        'jinja2.ext',
        'werkzeug',
        'werkzeug.serving',
        'werkzeug.routing',
        'pandas',
        'openpyxl',
        'openpyxl.workbook',
        'openpyxl.reader.excel',
        'openpyxl.styles',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'asyncio',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'IPython', 'pytest'],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SEO-Event-Tracker',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    windowed=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SEO-Event-Tracker',
)

app = BUNDLE(
    coll,
    name='SEO-Event-Tracker.app',
    icon=None,
    bundle_identifier='com.tvsemerald.seoeventtracker',
    info_plist={
        'NSHighResolutionCapable': True,
        'NSRequiresAquaSystemAppearance': False,
        'LSMinimumSystemVersion': '10.15.0',
        'CFBundleDisplayName': 'SEO Event Tracker',
        'CFBundleShortVersionString': '1.0',
    },
)
