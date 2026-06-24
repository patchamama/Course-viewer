# -*- mode: python ; coding: utf-8 -*-
import sys
import os

block_cipher = None

# Build datas list before Analysis — avoids TOC tuple-arity issues
_datas = [
    ('course-viewer.html', '.'),
    ('demo.html', '.'),
    ('course-viewer.config.json.example', '.'),
]
for _asset in ['assets/icon.png', 'assets/icon.ico', 'assets/icon.icns']:
    if os.path.isfile(_asset):
        _datas.append((_asset, 'assets'))

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=_datas,
    hiddenimports=['proxy', 'updater'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='course-viewer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico' if os.path.isfile('assets/icon.ico') else None,
    onefile=True,
)

# macOS: wrap binary in a .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Course Viewer.app',
        icon='assets/icon.icns' if os.path.isfile('assets/icon.icns') else None,
        bundle_identifier='com.patchamama.courseviewer',
        info_plist={
            'CFBundleVersion': '1.0.1',
            'CFBundleShortVersionString': '1.0.1',
            'NSHighResolutionCapable': True,
            'LSUIElement': True,
            'CFBundleDocumentTypes': [
                {
                    'CFBundleTypeRole': 'Viewer',
                    'LSItemContentTypes': ['public.folder'],
                    'CFBundleTypeName': 'Folder',
                }
            ],
        },
    )
