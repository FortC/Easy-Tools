"""Stage a matching MSVC runtime next to llama, never change Windows system DLLs."""
from pathlib import Path
import importlib.util
import shutil
import ctypes
import os

_loaded_libraries = []
_search_paths = []

MSVC_NAMES = ('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
              'vcruntime140.dll', 'vcruntime140_1.dll', 'concrt140.dll', 'msvcp140_codecvt_ids.dll')


def preload_msvc():
    """Keep native imports on the application's matching runtime on old Windows."""
    if os.name != 'nt' or _loaded_libraries:
        return
    files = msvc_runtime_files()
    root = files[0].parent
    _search_paths.append(os.add_dll_directory(str(root)))
    ordered = sorted(files, key=lambda p: 0 if p.name.startswith('vcruntime') else 1)
    for path in ordered:
        _loaded_libraries.append(ctypes.WinDLL(str(path)))


def msvc_runtime_files():
    spec = importlib.util.find_spec('PySide6')
    if not spec or not spec.origin:
        raise RuntimeError('无法定位应用自带的 Windows C++ 运行库')
    root = Path(spec.origin).parent
    files = [root / name for name in MSVC_NAMES if (root / name).is_file()]
    if not any(path.name == 'msvcp140.dll' for path in files):
        raise RuntimeError('应用缺少匹配的 Windows C++ 运行库')
    return files


def prepare_model_runtime(executable):
    executable = Path(executable)
    if (executable.parent / 'msvcp140.dll').is_file():
        return str(executable)
    # Packaged builds contain these beside llama. Source runs prepare an isolated
    # cache in the app's own writable directory, off the UI thread.
    from .paths import data_dir
    root = data_dir().resolve()
    target = root / 'runtime' / 'llama'
    if root not in target.resolve().parents:
        raise RuntimeError('后台运行库缓存目录越界')
    target.mkdir(parents=True, exist_ok=True)
    for source in [p for p in executable.parent.iterdir() if p.suffix.lower() == '.dll' or p == executable] + msvc_runtime_files():
        destination = target / source.name
        if destination.is_symlink():
            raise RuntimeError('后台运行库缓存文件无效')
        if (not destination.exists() or destination.stat().st_size != source.stat().st_size
                or destination.stat().st_mtime_ns != source.stat().st_mtime_ns):
            shutil.copy2(source, destination)
    return str(target / executable.name)
