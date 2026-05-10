#!/usr/bin/env python3
"""Small TCP forwarder for the Hermes WebUI host wrapper.

Listens on the WSL/host side and forwards connections to the WebUI running
inside a Hermes Docker container.  Intentionally stdlib-only so the wrapper
works on a plain WSL install without socat/ncat.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path

stop_event = threading.Event()
listener: socket.socket | None = None


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] {message}", flush=True)


def relay(src: socket.socket, dst: socket.socket) -> None:
    try:
        while not stop_event.is_set():
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (dst, src):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle_client(client: socket.socket, target_host: str, target_port: int) -> None:
    try:
        upstream = socket.create_connection((target_host, target_port), timeout=10)
    except OSError as exc:
        log(f"connect to {target_host}:{target_port} failed: {exc}")
        try:
            client.close()
        except OSError:
            pass
        return

    threads = [
        threading.Thread(target=relay, args=(client, upstream), daemon=True),
        threading.Thread(target=relay, args=(upstream, client), daemon=True),
    ]
    for thread in threads:
        thread.start()


def shutdown(signum: int, _frame) -> None:  # type: ignore[no-untyped-def]
    stop_event.set()
    global listener
    if listener is not None:
        try:
            listener.close()
        except OSError:
            pass
    log(f"received signal {signum}; shutting down")


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward a local port to a Docker container WebUI port")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--bind-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--pid-file")
    parser.add_argument("--state-file")
    args = parser.parse_args()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)

    global listener
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind_host, args.bind_port))
    listener.listen(128)

    state = {
        "pid": os.getpid(),
        "bind_host": args.bind_host,
        "bind_port": args.bind_port,
        "target_host": args.target_host,
        "target_port": args.target_port,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if args.pid_file:
        Path(args.pid_file).write_text(f"{os.getpid()}\n", encoding="utf-8")
    if args.state_file:
        Path(args.state_file).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    log(
        "forwarding "
        f"{args.bind_host}:{args.bind_port} -> {args.target_host}:{args.target_port} "
        f"(pid {os.getpid()})"
    )

    while not stop_event.is_set():
        try:
            client, _addr = listener.accept()
        except OSError:
            if stop_event.is_set():
                break
            raise
        threading.Thread(
            target=handle_client,
            args=(client, args.target_host, args.target_port),
            daemon=True,
        ).start()

    if args.pid_file:
        try:
            Path(args.pid_file).unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        log(f"fatal: {exc}")
        raise
