# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（方案 §3 技术选型最后一条）。

为什么是 onedir 而不是 onefile：
onefile 每次启动都要把整个包解压到 %TEMP%，启动延迟 2-5 秒，
而本程序是常驻托盘 + 全局钩子的低延迟工具，首屏慢会直接被感知。
目录模式启动是即时的，代价只是分发时是个文件夹（可以压缩成 zip）。

hiddenimports 里显式列出 rapidocr_onnxruntime：
它是运行时懒加载的，PyInstaller 静态分析看不到，必须点名，
否则打包后 OCR 会静默失效。
"""

block_cipher = None

import os
import sys
_model_file = os.path.join("models", "SmolLM2-360M-Instruct-Q4_K_M.gguf")
_llama_dir = os.path.join("vendor", "llama")
_build_tag = os.environ.get("EASYTOOLS_BUILD_TAG", "")
_build_dir = "build-" + _build_tag if _build_tag else "build"
_python_ssl_binaries = [(os.path.join(sys.base_prefix, "DLLs", name), ".")
                        for name in ("libssl-3-x64.dll", "libcrypto-3-x64.dll")
                        if os.path.isfile(os.path.join(sys.base_prefix, "DLLs", name))]
_update_datas = [
    ("resources/downloadcenter-root.crt", "update"),
    (os.path.join(_build_dir, "EasyToolsUpdater.exe"), "update"),
]
_cloud_root = "resources/downloadcenter-cloud-root.crt"
if os.path.isfile(_cloud_root):
    _update_datas.append((_cloud_root, "update"))
_llama_datas = [(os.path.join(_llama_dir, name), "llama")
                for name in os.listdir(_llama_dir)
                if name == "llama-server.exe" or name.lower().endswith(".dll")
                or name.lower().startswith("license-")]
from easy_tools.core.runtime import msvc_runtime_files
_llama_datas += [(str(path), 'llama') for path in msvc_runtime_files()]
if not os.path.isfile(_model_file):
    raise SystemExit("缺少内置模型权重：" + _model_file)
if not os.path.isfile(os.path.join(_llama_dir, "llama-server.exe")):
    raise SystemExit("缺少 llama.cpp CPU 运行时：" + _llama_dir)

# OCR 依赖（rapidocr_onnxruntime → onnxruntime + opencv + 模型文件）是运行时
# 懒加载：静态分析只能看到 import 名字，模型与动态库必须整包收集，
# 否则打包后 OCR 会静默失效。
from PyInstaller.utils.hooks import collect_all
from importlib.util import find_spec
for _required in ('rapidocr_onnxruntime', 'onnxruntime', 'cv2', 'numpy'):
    if find_spec(_required) is None:
        raise SystemExit('完整安装包缺少 OCR 构建依赖：' + _required + '；请安装 requirements-build.txt 后重试。')
_ocr_datas, _ocr_binaries, _ocr_hidden = [], [], []
for _pkg in ("rapidocr_onnxruntime", "onnxruntime", "cv2", "numpy"):
    _d, _b, _h = collect_all(_pkg, include_py_files=False)
    _ocr_datas += _d
    _ocr_binaries += _b
    _ocr_hidden += _h
_ocr_datas = [entry for entry in _ocr_datas
              if not any(part in str(entry[0]).replace("\\", "/").lower().split("/")
                         for part in ("test", "tests", "testing", "benchmarks"))
              and not os.path.basename(str(entry[0])).lower().startswith("test_")
              and not os.path.basename(str(entry[0])).lower().endswith('_test.py')
              and os.path.basename(str(entry[0])).lower() != "conftest.py"]
_ocr_hidden = [name for name in _ocr_hidden
               if not name.startswith(('onnxruntime.quantization', 'onnxruntime.transformers',
                                       'onnxruntime.tools', 'onnxruntime.datasets',
                                       'numpy.f2py', 'numpy.distutils', 'numpy.array_api'))
               and not any(part in name.split(".")
                          for part in ("test", "tests", "testing", "benchmarks"))
               and not any(part.startswith("test_") or part.endswith("_test")
                           or part == "conftest"
                           for part in name.split("."))]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=_ocr_binaries + _python_ssl_binaries,
    datas=[
        ("easy_tools/config/defaults.json", "easy_tools/config"),
        ("easy_tools/ui/assets/check.png", "easy_tools/ui/assets"),
        ("easy_tools/ui/icons", "easy_tools/ui/icons"),
        ("app.ico", "."),
    ] + _update_datas + [
        (_model_file, "models"),
        ("models/MODEL-NOTICE.txt", "models"),
        ("models/LICENSE-Apache-2.0.txt", "models"),
    ] + _ocr_datas + _llama_datas,
    hiddenimports=[
        "rapidocr_onnxruntime",
        "onnxruntime",
        "numpy",
        "cv2",
        "openpyxl",
    ] + _ocr_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6.QtWebEngineCore", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# PATH 里可能包含其它应用携带的较新 MSVC runtime。PyInstaller 会把它们
# 解析到 _internal 根目录，优先于 PySide6 自带运行库加载，造成 QtCore
# 报“找不到指定的程序”。保留 PySide6/shiboken6 目录内随包的 runtime，
# 移除误解析到根目录的副本，避免污染 Qt 的 DLL 搜索顺序。
def _keep_binary(entry):
    destination = entry[0].replace("\\", "/").lower()
    source = str(entry[1]).replace("\\", "/").lower()
    # The Codex desktop runtime prepends its Poppler and MSVC folders to PATH.
    # PyInstaller otherwise collects unrelated ICU/OpenSSL/CRT DLLs from there,
    # which shadow or mismatch the libraries bundled with PySide6.
    if "/.cache/codex-runtimes/" in source:
        return False
    # Windows provides API-set shims and UCRT. Keep Qt's own redistributables
    # under PySide6/shiboken6, not unrelated copies at _internal's root.
    if destination.startswith("api-ms-win-"):
        return False
    if destination in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "ucrtbase.dll"):
        return False
    return True


a.binaries = [entry for entry in a.binaries if _keep_binary(entry)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Easy-Tools",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI 程序，不弹黑框
    icon="app.ico",          # 托盘图标与任务栏/资源管理器图标都来自这里
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Easy-Tools",
)
