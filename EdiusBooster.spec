# Build on Windows: .venv\Scripts\pyinstaller EdiusBooster.spec
from pathlib import Path
root = Path(SPECPATH)
binaries = [(str(root / name), '.') for name in ('ffmpeg.exe', 'ffprobe.exe') if (root / name).exists()]
a = Analysis(['main.py'], pathex=[str(root)], binaries=binaries,
             datas=[(str(root / 'assets'), 'assets')],
             hiddenimports=['wmi', 'pythoncom', 'win32serviceutil', 'win32timezone'],
             hookspath=[], runtime_hooks=[], excludes=['tkinter', 'numpy', 'pandas', 'matplotlib'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EdiusBooster',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, uac_admin=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EdiusBooster')
