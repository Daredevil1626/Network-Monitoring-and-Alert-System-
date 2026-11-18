"""Ryu app: learning switch + UDP telemetry server + OpenFlow stats polling
+ utilization + threshold alerts + correlation + terminal report.
Run:  ryu-manager monitor_app.py
"""
import csv, json, socket, time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.lib import hub

CFG = {
    "capacity_bps": 10_000_000,   # must match topology bw
    "poll_s": 2,
    "udp_port": 9999,
    "util_warn": 80.0,
    "util_crit": 95.0,
    "silent_after_s": 7,          # ~3x agent interval
    "host_tx_high_pct": 50.0,
}

class Monitor(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.mac_to_port = {}
        self.datapaths = {}
        self.port_prev = {}      # (dpid,port) -> (t, rx, tx, drops)
        self.port_util = {}      # (dpid,port) -> util %
        self.hosts = {}          # host -> dict(last_seen, seq, lost, tx_rate_bps, prev)
        self.active = {}         # (type,node) -> severity  (suppresses repeats)
        self.flows = {}          # dpid -> list of (pkts, bytes, match str)
        self.alertlog = open("alerts.log", "a")
        self.csv = open("metrics.csv", "a", newline="")
        self.csvw = csv.writer(self.csv)
        hub.spawn(self._udp_server)
        hub.spawn(self._poller)

    # ---------- learning switch ----------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath; p = dp.ofproto_parser; o = dp.ofproto
        self._add_flow(dp, 0, p.OFPMatch(), [p.OFPActionOutput(o.OFPP_CONTROLLER, o.OFPCML_NO_BUFFER)])

    def _add_flow(self, dp, prio, match, actions):
        p = dp.ofproto_parser; o = dp.ofproto
        inst = [p.OFPInstructionActions(o.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg; dp = msg.datapath; o = dp.ofproto; p = dp.ofproto_parser
        in_port = msg.match["in_port"]
        eth = packet.Packet(msg.data).get_protocols(ethernet.ethernet)[0]
        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        out = self.mac_to_port[dp.id].get(eth.dst, o.OFPP_FLOOD)
        actions = [p.OFPActionOutput(out)]
        if out != o.OFPP_FLOOD:
            self._add_flow(dp, 1, p.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src), actions)
        data = msg.data if msg.buffer_id == o.OFP_NO_BUFFER else None
        dp.send_msg(p.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                   in_port=in_port, actions=actions, data=data))

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER])
    def _state(self, ev):
        self.datapaths[ev.datapath.id] = ev.datapath

    # ---------- UDP telemetry server ----------
    def _udp_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(("0.0.0.0", CFG["udp_port"]))
        self.logger.info("UDP telemetry server on :%d", CFG["udp_port"])
        while True:
            data, _ = s.recvfrom(2048)
            try:
                m = json.loads(data)
            except ValueError:
                continue
            h = self.hosts.setdefault(m["host"], {"lost": 0, "seq": -1, "prev": None, "tx_bps": 0.0})
            if h["seq"] >= 0 and m["seq"] > h["seq"] + 1:
                h["lost"] += m["seq"] - h["seq"] - 1
            if h["prev"]:
                dt = m["ts"] - h["prev"]["ts"]
                if dt > 0:
                    h["tx_bps"] = (m["tx_bytes"] - h["prev"]["tx_bytes"]) * 8 / dt
            h["prev"], h["seq"], h["last_seen"] = m, m["seq"], time.time()

    # ---------- OpenFlow stats polling ----------
    def _poller(self):
        while True:
            hub.sleep(CFG["poll_s"])
            for dp in list(self.datapaths.values()):
                p = dp.ofproto_parser; o = dp.ofproto
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, o.OFPP_ANY))
                dp.send_msg(p.OFPFlowStatsRequest(dp))
            self._check_hosts()
            self._report()

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats(self, ev):
        dpid = ev.msg.datapath.id; now = time.time()
        for st in ev.msg.body:
            if st.port_no > 0xffffff00:      # skip LOCAL
                continue
            key = (dpid, st.port_no)
            drops = st.rx_dropped + st.tx_dropped + st.rx_errors + st.tx_errors
            prev = self.port_prev.get(key)
            if prev:
                dt = now - prev[0]
                if dt > 0:
                    rx_bps = (st.rx_bytes - prev[1]) * 8 / dt
                    tx_bps = (st.tx_bytes - prev[2]) * 8 / dt
                    util = max(rx_bps, tx_bps) / CFG["capacity_bps"] * 100
                    self.port_util[key] = util
                    self.csvw.writerow([round(now, 1), dpid, st.port_no, round(rx_bps), round(tx_bps), round(util, 1)])
                    self.csv.flush()
                    self._eval_port(key, util, drops - prev[3])
            self.port_prev[key] = (now, st.rx_bytes, st.tx_bytes, drops)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats(self, ev):
        self.flows[ev.msg.datapath.id] = sorted(
            [(f.byte_count, f.packet_count, str(f.match)) for f in ev.msg.body if f.priority > 0],
            reverse=True)[:3]

    # ---------- abnormality detection ----------
    def _eval_port(self, key, util, new_drops):
        node = f"s{key[0]}:p{key[1]}"
        sev = "CRITICAL" if util >= CFG["util_crit"] else "WARNING" if util >= CFG["util_warn"] else None
        self._set_alert("HIGH_UTILIZATION", node, sev, f"util={util:.1f}% (warn {CFG['util_warn']}%, crit {CFG['util_crit']}%)"
                        + self._culprit(sev))
        self._set_alert("PORT_DROPS", node, "WARNING" if new_drops > 0 else None, f"+{new_drops} drops/errors")

    def _culprit(self, sev):
        # correlation: which host agents report heavy tx while the switch port is hot
        if not sev:
            return ""
        c = [h for h, v in self.hosts.items()
             if v["tx_bps"] / CFG["capacity_bps"] * 100 >= CFG["host_tx_high_pct"]]
        return f" | likely source: {', '.join(c)}" if c else " | no host agent reports high tx (check flows)"

    def _check_hosts(self):
        now = time.time()
        for name, h in self.hosts.items():
            silent = now - h["last_seen"] > CFG["silent_after_s"]
            self._set_alert("HOST_SILENT", name, "CRITICAL" if silent else None,
                            f"no telemetry for {now - h['last_seen']:.0f}s")
            self._set_alert("TELEMETRY_LOSS", name, "WARNING" if h["lost"] > 0 and not silent else None,
                            f"{h['lost']} datagrams missed")

    def _set_alert(self, typ, node, sev, detail):
        key = (typ, node)
        if sev and self.active.get(key) != sev:
            self.active[key] = sev
            line = f"{time.strftime('%H:%M:%S')} [{sev}] {typ} @ {node}: {detail}"
            print("\n!!! ALERT " + line); self.alertlog.write(line + "\n"); self.alertlog.flush()
        elif not sev and key in self.active:
            del self.active[key]
            line = f"{time.strftime('%H:%M:%S')} [CLEARED] {typ} @ {node}"
            print("\n--- " + line); self.alertlog.write(line + "\n"); self.alertlog.flush()

    # ---------- presentation ----------
    def _report(self):
        now = time.time()
        print("\n" + "=" * 62 + f"\n {time.strftime('%H:%M:%S')}  NETWORK STATUS")
        print(" SWITCH PORTS         util%   state")
        for (dpid, port), u in sorted(self.port_util.items()):
            st = "CRIT" if u >= CFG["util_crit"] else "WARN" if u >= CFG["util_warn"] else "ok"
            print(f"  s{dpid}:p{port:<14}{u:6.1f}   {st}")
        print(" HOSTS (agent)        tx Mbps  lost  last_seen")
        for n, h in sorted(self.hosts.items()):
            print(f"  {n:<18}{h['tx_bps']/1e6:7.2f}  {h['lost']:4d}  {now - h['last_seen']:.0f}s ago")
        for dpid, fl in self.flows.items():
            for b, pk, m in fl[:1]:
                print(f" top flow s{dpid}: {b} B / {pk} pkts  {m[:60]}")
        print(f" ACTIVE ALERTS: {len(self.active)}")
