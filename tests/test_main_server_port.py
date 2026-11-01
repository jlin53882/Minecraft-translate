"""網頁服務連接埠選擇：偏好埠被占用時不可讓 Flet 啟動失敗。"""

import socket

import main


def _occupy_port() -> tuple[socket.socket, int]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    return listener, listener.getsockname()[1]


def test_free_preferred_port_is_kept():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    assert main.resolve_server_port(port) == port


def test_busy_preferred_port_falls_back_to_a_free_port(caplog):
    listener, busy = _occupy_port()
    try:
        chosen = main.resolve_server_port(busy)
    finally:
        listener.close()

    assert chosen != busy
    assert 0 < chosen < 65536
    assert main._port_is_free(chosen)
    assert any("已被占用" in record.getMessage() for record in caplog.records)


def test_env_port_parsing(monkeypatch):
    monkeypatch.setenv("FLET_SERVER_PORT", "8551")
    assert main._preferred_server_port() == 8551

    monkeypatch.setenv("FLET_SERVER_PORT", "not-a-port")
    assert main._preferred_server_port() is None

    monkeypatch.setenv("FLET_SERVER_PORT", "70000")
    assert main._preferred_server_port() is None

    monkeypatch.delenv("FLET_SERVER_PORT", raising=False)
    assert main._preferred_server_port() is None


def test_desktop_mode_keeps_flet_automatic_port(monkeypatch):
    monkeypatch.delenv("FLET_SERVER_PORT", raising=False)
    monkeypatch.delenv("FLET_FORCE_WEB_SERVER", raising=False)

    assert main._web_server_port() == 0


def test_web_mode_checks_the_env_port(monkeypatch):
    listener, busy = _occupy_port()
    monkeypatch.setenv("FLET_SERVER_PORT", str(busy))
    try:
        chosen = main._web_server_port()
    finally:
        listener.close()

    assert chosen != busy
