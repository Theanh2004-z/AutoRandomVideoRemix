# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('bin', 'bin')]
binaries = []
hiddenimports = [
    'core',
    'core.config',
    'core.models',
    'core.policies',
    'core.errors',
    'core.timebase',
    'core.media_catalog',
    'core.plan_manifest',
    'core.video_planner',
    'core.audio_planner',
    'core.export_planner',
    'core.app_paths',
    'core.history_repository',
    'core.history_migration',
    'core.history_manager',
    'core.process_runner',
    'core.job_controller',
    'core.output_validator',
    'core.output_commit',
    'core.job_recovery',
    'core.render_cache',
    'core.disk_estimator',
    'core.hardware_detector',
    'core.video_processor',
    'core.audio_processor',
    'core.segment_slicer',
    'core.video_scanner',
    'core.audio_scanner',
    'core.filename_generator',
    'core.pool_manager',
    'core.audio_pool_manager',
    'core.version',
    'core.update_manager',
    'core.update_service',
    'core.storage_cleanup',
    'ui.update_dialog',
    'sqlite3',
]

tmp_ret = collect_all('qfluentwidgets')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutoRandomVideoRemix',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AutoRandomVideoRemix',
)
