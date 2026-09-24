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


# ── start at login ───────────────────────────────────────────────────────────────────────────────
import json as _json
import os
import plistlib
from types import SimpleNamespace


def _args(**kw):
    base = dict(port=8000, host="127.0.0.1", data="relay_data", tunnel=False, tailscale=True, no_save=False)
    return SimpleNamespace(**{**base, **kw})


def test_login_entry_repeats_the_chosen_options():
    cmd = run_relay.autostart_command(_args())
    assert cmd[1].endswith("run_relay.py") and "--tailscale" in cmd and "--autostart" in cmd
    assert os.path.isabs(cmd[cmd.index("--data") + 1])                    # works from any working directory
    assert "--tunnel" in run_relay.autostart_command(_args(tailscale=False, tunnel=True))


def test_macos_login_item_keeps_path_and_restarts(tmp_path, monkeypatch):
    cmd = ["/usr/bin/python3", "/x/run_relay.py", "--tailscale", "--autostart"]
    plist = plistlib.loads(run_relay.launchd_plist(cmd, "/tmp/r.log", "/opt/homebrew/bin:/usr/bin"))
    assert plist["ProgramArguments"] == cmd and plist["RunAtLoad"] and plist["KeepAlive"]
    assert plist["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin")     # tailscale is found at login

    calls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(run_relay.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(run_relay.subprocess, "run",
                        lambda c, **k: calls.append(c) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert run_relay.install_autostart(_args()) == 0
    agent = tmp_path / "Library" / "LaunchAgents" / "com.ansx.relay.plist"
    assert "--tailscale" in plistlib.loads(agent.read_bytes())["ProgramArguments"]
    assert calls[-1][:2] == ["launchctl", "bootstrap"] and calls[-1][-1] == str(agent)
    run_relay.remove_autostart()
    assert not agent.exists() and calls[-1][:2] == ["launchctl", "bootout"]


def test_linux_and_windows_login_entries():
    cmd = ["/usr/bin/python3", "/home/me/ANSX Vault/run_relay.py", "--tailscale", "--autostart"]
    unit = run_relay.systemd_unit(cmd, "/usr/local/bin:/usr/bin")
    assert 'ExecStart=/usr/bin/python3 "/home/me/ANSX Vault/run_relay.py" --tailscale' in unit and "Restart=on-failure" in unit
    task = run_relay.windows_task_command([r"C:\Py\pythonw.exe", r"C:\ANSX Vault\run_relay.py", "--tailscale"], r"C:\l\relay.log")
    assert task[:5] == ["schtasks", "/Create", "/F", "/SC", "ONLOGON"]
    assert '"C:\\ANSX Vault\\run_relay.py"' in task[-1] and task[-1].endswith(r"--log C:\l\relay.log")


def test_at_login_it_waits_for_tailscale_instead_of_giving_up(monkeypatch):
    tries = []

    def step():
        tries.append(1)
        if len(tries) < 3:
            raise SystemExit("Tailscale is installed but not connected.")
        return "https://vault-mac.tail1234.ts.net"

    monkeypatch.setattr(run_relay.time, "sleep", lambda s: None)
    assert run_relay._keep_trying(step, autostart=True) == "https://vault-mac.tail1234.ts.net" and len(tries) == 3
    with pytest.raises(SystemExit):                                          # run by hand: say so and stop
        run_relay._keep_trying(lambda: (_ for _ in ()).throw(SystemExit("no")), autostart=False)


def test_permanent_address_is_built_into_the_app(tmp_path, monkeypatch):
    (tmp_path / "default_config.json").write_text('{"relay_url": "https://old.example", "other": 1}')
    run_relay.write_bundled_address("https://vault-mac.tail1234.ts.net", root=str(tmp_path))
    data = _json.loads((tmp_path / "default_config.json").read_text())
    assert data == {"relay_url": "https://vault-mac.tail1234.ts.net", "other": 1}    # other settings kept

    import relay_config
    monkeypatch.setattr(relay_config.os.path, "abspath", lambda p: str(tmp_path / "relay_config.py"))
    assert relay_config.bundled_default() == "https://vault-mac.tail1234.ts.net"      # what a fresh install uses
