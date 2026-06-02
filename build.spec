# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — builds dist/sales-automation.exe."""

import os
from pathlib import Path

block_cipher = None
ROOT = os.path.abspath(".")

a = Analysis(
    [os.path.join(ROOT, "src", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "company_rules"), "company_rules"),
        (os.path.join(ROOT, "src", "llm", "prompts"), os.path.join("src", "llm", "prompts")),
        (os.path.join(ROOT, "src", "db", "migrations"), os.path.join("src", "db", "migrations")),
        (os.path.join(ROOT, "src", "api", "web", "templates"), os.path.join("src", "api", "web", "templates")),
        (os.path.join(ROOT, ".env.example"), "."),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "sqlalchemy.dialects.sqlite",
        "sqlalchemy.dialects.postgresql",
        "google.genai",
        "jinja2",
        "multipart",
    ],
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
    name="sales-automation",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
