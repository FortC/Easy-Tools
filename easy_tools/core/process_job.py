"""Own worker process trees on Windows; closing the handle kills descendants.

Never assign user-opened applications or the independent update helper here.
This is lifetime management, not a permissions sandbox.
"""
import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes


class ProcessJob:
    def __init__(self, process, memory_limit=None):
        self.handle = None
        self.process = process
        if os.name != 'nt':
            return
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)

        class BASIC(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in (
                'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class EXTENDED(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BASIC), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        handle = kernel.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if memory_limit:
            info.BasicLimitInformation.LimitFlags |= 0x200  # JOB_MEMORY
            info.JobMemoryLimit = memory_limit
        if (not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
                or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle)))):
            error = ctypes.get_last_error()
            if handle:
                kernel.CloseHandle(handle)
            process.kill()
            process.wait(timeout=2)
            raise OSError(error, '无法建立后台进程清理约束，已停止启动')
        self.handle = handle

    def close(self):
        handle, self.handle = self.handle, None
        if handle:
            self._kernel.CloseHandle(handle)
        elif os.name != 'nt' and self.process.poll() is None:
            self.process.kill()

    def __del__(self):
        self.close()


def spawn_worker(command, **kwargs):
    """Assign the process before its first instruction, then resume its thread."""
    memory_limit = kwargs.pop('memory_limit', None)
    if os.name == 'nt':
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | 0x4  # CREATE_SUSPENDED
    process = subprocess.Popen(command, **kwargs)
    job = ProcessJob(process, memory_limit=memory_limit)
    if os.name != 'nt':
        return process, job
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
                    ('th32ThreadID', wintypes.DWORD), ('th32OwnerProcessID', wintypes.DWORD),
                    ('tpBasePri', wintypes.LONG), ('tpDeltaPri', wintypes.LONG), ('dwFlags', wintypes.DWORD)]

    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Thread32First', 'Thread32Next'):
        getattr(kernel, name).argtypes = (wintypes.HANDLE, ctypes.POINTER(THREADENTRY32))
        getattr(kernel, name).restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)
    resumed = False
    try:
        if snapshot not in (None, ctypes.c_void_p(-1).value):
            entry = THREADENTRY32()
            entry.dwSize = ctypes.sizeof(entry)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    thread = kernel.OpenThread(0x2, False, entry.th32ThreadID)
                    if thread:
                        try:
                            resumed = kernel.ResumeThread(thread) != 0xffffffff
                        finally:
                            kernel.CloseHandle(thread)
                    break
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        if snapshot not in (None, ctypes.c_void_p(-1).value):
            kernel.CloseHandle(snapshot)
        if not resumed:
            job.close()
            process.wait(timeout=2)
    if not resumed:
        raise OSError('无法恢复受控后台进程，已安全停止启动')
    return process, job


def communicate_bounded(process, payload, job, cancel, timeout, output_limit=8_000_000):
    """Drain both pipes with bounded buffers, including scripts bypassing print()."""
    buffers = [[], []]
    overflow = threading.Event()

    def reader(index, stream, limit):
        total = 0
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    return
                total += len(chunk)
                if total > limit:
                    overflow.set()
                    job.close()
                    return
                buffers[index].append(chunk)
        finally:
            stream.close()

    readers = [threading.Thread(target=reader, args=(i, stream, limit), daemon=True,
                                name='script-pipe-reader')
               for i, (stream, limit) in enumerate(((process.stdout, output_limit), (process.stderr, 64000)))]
    for thread in readers:
        thread.start()
    deadline = time.monotonic() + timeout
    try:
        try:
            process.stdin.write(payload)
        except BrokenPipeError:
            pass
        finally:
            process.stdin.close()
        while process.poll() is None:
            if cancel.is_set() or overflow.is_set():
                job.close()
                break
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            cancel.wait(0.02)
    finally:
        # Terminate descendants even if the script itself has exited but left pipes open.
        job.close()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        for thread in readers:
            thread.join(timeout=1)
    if overflow.is_set():
        raise ValueError('脚本输出超过预算，请缩小输出范围')
    return b''.join(buffers[0]), b''.join(buffers[1])
