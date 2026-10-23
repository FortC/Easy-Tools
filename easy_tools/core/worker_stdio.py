"""UTF-8 worker pipes, including windowed frozen builds with sys.std*=None."""
import ctypes
from ctypes import wintypes
import json
import os
import sys


def _windows_pipe(writing=False):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = (wintypes.DWORD,)
    kernel.GetStdHandle.restype = wintypes.HANDLE
    function = kernel.WriteFile if writing else kernel.ReadFile
    function.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
    function.restype = wintypes.BOOL
    return function, kernel.GetStdHandle(wintypes.DWORD(-11 if writing else -10))


def read_payload(limit=8_000_000):
    if sys.stdin is not None:
        data = sys.stdin.buffer.read(limit + 1) if hasattr(sys.stdin, 'buffer') else sys.stdin.read(limit + 1)
    elif os.name == 'nt':
        read, handle = _windows_pipe()
        chunks, size = [], 0
        while size <= limit:
            buffer = ctypes.create_string_buffer(min(65536, limit + 1 - size))
            count = wintypes.DWORD()
            if not read(handle, buffer, len(buffer), ctypes.byref(count), None):
                if ctypes.get_last_error() in (38, 109):
                    break
                raise OSError('无法读取后台任务输入')
            if not count.value:
                break
            chunks.append(buffer.raw[:count.value])
            size += count.value
        data = b''.join(chunks)
    else:
        with os.fdopen(0, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('输入超过传输预算')
    return json.loads(data.decode('utf-8') if isinstance(data, bytes) else data)


def write_payload(value):
    data = (json.dumps(value, ensure_ascii=False) + '\n').encode('utf-8')
    stream = sys.__stdout__ or sys.stdout
    if stream is not None:
        if hasattr(stream, 'buffer'):
            stream.buffer.write(data)
        else:
            stream.write(data.decode('utf-8'))
        stream.flush()
    elif os.name == 'nt':
        write, handle = _windows_pipe(True)
        while data:
            part = data[:65536]
            buffer, count = ctypes.create_string_buffer(part), wintypes.DWORD()
            if not write(handle, buffer, len(part), ctypes.byref(count), None) or not count.value:
                raise OSError('无法返回后台任务结果')
            data = data[count.value:]
    else:
        while data:
            data = data[os.write(1, data):]
