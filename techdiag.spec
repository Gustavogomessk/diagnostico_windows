# -*- mode: python ; coding: utf-8 -*-
# Gera dist/TechDiag.exe (arquivo unico, sem console). Use scripts\build.ps1.
import os

block_cipher = None
datas = [(os.path.join("src", "techdiag", "data"), os.path.join("techdiag", "data"))]
for d in ("tools", os.path.join("resources", "tools")):
    if os.path.isdir(d):
        datas.append((d, d))

a = Analysis(["run_techdiag.py"], pathex=["src"], datas=datas,
             hiddenimports=["PySide6.QtSvg"], excludes=["tkinter", "pytest"], cipher=block_cipher)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [], name="TechDiag", debug=False,
          strip=False, upx=False, console=False, uac_admin=False)
