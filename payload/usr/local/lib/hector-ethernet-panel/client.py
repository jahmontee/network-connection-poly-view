"""One bounded local request. No TCP listener or shell commands."""
import json
import socket
import sys


def main():
    try:
        request = sys.stdin.buffer.readline(1025)
        if len(request) > 1024:
            raise ValueError('oversize')
        json.loads(request)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(2)
            peer.connect('/run/hector-ethernet-panel/control.sock')
            peer.sendall(request.rstrip(b'\n') + b'\n')
            raw = b''
            while b'\n' not in raw and len(raw) < 32768:
                part = peer.recv(4096)
                if not part:
                    break
                raw += part
            reply = json.loads(raw)
    except Exception:
        reply = dict(ok=False, error='service-unavailable')
    print(json.dumps(reply))


if __name__ == '__main__':
    main()
