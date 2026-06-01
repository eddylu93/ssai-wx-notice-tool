# -*- mode: python ; coding: utf-8 -*-

import re
from pathlib import Path


APP_NAME = 'SSAI-WX 通知小工具'
APP_IDENTIFIER = 'local.ssai.wx.notice.helper'
APP_VERSION = re.search(
    r'^APP_VERSION\s*=\s*"V?([^"]+)"',
    Path('app.py').read_text(encoding='utf-8'),
    re.MULTILINE,
).group(1)

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['docx'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SSAI-WX 通知小工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app_icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
app = BUNDLE(
    coll,
    name=f'{APP_NAME}.app',
    icon='assets/app_icon.icns',
    bundle_identifier=APP_IDENTIFIER,
    info_plist={
        'CFBundleDisplayName': APP_NAME,
        'CFBundleName': APP_NAME,
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
    },
)
