#!/usr/bin/env python3
"""Kill all uvicorn processes and clean up."""
import os
import signal
import time

killed = []
for pid_str in os.listdir("/proc"):
    if not pid_str.isdigit():
        continue
    pid = int(pid_str)
    if pid == os.getpid():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "r") as f:
            cmdline = f.read().replace("\x00", " ")
        if "uvicorn" in cmdline and "app.main" in cmdline:
            print(f"Killing PID {pid}: {cmdline[:80]}")
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass

print(f"\nKilled {len(killed)} process(es): {killed}")
time.sleep(2)

# Verify no uvicorn processes remain
remaining = []
for pid_str in os.listdir("/proc"):
    if not pid_str.isdigit():
        continue
    pid = int(pid_str)
    if pid == os.getpid():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "r") as f:
            cmdline = f.read().replace("\x00", " ")
        if "uvicorn" in cmdline and "app.main" in cmdline:
            remaining.append((pid, cmdline[:80]))
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass

if remaining:
    print(f"WARNING: {len(remaining)} process(es) still running:")
    for pid, cmd in remaining:
        print(f"  PID {pid}: {cmd}")
else:
    print("All uvicorn processes killed successfully.")
