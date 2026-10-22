from pathlib import Path
from unittest.mock import patch

from signalbloom_agent.collectors import gateway

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_windows_picks_lowest_metric_real_gateway():
    # The fixture also has an On-link route (not a gateway address), a route with
    # a higher metric, and a persistent route that isn't active.
    assert gateway._gateway_from_windows_route(fixture("route_windows.txt")) == "192.168.88.1"


def test_windows_no_default_route():
    assert gateway._gateway_from_windows_route("Active Routes:\nNone\n") is None


def test_linux_picks_lowest_metric():
    # 0158A8C0 = 192.168.88.1 (metric 600), FE01A8C0 = 192.168.1.254 (metric 100)
    assert gateway._gateway_from_proc_net_route(fixture("proc_net_route.txt")) == "192.168.1.254"


def test_macos():
    assert gateway._gateway_from_macos_route(fixture("route_macos.txt")) == "192.168.1.1"


def test_failure_returns_none(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    with patch.object(gateway, "_run", side_effect=FileNotFoundError()):
        assert gateway.find_default_gateway() is None


def test_find_uses_os_specific_source(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    with patch.object(gateway, "_run", return_value=fixture("route_macos.txt")) as run:
        assert gateway.find_default_gateway() == "192.168.1.1"
    assert run.call_args.args[0] == ["route", "-n", "get", "default"]
