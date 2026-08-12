"""Поддержка IPv6: разбор адресов, двойной стек, сведение написаний.

Зачем это узлу. У домашних провайдеров IPv4 всё чаще за CGNAT — входящее
соединение туда не приходит вовсе, и узел виден только исходящими. IPv6 при
этом выдаётся честный, и часто это ЕДИНСТВЕННЫЙ способ принять соседа.

⚠️ ЧЕГО ЭТИ ТЕСТЫ НЕ ПРОВЕРЯЮТ. В контейнере разработки IPv6 нет совсем:
`socket.socket(AF_INET6, …)` отвечает «Address family not supported by
protocol», интерфейсов с inet6 ноль. Поэтому живое соединение по IPv6 здесь
проверить нечем — как и передачу по Bluetooth. Проверено всё остальное:
разбор и запись адресов, сведение `::ffff:`-написаний, откат на чистый IPv4
(эта ветка тут как раз рабочая, а не запасная) и то, что IPv4 не пострадал.
"""

import socket

import pytest

from b_hydra.p2p import P2PNode, parse_seeds
from b_hydra.transport import (TCPTransport, is_ipv6, join_host_port,
                               normalise_host, split_host_port)

HAS_IPV6 = False
if socket.has_ipv6:
    try:
        _probe = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        _probe.close()
        HAS_IPV6 = True
    except OSError:
        HAS_IPV6 = False

needs_ipv6 = pytest.mark.skipif(not HAS_IPV6, reason="в системе нет IPv6")


# --- Запись адреса -------------------------------------------------------------
def test_ipv4_format_is_unchanged():
    """⚠️ Ключи IPv4 обязаны остаться байт-в-байт прежними.

    Они уходят в файл пиров и в сообщение `peers`, поэтому изменение формата
    сделало бы записи нечитаемыми для узлов прежних версий.
    """
    assert join_host_port("1.2.3.4", 5000) == "1.2.3.4:5000"
    assert join_host_port("localhost", 5000) == "localhost:5000"
    assert join_host_port("node.example.com", 80) == "node.example.com:80"


def test_ipv6_is_written_in_brackets():
    """Скобки — стандарт (RFC 3986) и единственная однозначная запись."""
    assert join_host_port("::1", 5000) == "[::1]:5000"
    assert join_host_port("2001:db8::1", 5000) == "[2001:db8::1]:5000"


def test_is_ipv6_recognises_by_the_colon():
    assert is_ipv6("::1") and is_ipv6("2001:db8::1")
    assert not is_ipv6("1.2.3.4")
    assert not is_ipv6("example.com")


# --- Разбор адреса -------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("1.2.3.4:5000", ("1.2.3.4", 5000)),
    ("localhost:5000", ("localhost", 5000)),
    ("[::1]:5000", ("::1", 5000)),
    ("[2001:db8::1]:5000", ("2001:db8::1", 5000)),
    ("2001:db8::1:5000", ("2001:db8::1", 5000)),        # наивная запись
    ("  1.2.3.4:5000  ", ("1.2.3.4", 5000)),
])
def test_split_host_port(text, expected):
    assert split_host_port(text) == expected


@pytest.mark.parametrize("junk", [
    "", "мусор", "1.2.3.4", "1.2.3.4:", ":5000", "[::1]", "[::1:5000",
    "[::1]5000", "1.2.3.4:порт", "[::1]:порт",
])
def test_split_host_port_refuses_junk(junk):
    """Мусор — None, а не пара с нулевым портом: молча звонить в никуда нельзя."""
    assert split_host_port(junk) is None


def test_round_trip_through_write_and_read():
    """Что записали — то и прочитали. На этом держится файл пиров."""
    for host, port in (("1.2.3.4", 5000), ("::1", 5000),
                       ("2001:db8::dead:beef", 65535), ("example.com", 80)):
        assert split_host_port(join_host_port(host, port)) == (host, port)


def test_brackets_fix_what_rpartition_broke():
    """⚠️ Прежний разбор давал на скобочной форме неработоспособный хост.

    `"[::1]:5000".rpartition(":")` → `("[::1]", "5000")`, и соединиться с
    `[::1]` нельзя: скобки — часть записи, а не адреса.
    """
    assert "[::1]:5000".rpartition(":")[0] == "[::1]"      # как было
    assert split_host_port("[::1]:5000") == ("::1", 5000)  # как стало


# --- Сведение написаний --------------------------------------------------------
def test_ipv4_mapped_address_folds_to_plain_ipv4():
    """⚠️ Двойной стек отдаёт IPv4-соседа как `::ffff:1.2.3.4` — это он же.

    Без сведе́ния один сосед занял бы ДВЕ записи в таблице, вдвое обошёл бы
    лимит соединений с хоста, а бан одной формы не подействовал бы на вторую.
    """
    assert normalise_host("::ffff:1.2.3.4") == "1.2.3.4"
    assert normalise_host("::FFFF:1.2.3.4") == "1.2.3.4"
    assert normalise_host("[::ffff:1.2.3.4]") == "1.2.3.4"


def test_normalise_keeps_real_addresses_intact():
    assert normalise_host("1.2.3.4") == "1.2.3.4"
    assert normalise_host("example.com") == "example.com"
    assert normalise_host("[::1]") == "::1"
    assert normalise_host("2001:DB8::1") == "2001:db8::1"


