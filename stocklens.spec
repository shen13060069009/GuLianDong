# -*- mode: python ; coding: utf-8 -*-
"""股联动 GuLianDong 打包配置（PyInstaller 6.x）

构建：
    python -m PyInstaller --clean --noconfirm stocklens.spec

产物：dist/GuLianDong/GuLianDong.exe（双击即用，onedir 形态）

两个关键决定：
  1. onedir 而不是 onefile。PySide6 + onnxruntime + 3 个 OCR 模型解压后
     数百 MB，onefile 每次启动都要解压到临时目录，启动要十几秒；onedir
     直接读文件，秒开。
  2. uac_admin=True。微信 / 行情软件很可能以管理员权限运行，
     不提权的话 SendInput 会被 UIPI 静默拦掉（表现为「点了没反应」），
     这种静默失败极难排查，宁可启动时点一次 UAC。
"""
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(SPECPATH)

# ---------------------------------------------------------------- 随包资源
datas = []

# rapidocr 的 3 个 onnx 模型 + 各级 config.yaml。
# 注意：字符字典是内嵌在 onnx 的 metadata 里的（session.get_character_list()），
# 没有独立的 keys.txt 要带 —— 少一个容易漏的文件。
datas += collect_data_files('rapidocr_onnxruntime')

# 股票主数据 / 别名 / 概念 / 弱词表，作为「首次运行自举源」。
# 运行时会由 src/paths.py 复制到 exe 同目录；之后一律以用户目录那份为准，
# 这样用户改了 weak_names.json 才会生效。
datas += [(os.path.join(ROOT, 'data'), 'data')]

# 托盘/任务栏图标资源：与 exe 图标同一枚 icon.ico（内含 16~256 七种尺寸）。
# EXE(icon=) 只是写进 PE 资源，运行时拿不到文件；托盘 QIcon 必须读实际文件，
# 所以这里随包再带一份，运行时从 paths.bundle_dir() 取。
datas += [(os.path.join(ROOT, 'build', 'icon.ico'), 'build')]

# ---------------------------------------------------------------- 隐式导入
hiddenimports = [
    'src', 'src.paths', 'src.capture', 'src.ocr', 'src.matcher',
    'src.overlay', 'src.linkage', 'src.stockdb',
    'ahocorasick',
    'win32gui', 'win32ui', 'win32con', 'win32api',
]
hiddenimports += collect_submodules('rapidocr_onnxruntime')

# ---------------------------------------------------------------- 排除
excludes = [
    'tkinter', 'matplotlib', 'pandas', 'scipy', 'IPython', 'jupyter',
    'notebook', 'PyQt5', 'PyQt6', 'PySide2', 'pytest', 'setuptools',
    'PIL.ImageQt',           # 我们只用 PIL 读写数组，不接 Qt
    # jieba 只在离线探针 probe/p23（弱词全表扫描）里用，运行时不碰。
    # 它的 lac_small 词向量模型就 ~10MB，打进去纯属浪费。
    'jieba',
]

a = Analysis(
    ['main.py'],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 裁掉 opencv 的视频编解码（opencv_videoio_ffmpeg500_64.dll，29MB）。
# 我们只做「静态图 → OCR」，不碰视频；rapidocr 用到 cv2 的部分是
# resize / warpPerspective 这类图像处理，跟 videoio 无关。
# PyInstaller 的 cv2 hook 会整套收进来，这里手动过滤。
a.binaries = [b for b in a.binaries if 'opencv_videoio_ffmpeg' not in b[0].lower()]

# ---------------------------------------------------------------- 裁剪 Qt
# 2026-10-07 瘦身：去掉自动识别后用户问「能不能小于 92.8M」。
# 框选/划词仍要 OCR（cv2/onnxruntime/numpy 是刚性成本，动不了），
# 能砍的是 PySide6 里纯 2D 桌面应用用不到的组件（du 实测尺寸）：
_DROP_BIN = (
    'opengl32sw.dll',          # 20.2MB 软件 OpenGL 光栅化器，纯 raster 用不到
    'qt6quick', 'qt6qml',      # 14.0MB QML/Quick 全家桶（含 QmlModels），没用 QML
    'qt6pdf',                  # 4.5MB PDF 模块
    'qt6network', 'qtnetwork', # 2.7MB 网络模块（dll + pyd + 依赖它的 tls 插件）
    'qt6opengl',               # 1.9MB OpenGL 模块（QtWidgets raster 路径不需要）
    'qdirect2d',               # 1.0MB 备选平台插件，qwindows 足够
    'plugins/tls', 'plugins/networkinformation',   # 依赖 Qt6Network，一并裁
)
a.binaries = [b for b in a.binaries
              if not any(k in b[0].lower() for k in _DROP_BIN)]
# 翻译文件 7MB，只留 qtbase 的中文（界面语言是中文，其余 40 国语言纯占地方）
a.datas = [d for d in a.datas
           if '/translations/' not in d[0].replace('\\', '/').lower()
           or 'zh_cn' in d[0].lower()]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GuLianDong',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # UPX 压缩会让 onnxruntime 的 DLL 加载失败，禁用
    console=False,             # GUI 应用；日志由 main._setup_frozen_logging 落盘
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, 'build', 'icon.ico'),
    uac_admin=True,            # 见文件头说明：避免 UIPI 静默拦截 SendInput
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='GuLianDong',
)
