#!/usr/bin/env python3
"""Find ALL python processes and any process listening on port 8000."""
import os
import signal

print("=== All python3 processes ===")
for pid_str in os.listdir("/proc"):
    if not pid_str.isdigit():
        continue
    pid = int(pid_str)
    if pid == os.getpid():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "r") as f:
            cmdline = f.read().replace("\x00", " ").strip()
        if not cmdline:
            continue
        # Show python processes or anything mentioning 8000/uvicorn/fastapi
        if any(kw in cmdline.lower() for kw in ["python", "uvicorn", "fastapi", "8000"]):
            print(f"  PID {pid}: {cmdline[:120]}")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass

print("\n=== Check TCP connections on port 8000 ===")
# Check /proc/net/tcp for port 8000 (0x1F40 in hex)
try:
    with open("/proc/net/tcp", "r") as f:
        lines = f.readlines()
    for line in lines[1:]:  # skip header
        parts = line.split()
        local = parts[1]
        port_hex = local.split(":")[1]
        port = int(port_hex, 16)
        if port == 8000:
            state = parts[3]
            inode = parts[9]
            print(f"  Port 8000: state={state} inode={inode}")
            # Find the PID that owns this socket
            for pid_str in os.listdir("/proc"):
                if not pid_str.isdigit():
                    continue
                fd_dir = f"/proc/{pid_str}/fd"
                try:
                    for fd in os.listdir(fd_dir):
                        try:
                            link = os.readlink(f"{fd_dir}/{fd}")
                            if f"socket:[{inode}]" in link:
                                print(f"    -> PID {pid_str} owns this socket")
                        except (FileNotFoundError, ProcessLookupError, PermissionError):
                            pass
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    pass
except Exception as e:
    print(f"  Error reading /proc/net/tcp: {e}")
