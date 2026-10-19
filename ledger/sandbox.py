"""Process containment for skill subprocesses and MCP servers.

Phase 1 of the OS sandbox (HARNESS_STUDY.md item 6): every command a skill
runs, and everything it starts, lives and dies together. On Windows the
child is created suspended, assigned to a Job Object with
KILL_ON_JOB_CLOSE, then resumed, so not even its first grandchild escapes
(gemini-cli's GeminiSandbox.cs and codex's job.rs do the same). On POSIX the
child leads its own session and the group is killed.

When the command itself exits, whatever it left running is killed. That is
the point: a test suite that starts a server, or a `claude` call that
outlives its .CMD shim, no longer survives the step that started it.

This contains processes. It does not yet restrict what they can read or
write -- that is the restricted-token / Low-integrity phase, still open.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

_WINDOWS = sys.platform == "win32"

if _WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC),
                    ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                             wintypes.LPVOID, wintypes.DWORD)
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9
    _CREATE_SUSPENDED = 0x4


class Job:
    """A process tree that is killed as a unit by `close()`."""

    def __init__(self, proc: subprocess.Popen, handle=None):
        self.proc = proc
        self._handle = handle

    def close(self) -> None:
        if _WINDOWS:
            if self._handle:
                _k32.TerminateJobObject(self._handle, 1)
                _k32.CloseHandle(self._handle)
                self._handle = None
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _new_job():
    handle = _k32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = _EXTENDED()
    info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(handle, _EXTENDED_LIMIT_INFORMATION,
                                        ctypes.byref(info), ctypes.sizeof(info)):
        err = ctypes.get_last_error()
        _k32.CloseHandle(handle)
        raise OSError(err, "SetInformationJobObject failed")
    return handle


def popen(cmd, **kwargs) -> Job:
    """Start `cmd` inside a fresh job. Fails closed: a process that cannot
    be contained is not started. Provider API keys are stripped from its
    environment: whatever it runs came from the repository."""
    from .credentials import child_env
    kwargs["env"] = child_env(kwargs.get("env"))
    if not _WINDOWS:
        return Job(subprocess.Popen(cmd, start_new_session=True, **kwargs))
    handle = _new_job()
    flags = kwargs.pop("creationflags", 0) | _CREATE_SUSPENDED
    try:
        proc = subprocess.Popen(cmd, creationflags=flags, **kwargs)
    except BaseException:
        _k32.CloseHandle(handle)
        raise
    if not _k32.AssignProcessToJobObject(handle, int(proc._handle)):
        err = ctypes.get_last_error()
        proc.kill()
        _k32.CloseHandle(handle)
        raise OSError(err, "AssignProcessToJobObject failed")
    _ntdll.NtResumeProcess(int(proc._handle))
    return Job(proc, handle)


def run(cmd, *, cwd=None, env=None, timeout: float | None = None,
        **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run(capture_output=True)` with the tree contained.

    Output is drained on a thread while the main process is awaited, so a
    grandchild holding the pipes open cannot hang the step: once the command
    exits, the job is closed, the stragglers die and the pipes reach EOF.
    Raises subprocess.TimeoutExpired like subprocess.run.
    """
    job = popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, **kwargs)
    proc = job.proc
    result: dict = {}
    reader = threading.Thread(
        target=lambda: result.update(zip(("out", "err"), proc.communicate())),
        daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        job.close()
    reader.join(timeout=30)
    out, err = result.get("out"), result.get("err")
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(cmd, proc.wait(), out, err)
