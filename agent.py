#!/usr/bin/env python3
"""UDP telemetry agent: sends one JSON datagram per interval to the monitor."""
import argparse, json, socket, time

def read_bytes(iface):
    with open("/proc/net/dev") as f:
        for line in f:
            if line.strip().startswith(iface + ":"):
                p = line.split(":")[1].split()
                return int(p[0]), int(p[8])      # rx_bytes, tx_bytes
    return 0, 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--iface", required=True)
    ap.add_argument("--server", default="10.0.0.254")
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    while True:
        rx, tx = read_bytes(a.iface)
        msg = {"host": a.id, "seq": seq, "ts": time.time(),
               "rx_bytes": rx, "tx_bytes": tx, "interval": a.interval}
        sock.sendto(json.dumps(msg).encode(), (a.server, a.port))
        seq += 1
        time.sleep(a.interval)

if __name__ == "__main__":
    main()
