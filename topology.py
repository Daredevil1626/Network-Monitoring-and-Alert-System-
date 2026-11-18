#!/usr/bin/env python3
"""Mininet topology: 1 switch, 4 hosts, 10 Mbps links, plus a root-namespace
interface (10.0.0.254) so host agents can reach the controller's UDP server."""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.util import connectToRootNS

COLLECTOR_IP = "10.0.0.254"
N_HOSTS = 4

def run():
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    for i in range(1, N_HOSTS + 1):
        h = net.addHost(f"h{i}", ip=f"10.0.0.{i}/24")
        net.addLink(h, s1, bw=10)              # 10 Mbps = known capacity
    connectToRootNS(net, s1, COLLECTOR_IP + "/24", ["10.0.0.0/24"])
    net.start()
    # start a telemetry agent on every host
    for h in net.hosts:
        h.cmd(f"python3 agent.py --id {h.name} --iface {h.name}-eth0 "
              f"--server {COLLECTOR_IP} > /tmp/{h.name}_agent.log 2>&1 &")
    print("Agents started. Try: h1 iperf -s -u & ; h2 iperf -u -c 10.0.0.1 -b 9M -t 20")
    CLI(net)
    net.hosts[0].cmd("pkill -f agent.py")
    net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    run()
