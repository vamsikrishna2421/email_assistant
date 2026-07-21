# PyInstaller spec for the Jerry .app bundle.
#
# hiddenimports carry the modules PyInstaller cannot see: uvicorn picks its
# event loop and protocol implementations by string at runtime, and pystray
# chooses a backend the same way. Without these the app builds cleanly and then
# fails on launch, which is the worst possible time to find out.

block_cipher = None

a = Analysis(
    ['launch.py'],
    pathex=['../src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
        'pystray._darwin',
        'jerry.server', 'jerry.app', 'jerry.mail', 'jerry.learning',
        'jerry.connectors.gmail', 'jerry.digest.runner',
    ],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pytest'],
    win_no_prefer_redirects=False, win_private_assemblies=False,
    cipher=block_cipher, noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='Jerry', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False, name='Jerry',
)
app = BUNDLE(
    coll,
    name='Jerry.app',
    icon=None,
    bundle_identifier='com.jerry.inbox',
    info_plist={
        # Menu-bar only: no Dock icon, no window on launch.
        'LSUIElement': True,
        'CFBundleName': 'Jerry',
        'CFBundleDisplayName': 'Jerry',
        'CFBundleShortVersionString': '0.1.0',
        'NSHighResolutionCapable': True,
    },
)