def test_zone_index_is_preserved():
    """`fe80::1%eth0` без зоны неработоспособен — обрезать её нельзя."""
    assert normalise_host("fe80::1%eth0") == "fe80::1%eth0"


# --- Seeds ---------------------------------------------------------------------
def test_parse_seeds_understands_both_families():
    seeds = parse_seeds(["1.2.3.4:5000", "[2001:db8::1]:5001", "мусор",
                         "example.com:5002"])
    assert seeds == [("1.2.3.4", 5000), ("2001:db8::1", 5001),
                     ("example.com", 5002)]


def test_parse_seeds_skips_junk_silently():
    assert parse_seeds(["", "abc", None, "[::1]"]) == []


# --- Таблица пиров и баны ------------------------------------------------------
def test_the_same_peer_in_two_notations_is_one_peer():
    """Главное следствие сведения: сосед не удваивается."""
    node = P2PNode("127.0.0.1", 5401, None)
    assert node.add_peer("10.0.0.7", 5000) is True
    assert node.add_peer("::ffff:10.0.0.7", 5000) is False   # он же
    assert node.peer_list() == [("10.0.0.7", 5000)]


def test_a_ban_covers_both_notations():
    """Бан по одной записи обязан действовать на обе — иначе он обходится."""
    node = P2PNode("127.0.0.1", 5402, None)
    node.ban_peer("::ffff:10.0.0.8")
    assert node.is_banned("10.0.0.8") is True
    assert node.is_banned("::ffff:10.0.0.8") is True
    assert node.add_peer("10.0.0.8", 5000) is False


def test_ipv6_loopback_is_not_banned_by_default():
    """`::1` — та же петля, что `127.0.0.1`: банить её нельзя.

    Демо и тесты поднимают десятки узлов на одной машине, и общий банлист
    положил бы их все.
    """
    node = P2PNode("127.0.0.1", 5403, None)
    assert node._is_loopback("::1") is True
    assert node._is_loopback("[::1]") is True
    assert node._is_loopback("127.0.0.1") is True


def test_ipv6_peer_survives_the_peers_file(tmp_path):
    """IPv6-сосед обязан пережить перезапуск: он пишется и читается обратно."""
    path = str(tmp_path / "peers.json")
    first = P2PNode("127.0.0.1", 5404, None, peers_file=path)
    first.add_peer("2001:db8::1", 5000)
    first.add_peer("1.2.3.4", 5001)
    first.save_peers()

    second = P2PNode("127.0.0.1", 5405, None, peers_file=path)
    second.load_peers()
    assert ("2001:db8::1", 5000) in second.peer_list()
    assert ("1.2.3.4", 5001) in second.peer_list()


# --- Транспорт -----------------------------------------------------------------
def test_listen_works_whatever_the_system_supports():
    """Узел обязан подняться и там, где IPv6 нет.

    ⚠️ В этом контейнере IPv6 отсутствует, поэтому здесь проверяется именно
    ОТКАТ на IPv4 — то есть ветка, которая иначе осталась бы непройденной.
    """
    transport = TCPTransport()
    server = transport.listen(0)
    try:
        assert server.getsockname()[1] > 0
        assert server.family in (socket.AF_INET, socket.AF_INET6)
    finally:
        transport.close_listener(server)


def test_dual_stack_probe_never_raises():
    """Проба двойного стека обязана возвращать None, а не падать."""
    result = TCPTransport._dual_stack_socket()
    if result is not None:
        assert result.family == socket.AF_INET6
        result.close()


def test_ipv4_still_connects_end_to_end():
    """IPv4 не должен пострадать: соединение и обмен байтами как раньше."""
    transport = TCPTransport()
    server = transport.listen(0)
    try:
        port = server.getsockname()[1]
        client = transport.connect("127.0.0.1", port, timeout=5)
        conn, host = transport.accept(server)
        try:
            assert host in ("127.0.0.1", "::1")
            client.sendall(b"ping")
            assert conn.recv(4) == b"ping"
        finally:
            conn.close()
            client.close()
    finally:
        transport.close_listener(server)


@needs_ipv6
def test_ipv6_connects_end_to_end():
    """Живое соединение по IPv6. Пропускается там, где IPv6 нет."""
    transport = TCPTransport()
    server = transport.listen(0)
    try:
        port = server.getsockname()[1]
        client = transport.connect("::1", port, timeout=5)
        conn, host = transport.accept(server)
        try:
            assert normalise_host(host) == "::1"
            client.sendall(b"ping6")
            assert conn.recv(5) == b"ping6"
        finally:
            conn.close()
            client.close()
    finally:
        transport.close_listener(server)


@needs_ipv6
def test_dual_stack_accepts_ipv4_too():
    """Один сокет обязан принимать обе семьи, и IPv4 приходить как 1.2.3.4."""
    transport = TCPTransport()
    server = transport.listen(0)
    try:
        if server.family != socket.AF_INET6:
            pytest.skip("двойной стек в этой системе недоступен")
        port = server.getsockname()[1]
        client = transport.connect("127.0.0.1", port, timeout=5)
        conn, host = transport.accept(server)
        try:
            assert host == "127.0.0.1", "IPv4-сосед пришёл как ::ffff:…"
        finally:
            conn.close()
            client.close()
    finally:
        transport.close_listener(server)
