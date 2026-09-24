#!/usr/bin/env python3
"""
run_relay.py — start the ANSX relay on this computer, optionally with a free public HTTPS address.

    python run_relay.py                # relay only, http://127.0.0.1:8000 (same computer / same network)
    python run_relay.py --tunnel       # relay + free Cloudflare tunnel: prints an https://….trycloudflare.com address
    python run_relay.py --tailscale    # relay + Tailscale Funnel: a PERMANENT https://<computer>.<tailnet>.ts.net address

With --tunnel or --tailscale the address is also saved as this computer's relay setting, so the app on THIS
computer is already connected. Everyone else pastes the printed address into Settings → Connection.

Keep this window open while people use the relay. Ctrl+C stops everything. The free Cloudflare address changes
every time you start it; the Tailscale address never does, so people only need to enter it once. The relay's
data (names, waiting files) lives in ./relay_data and survives restarts.

--tailscale needs the Tailscale app, signed in (`tailscale up`), with Funnel allowed for your tailnet. The first
run prints a link to switch Funnel on if it is not. People who USE the relay do not need Tailscale.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import socket
import ssl
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


TAILSCALE_APP = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"     # CLI bundled with the macOS app
TAILSCALE_MIN = (1, 52)            # first version with `tailscale funnel <port>`; older ones use a different command


def tailscale_exe() -> str:
    exe = shutil.which("tailscale")
    if exe:
        return exe
    return TAILSCALE_APP if platform.system() == "Darwin" and os.path.exists(TAILSCALE_APP) else ""


def tailscale_install_hint() -> str:
    if platform.system() == "Darwin":
        return "brew install --cask tailscale-app    (or get the app from tailscale.com/download), then sign in"
    return "see https://tailscale.com/download, then run:  tailscale up"


def check_tailscale_version(exe: str) -> str:
    """The installed version (e.g. "1.102.4"). Exits with an update hint if it is too old for `tailscale funnel <port>`."""
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        sys.exit(f"Could not run Tailscale ({exc}). Is the Tailscale app running?")
    first = (out.strip().splitlines() or [""])[0].strip()
    m = re.match(r"(\d+)\.(\d+)", first)
    if not m:
        print(f"[tailscale] could not read the version from {first!r}; trying anyway.")
        return first
    if (int(m.group(1)), int(m.group(2))) < TAILSCALE_MIN:
        update = ("brew upgrade --cask tailscale-app  (or update it from the App Store / tailscale.com/download)"
                  if platform.system() == "Darwin" else "see https://tailscale.com/download")
        sys.exit(f"Tailscale {first} is too old: --tailscale needs version "
                 f"{TAILSCALE_MIN[0]}.{TAILSCALE_MIN[1]} or newer.\nUpdate it:  {update}")
    return first


def tailscale_address(exe: str) -> str:
    """This computer's permanent https address on the tailnet. Exits with a clear message if Tailscale is not ready."""
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=15)
        status = json.loads(out.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        sys.exit(f"Could not ask Tailscale for its status ({exc}). Is the Tailscale app running?")
    if status.get("BackendState") != "Running":
        sys.exit("Tailscale is installed but not connected. Sign in first:  tailscale up")
    name = ((status.get("Self") or {}).get("DNSName") or "").rstrip(".")
    if not name:
        sys.exit("Tailscale did not report a name for this computer. Turn on MagicDNS in the Tailscale admin console.")
    return f"https://{name}"


def install_hint() -> str:
    system = platform.system()
    if system == "Darwin":
        return "brew install cloudflared"
    if system == "Windows":
        return "winget install --id Cloudflare.cloudflared    (or download it from developers.cloudflare.com)"
    return "see https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"


def wait_until_up(url: str, seconds: float = 45) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + "/", timeout=4) as r:
                if b"ANSX Relay" in r.read():
                    return True
        except Exception:
            time.sleep(1.5)
    return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def resolve_via_public_dns(host: str) -> str:
    """Ask Cloudflare's public DNS (DNS-over-HTTPS) for the address, ignoring this computer's own resolver."""
    req = urllib.request.Request(f"https://1.1.1.1/dns-query?name={host}&type=A", headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        for ans in json.loads(r.read()).get("Answer", []):
            if ans.get("type") == 1:
                return ans["data"]
    return ""


def probe_public(url: str) -> bool:
    """Is the relay reachable through the tunnel? Uses public DNS so a slow local resolver cannot fool the check."""
    host = url.split("//", 1)[1]
    try:
        ip = resolve_via_public_dns(host)
        if not ip:
            return False
        ctx = ssl.create_default_context()
        with socket.create_connection((ip, 443), timeout=8) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                conn = http.client.HTTPSConnection(host, context=ctx)
                conn.sock = tls
                conn.request("GET", "/")
                return b"ANSX Relay" in conn.getresponse().read()
    except Exception:
        return False


def local_dns_ready(url: str) -> bool:
    try:
        socket.gethostbyname(url.split("//", 1)[1])
        return True
    except OSError:
        return False


class Tunnel:
    """Runs `cloudflared tunnel --url …`, captures the public address from its log, and restarts it if it dies."""

    def __init__(self, port: int, on_url):
        self.port, self.on_url = port, on_url
        self.proc: subprocess.Popen | None = None
        self.url = ""
        self._stop = False

    def start(self) -> None:
        exe = shutil.which("cloudflared")
        if not exe:
            sys.exit(f"cloudflared is not installed.\nInstall it with:  {install_hint()}")
        threading.Thread(target=self._loop, args=(exe,), daemon=True).start()

    def _loop(self, exe: str) -> None:
        while not self._stop:
            self.proc = subprocess.Popen(
                [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in self.proc.stdout:                       # cloudflared logs to stderr; merged above
                m = URL_RE.search(line)
                if m and m.group(0) != self.url:
                    self.url = m.group(0)
                    self.on_url(self.url)
            if self._stop:
                return
            print("\n[tunnel] stopped unexpectedly; restarting in 5 s (the address will change)…")
            self.url = ""
            time.sleep(5)

    def stop(self) -> None:
        self._stop = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class TailscaleFunnel:
    """Runs `tailscale funnel <port>` in the foreground (it stops when we do) and restarts it if it dies."""

    def __init__(self, exe: str, port: int, url: str, on_up):
        self.exe, self.port, self.url, self.on_up = exe, port, url, on_up
        self.proc: subprocess.Popen | None = None
        self._stop = False

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self) -> None:
        announced = False
        while not self._stop:
            self.proc = subprocess.Popen([self.exe, "funnel", str(self.port)],
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if not announced:
                announced = True
                threading.Thread(target=self.on_up, args=(self.url,), daemon=True).start()
            for line in self.proc.stdout:                     # shows e.g. the link to switch Funnel on
                if line.strip():
                    print(f"[tailscale] {line.rstrip()}")
            if self._stop:
                return
            print("\n[tailscale] funnel stopped unexpectedly; restarting in 5 s (the address stays the same)…")
            time.sleep(5)

    def stop(self) -> None:
        self._stop = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def announce(url: str, save: bool, permanent: bool = False) -> None:
    kind = "permanent address" if permanent else "address created"
    print(f"\n[tunnel] {kind}: {url}\n[tunnel] checking that it works from the internet…")
    end, works = time.time() + 90, False
    while time.time() < end and not works:
        works = probe_public(url)
        if not works:
            time.sleep(3)
    if not works:
        if permanent:
            print("[tunnel] could not confirm the address yet. If the [tailscale] lines above show a link, open it to allow "
                  "Funnel, then check again in a browser. A brand-new ts.net name can take a few minutes to appear.")
        else:
            print("[tunnel] could not confirm the address yet. Cloudflare's free tunnels can take a minute; try opening it in a browser.")
        return
    bar = "═" * 66
    print(f"\n{bar}\n  YOUR RELAY IS ONLINE (verified from the internet)\n\n  Address to share:  {url}\n{bar}")
    print("  Everyone pastes this into the app:  Settings → Connection → Test → Save")
    if permanent:
        print("  This address stays the same every time you start the relay.")
    if save:
        sys.path.insert(0, ROOT)
        try:
            import relay_config
            relay_config.set_relay_url(url)
            print("  (Saved as this computer's relay setting.)")
        except Exception as exc:                                  # never let this stop the relay
            print(f"  (Could not save the setting automatically: {exc})")
    if not local_dns_ready(url):
        print("\n  NOTE: THIS computer has not learned the new name yet (common with free tunnels).")
        print("  Other computers and phones are usually fine. Here, wait 1-2 minutes, or flush the DNS cache:")
        if platform.system() == "Darwin":
            print("      sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder")
        elif platform.system() == "Windows":
            print("      ipconfig /flushdns")
        else:
            print("      sudo resolvectl flush-caches")
        print("  (Or set this computer's DNS servers to 1.1.1.1 and 8.8.8.8.)")
    print("\n  Keep this window open. Press Ctrl+C to stop.\n")


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)     # show output immediately, even when piped to a file or a service
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Run the ANSX relay on this computer.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other computers on your network directly")
    ap.add_argument("--data", default=os.path.join(ROOT, "relay_data"), help="where names and waiting files are kept")
    public = ap.add_mutually_exclusive_group()
    public.add_argument("--tunnel", action="store_true", help="also create a free public https address (Cloudflare; changes each start)")
    public.add_argument("--tailscale", action="store_true",
                        help="also publish a PERMANENT https address with Tailscale Funnel (needs Tailscale, signed in)")
    ap.add_argument("--no-save", action="store_true", help="do not save the public address as this computer's setting")
    args = ap.parse_args()

    ts_exe = ts_url = ""
    if args.tailscale:                            # check before starting anything, so a mistake costs nothing
        ts_exe = tailscale_exe()
        if not ts_exe:
            sys.exit(f"Tailscale is not installed.\nInstall it with:  {tailscale_install_hint()}")
        check_tailscale_version(ts_exe)
        ts_url = tailscale_address(ts_exe)

    local = f"http://127.0.0.1:{args.port}"
    relay = None                                  # stays None when we reuse a relay that is already running
    if wait_until_up(local, 1.5):
        print(f"A relay is already running on port {args.port}. Reusing it (its data is untouched).")
    else:
        if port_in_use(args.port):
            sys.exit(f"Port {args.port} is used by another program that is not an ANSX relay.\n"
                     f"Use a different port:  python3 run_relay.py --tunnel --port {args.port + 1}")
        env = dict(os.environ, ANSX_RELAY_DATA=args.data)
        relay = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "relay.server:create_app", "--factory", "--host", args.host,
             "--port", str(args.port), "--log-level", "warning", "--proxy-headers"],
            cwd=ROOT, env=env)
        if not wait_until_up(local, 20):
            relay.terminate()
            sys.exit("The relay did not start. See the error above; you can also try --port %d." % (args.port + 1))
        print(f"Relay running at {local}   (data folder: {args.data})")

    tunnel = None
    if args.tunnel:
        print("Opening a free public tunnel (takes a few seconds)…")
        tunnel = Tunnel(args.port, lambda u: announce(u, save=not args.no_save))
        tunnel.start()
    elif args.tailscale:
        print(f"Publishing {ts_url} with Tailscale Funnel…")
        tunnel = TailscaleFunnel(ts_exe, args.port, ts_url, lambda u: announce(u, save=not args.no_save, permanent=True))
        tunnel.start()
    else:
        print("No tunnel: only this computer (and your local network, with --host 0.0.0.0) can reach it.")

    awake = None
    if platform.system() == "Darwin" and shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])   # stop the Mac idling to sleep while we run

    def shutdown(*_a):
        print("\nStopping…")
        if tunnel:
            tunnel.stop()
        if relay is not None:                       # only stop a relay that this launcher started
            relay.terminate()
        if awake:
            awake.terminate()
        sys.exit(0)

    if relay is None and tunnel is None:
        print("Nothing to start: a relay is already running and no tunnel was requested (add --tunnel or --tailscale).")
        return 0
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        if relay is not None and relay.poll() is not None:
            print("The relay stopped unexpectedly (exit code %s)." % relay.returncode)
            if tunnel:
                tunnel.stop()
            return 1
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
