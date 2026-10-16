"""Finding the GFX PC's HUD on the local network: every address on this PC's
own networks is tried on the HUD's port, and those answering as a HUD (not
another observer) are returned."""

import concurrent.futures
import ipaddress
import json
import socket
import urllib.request


def own_addresses():
    """This PC's IPv4 addresses on private networks."""
    found = set()
    try:
        found.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))   # (no traffic is sent)
            found.add(s.getsockname()[0])
    except OSError:
        pass
    out = []
    for a in found:
        try:
            ip = ipaddress.IPv4Address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            out.append(str(ip))
    return sorted(out)


def _open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _state(ip, port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # straight there, never via a proxy
    with opener.open(f"http://{ip}:{port}/api/state", timeout=2) as r:
        return json.loads(r.read())


def find_huds(port, is_hud, timeout=0.35, addresses=None):
    """[{"ip", "url", "state"}] of the PCs on this PC's networks (each /24)
    whose /api/state at port passes is_hud(state)."""
    mine = set(addresses if addresses is not None else own_addresses())
    candidates = []
    for a in sorted(mine):
        net = ipaddress.IPv4Network(a + "/24", strict=False)
        candidates += [str(h) for h in net.hosts() if str(h) not in mine]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        open_ips = [ip for ip, ok in zip(candidates, pool.map(lambda ip: _open(ip, port, timeout), candidates)) if ok]

        def check(ip):
            try:
                state = _state(ip, port)
                return {"ip": ip, "url": f"http://{ip}:{port}", "state": state} if is_hud(state) else None
            except Exception:
                return None
        found = [hit for hit in pool.map(check, open_ips) if hit]
    return found
