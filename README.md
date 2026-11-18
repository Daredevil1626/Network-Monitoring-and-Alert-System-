# Network Monitoring and Alert System (First Evaluation)

Stack: Mininet + Ryu (OpenFlow 1.3), Python 3.

## Run
Terminal 1 (controller + monitor):   `ryu-manager monitor_app.py`
Terminal 2 (network + agents):       `sudo python3 topology.py`

## Demo (in the Mininet CLI)
1. Normal: wait ~10 s, status table shows all hosts, low utilization.
2. Utilization alert: `h1 iperf -s -u &` then `h2 iperf -u -c 10.0.0.1 -b 9.8M -t 20`
   -> HIGH_UTILIZATION alert, "likely source: h2" (switch stats + agent telemetry).
3. Silent host: `h3 pkill -f "agent.py --id h3"` -> HOST_SILENT after ~7 s.
4. Port drops: `h4 tc qdisc change dev h4-eth0 root netem loss 20%` + traffic.
Outputs: terminal table, `alerts.log`, `metrics.csv`.

## Requirement mapping
UDP agents -> agent.py | Central server -> UDP thread in monitor_app.py
SDN flow stats -> OFPPortStats/OFPFlowStats | Utilization -> delta bytes*8/(dt*capacity)
Thresholds -> CFG dict | Alerts -> _set_alert (dedup + clear) | Multi-node -> 4 hosts
Presentation -> _report() table + log files
