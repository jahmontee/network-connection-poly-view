#!/usr/bin/python3
"""GPL-2.0-or-later. Unprivileged stdin-to-local-socket bridge; no password argv."""
import json
import socket
import struct
import sys


def call(raw, path='/run/hector-wifi-manager/control.sock', timeout=12, expected_uid=0):
    if len(raw) > 4096 or not raw.endswith(b'\n') or raw.count(b'\n') != 1:
        return dict(ok=False, error='invalid-request')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(path)
            peer_uid = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            if peer_uid != expected_uid:
                raise ValueError('Unexpected local service owner')
            connection.sendall(raw)
            result = bytearray()
            while b'\n' not in result and len(result) <= 131072:
                chunk = connection.recv(8192)
                if not chunk:
                    break
                result.extend(chunk)
            if len(result) > 131072 or not result.endswith(b'\n'):
                raise ValueError()
            return json.loads(result)
    except Exception:
        return dict(ok=False, error='service-unavailable')


if __name__ == '__main__':
    print(json.dumps(call(sys.stdin.buffer.readline(4098)), ensure_ascii=True))
