"""Bounded subprocess I/O. Never retain a video's packet listing or stderr in RAM."""
import os
import atexit
import weakref
import shutil
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path


PROCESSES = weakref.WeakSet()
PROCESS_LOCK = threading.Lock()


class ToolError(RuntimeError):
    pass


def tool(name):
    roots = [Path(sys.executable).parent, Path(__file__).resolve().parents[2]]
    if getattr(sys, '_MEIPASS', None):
        roots.insert(0, Path(sys._MEIPASS))
    for root in roots:
        for suffix in ('.exe', ''):
            candidate = root / (name + suffix)
            if candidate.is_file():
                return str(candidate)
    found = shutil.which(name)
    if not found:
        raise ToolError(f'{name} is missing. Put {name}.exe beside the app or install it on PATH.')
    return found


def spawn(command, priority=False):
    flags = 0
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW
        if priority:
            flags |= subprocess.HIGH_PRIORITY_CLASS
    process = subprocess.Popen([str(x) for x in command], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            text=True, encoding='utf-8', errors='replace',
                            bufsize=1, creationflags=flags)
    with PROCESS_LOCK:
        PROCESSES.add(process)
    return process


def terminate_tree(process):
    try:
        import psutil
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children + [parent]:
            try:
                child.resume()
                child.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(children + [parent], timeout=2)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
    except Exception:
        if process.poll() is None:
            process.kill()


def capture(command, timeout=30, limit=2_000_000):
    """Drain both pipes concurrently; cap captured text and kill hung probes."""
    process = spawn(command)
    chunks, errors = [], deque(maxlen=160)
    size = 0
    overflow = threading.Event()

    def read_out():
        nonlocal size
        while True:
            text = process.stdout.read(4096)
            if not text:
                break
            size += len(text)
            if size <= limit:
                chunks.append(text)
            else:
                overflow.set()

    def read_err():
        for line in iter(lambda: process.stderr.readline(4096), ''):
            errors.append(line[-4096:])

    threads = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=read_err, daemon=True)]
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_tree(process)
        raise ToolError(f'Tool timed out after {timeout}s') from exc
    finally:
        for thread in threads:
            thread.join(timeout=3)
        process.stdout.close()
        process.stderr.close()
    if overflow.is_set():
        raise ToolError('Probe output exceeded the bounded capture limit')
    if process.returncode:
        raise ToolError(''.join(errors)[-16000:] or 'Tool failed')
    return ''.join(chunks)


def terminate_all():
    with PROCESS_LOCK:
        processes = list(PROCESSES)
    for process in processes:
        if process.poll() is None:
            terminate_tree(process)


atexit.register(terminate_all)
