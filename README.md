# Network Monitoring and Alert System

Hosts send telemetry over UDP sockets, and a Ryu SDN controller combines it with OpenFlow flow and port statistics to detect abnormal network conditions and raise alerts. Built with Python, Mininet, Open vSwitch and Ryu (OpenFlow 1.3).

## Architecture

```
  h1   h2   h3   h4          Mininet hosts, each runs agent.py
   \    |    |    /
    +---+----+---+   s1      Open vSwitch, OpenFlow 1.3, 10 Mbps links
        |      \
        |       +---- root link 10.0.0.254  (UDP collector address)
        |
   OpenFlow channel (127.0.0.1:6653)
        |
   Ryu controller running monitor_app.py
      learning switch | UDP server :9999 | stats poller (2 s)
      utilization | thresholds | correlation | alerts | live report
```

| Component | File | Role |
|---|---|---|
| Telemetry agent | `agent.py` | Runs on each host and sends rx/tx byte counters over UDP every 2 seconds |
| Topology | `topology.py` | Builds 1 switch, 4 hosts, 10 Mbps links and the root link, and starts the agents |
| Controller and monitor | `monitor_app.py` | Ryu app with a learning switch, UDP server, OpenFlow stats polling, alerts and a live report |

Two paths feed the monitor:

- **Telemetry path:** host agent, then the switch, then the root link to the UDP server on `10.0.0.254:9999`
- **Control path:** Ryu to the switch over OpenFlow on `127.0.0.1:6653`

## How to run

### Requirements

- Linux (Ubuntu 20.04 or 22.04). On Windows, use WSL2 or a VM, because Mininet needs Linux.
- Mininet, Open vSwitch and `iperf`
- Python 3.9 for Ryu (newer versions fail to build it), with `eventlet==0.30.2`

### Install

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf curl
sudo service openvswitch-switch start

curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
uv venv --python 3.9 ~/ryu-env
source ~/ryu-env/bin/activate
uv pip install "setuptools<58" wheel
uv pip install --no-build-isolation "ryu==4.34" "eventlet==0.30.2" "oslo.config>=2.5.0"
ryu-manager --version
```

Clone the repository and run everything from inside its folder:

```bash
git clone <your-repository-url>
cd <repository-folder>
```

### Start (three terminal windows)

**Window 1: controller and monitor (start this first)**
```bash
source ~/ryu-env/bin/activate
ryu-manager --ofp-tcp-listen-port 6653 monitor_app.py
```
Wait for `UDP telemetry server on :9999`.

**Window 2: network**
```bash
sudo mn -c
sudo python3 topology.py
```
This opens the `mininet>` prompt. After about 10 seconds, the table in Window 1 lists h1 to h4.

**Window 3: live alerts**
```bash
tail -f alerts.log
```

### Try it

Type these at the `mininet>` prompt, one command per line.

```
pingall
```
Expected: `0% dropped`, all four hosts in the table, no alerts.

```
h1 iperf -s -u &
h2 iperf -u -c 10.0.0.1 -b 9.8M -t 25
```
Expected: a CRITICAL `HIGH_UTILIZATION` alert with `likely source: h2`, then `[CLEARED]` after the test ends.

```
h3 pkill -f "agent.py --id h3"
```
Expected: `HOST_SILENT @ h3` after about 7 to 9 seconds. Bring it back with the host name in quotes:
```
h3 python3 agent.py --id "h3" --iface h3-eth0 --server 10.0.0.254 > /tmp/h3_agent.log 2>&1 &
```
Expected: `[CLEARED] HOST_SILENT @ h3`.

Results are saved in `alerts.log` and `metrics.csv`.

### Stop

Type `exit` in Window 2, press Ctrl+C in Windows 1 and 3, then run `sudo mn -c`.
