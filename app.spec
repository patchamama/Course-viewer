# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('course-viewer.html', '.'),
        ('demo.html', '.'),
        ('course-viewer.config.json.example', '.'),
    ],
    hiddenimports=['proxy', 'updater', 'pystray._util.gtk', 'pystray._util.win32', 'pystray._util.darwin'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Include assets only if they exist
for asset in ['assets/icon.png', 'assets/icon.ico']:
    if os.path.isfile(asset):
        a.datas += [(asset, os.path.dirname(asset))]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='course-viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if os.path.isfile('assets/icon.ico') else None,
    onefile=True,
)

# macOS: wrap exe in a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Course Viewer.app',
        icon='assets/icon.icns' if os.path.isfile('assets/icon.icns') else None,
        bundle_identifier='com.patchamama.courseviewer',
        info_plist={
            'CFBundleVersion': '1.0.0',
            'CFBundleShortVersionString': '1.0.0',
            'NSHighResolutionCapable': True,
            'LSUIElement': True,  # hide from Dock (tray-only app)
            'CFBundleDocumentTypes': [
                {
                    'CFBundleTypeRole': 'Viewer',
                    'LSItemContentTypes': ['public.folder'],
                    'CFBundleTypeName': 'Folder',
                }
            ],
        },
    )
