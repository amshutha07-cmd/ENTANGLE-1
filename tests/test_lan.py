"""Peer discovery on the local network: a busy port is waited for, never given up on; listeners can share it."""
import os
import socket
import time

import pytest

import web3_bridge


def _free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _until(cond, seconds=3.0) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


@pytest.fixture
def port(monkeypatch):
    p = _free_udp_port()                                   # never the real discovery port
    monkeypatch.setattr(web3_bridge, "_BROADCAST_PORT", p)
    monkeypatch.setattr(web3_bridge, "_LISTEN_RETRY", 0.05)
    return p


def test_a_busy_port_is_waited_for_not_given_up_on(port):
    """Regression: when another program held the port at start, discovery stayed off for the whole session."""
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # holds the port, without sharing it
    blocker.bind(("", port))
    lan = web3_bridge._LANDiscovery()
    lan.start_listener()
    try:
        time.sleep(0.2)
        assert not lan.listening
        blocker.close()
        assert _until(lambda: lan.listening), "never started listening once the port was free"
    finally:
        blocker.close()
        lan.stop()
    assert _until(lambda: not lan.listening)


@pytest.mark.skipif(os.name == "nt", reason="Windows has no SO_REUSEPORT")
def test_two_copies_can_listen_at_once(port):
    first, second = web3_bridge._LANDiscovery(), web3_bridge._LANDiscovery()
    first.start_listener()
    second.start_listener()
    try:
        assert _until(lambda: first.listening and second.listening)
    finally:
        first.stop()
        second.stop()
