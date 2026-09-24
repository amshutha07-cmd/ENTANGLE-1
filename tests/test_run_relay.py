"""run_relay.py --tailscale: the permanent address comes from Tailscale, and a Tailscale that isn't ready is a clear message."""
import stat
import sys
import time

import pytest

import run_relay


def _fake_tailscale(tmp_path, status: dict, version: str = "1.102.4\n  tailscale commit: abc"):
    """A stand-in `tailscale` CLI: `version`, `status --json` prints `status`; `funnel PORT` logs its args and stays up."""
    log = tmp_path / "funnel.log"
    exe = tmp_path / "tailscale"
    exe.write_text(f"""#!{sys.executable}
import json, sys, time
if sys.argv[1:2] == ["version"]:
    print({version!r})
elif sys.argv[1:3] == ["status", "--json"]:
    print(json.dumps({status!r}))
elif sys.argv[1] == "funnel":
    open({str(log)!r}, "a").write(" ".join(sys.argv[1:]) + "\\n")
    print("Available on the internet:", flush=True)
    time.sleep(30)
""")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe), log


def test_permanent_address_comes_from_the_tailnet_name(tmp_path):
    exe, _ = _fake_tailscale(tmp_path, {"BackendState": "Running", "Self": {"DNSName": "vault-mac.tail1234.ts.net."}})
    assert run_relay.tailscale_address(exe) == "https://vault-mac.tail1234.ts.net"


@pytest.mark.parametrize("status, hint", [
    ({"BackendState": "NeedsLogin", "Self": {}}, "tailscale up"),
    ({"BackendState": "Running", "Self": {"DNSName": ""}}, "MagicDNS"),
])
def test_tailscale_not_ready_is_explained(tmp_path, status, hint):
    exe, _ = _fake_tailscale(tmp_path, status)
    with pytest.raises(SystemExit) as e:
        run_relay.tailscale_address(exe)
    assert hint in str(e.value)


def test_funnel_publishes_the_relay_port_and_announces_once(tmp_path):
    exe, log = _fake_tailscale(tmp_path, {})
    seen = []
    funnel = run_relay.TailscaleFunnel(exe, 8123, "https://vault-mac.tail1234.ts.net", seen.append)
    funnel.start()
    try:
        end = time.time() + 10
        while time.time() < end and not (log.exists() and seen):
            time.sleep(0.1)
        assert log.read_text().split() == ["funnel", "8123"]
        assert seen == ["https://vault-mac.tail1234.ts.net"]
    finally:
        funnel.stop()


def test_tunnel_and_tailscale_cannot_be_combined(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_relay.py", "--tunnel", "--tailscale"])
    with pytest.raises(SystemExit) as e:
        run_relay.main()
    assert e.value.code == 2                                         # argparse usage error, before anything starts


@pytest.mark.parametrize("version, ok", [("1.102.4", True), ("1.52.0", True), ("1.50.1", False), ("0.99", False),
                                         ("2.0.0-dev", True)])
def test_old_tailscale_is_refused_with_an_update_hint(tmp_path, version, ok):
    exe, _ = _fake_tailscale(tmp_path, {}, version=version + "\n  go version: go1.22")
    if ok:
        assert run_relay.check_tailscale_version(exe) == version
    else:
        with pytest.raises(SystemExit) as e:
            run_relay.check_tailscale_version(exe)
        assert "too old" in str(e.value) and "1.52" in str(e.value)


def test_unreadable_version_does_not_block(tmp_path):
    exe, _ = _fake_tailscale(tmp_path, {}, version="unknown")
    assert run_relay.check_tailscale_version(exe) == "unknown"
