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


@pytest.mark.parametrize("bare", [
    "2001:db8::1", "::1", "fe80::1%eth0", "2001:db8::1:5000",
    "0:0:0:0:0:0:0:1",
])
def test_a_bare_ipv6_is_not_a_host_port_pair(bare):
    """⚠️ Голый IPv6 — это адрес ЦЕЛИКОМ, а не «хост и порт».

    Отличить `2001:db8::1:5000` от «`2001:db8::1` на порту 5000» нельзя в
    принципе: обе строки законны. Прежний `rpartition(":")` резал адрес по
    последнему двоеточию, и `2001:db8::1` превращался в хост `2001:db8:` с
    портом 1 — узел ходил стучаться в несуществующий адрес. Такие строки
    приходят из `peers`-сообщения соседа, то есть таблицу можно было засорять
    УДАЛЁННО.
    """
    assert split_host_port(bare) is None


@pytest.mark.parametrize("text", ["1.2.3.4:0", "[::1]:0", "1.2.3.4:65536",
                                  "1.2.3.4:99999", "[::1]:70000"])
def test_port_must_be_in_range(text):
    """Порт 0 и всё сверх 65535 соединением не станут никогда."""
    assert split_host_port(text) is None


def test_port_boundaries_are_accepted():
    assert split_host_port("1.2.3.4:1") == ("1.2.3.4", 1)
    assert split_host_port("1.2.3.4:65535") == ("1.2.3.4", 65535)


@pytest.mark.parametrize("text", ["[not-an-addr]:80", "[1.2.3.4]:80",
                                  "[example.com]:80", "[]:80"])
def test_brackets_are_only_for_ipv6(text):
    """В скобках обязан быть настоящий IPv6-литерал, а не что попало."""
    assert split_host_port(text) is None


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
    assert normalise_host("FE80::0001%eth0") == "fe80::1%eth0"   # и сводится


@pytest.mark.parametrize("spelling", [
    "2001:db8::1",
    "2001:0db8::1",
    "2001:0DB8:0000:0000:0000:0000:0000:0001",
    "2001:db8:0:0:0:0:0:1",
    "2001:DB8::0001",
    "[2001:0db8::1]",
])
def test_every_spelling_of_one_address_gives_one_key(spelling):
    """⚠️ У одного /128 написаний десятки, и все обязаны сойтись в одно.

    Ведущие нули независимо в каждой из восьми групп, `::` на любом месте
    нулевой серии, регистр hex-цифр. Пока написания разные, сосед их
    переписыванием заводит себе новый счётчик и новый ключ в таблице.
    """
    assert normalise_host(spelling) == "2001:db8::1"


@pytest.mark.parametrize("mapped", [
    "::ffff:1.2.3.4", "::FFFF:1.2.3.4", "0:0:0:0:0:ffff:1.2.3.4",
    "[::ffff:1.2.3.4]",
])
def test_every_mapped_form_folds_to_ipv4(mapped):
    """`ipv4_mapped` ловит и те записи, которые проверка по префиксу пропускала."""
    assert normalise_host(mapped) == "1.2.3.4"


def test_a_colonful_non_address_does_not_crash():
    """Строка с двоеточиями, адресом не являющаяся, не должна ронять разбор."""
    assert normalise_host("не:адрес:вовсе") == "не:адрес:вовсе"
    assert normalise_host("http://example.com") == "http://example.com"


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


def test_rewriting_the_address_does_not_buy_extra_connection_slots():
    """⚠️ Сосед не должен обходить `MAX_INBOUND_PER_HOST` переписыванием адреса.

    Счёт входящих идёт по хосту, и пока написания IPv6 сравнивались строками,
    каждая форма одного и того же /128 заводила СВОЙ счётчик: лимит обходился
    столько раз, сколько написаний придумает сосед, — а их десятки.
    """
    from b_hydra.p2p import MAX_INBOUND_PER_HOST

    node = P2PNode("127.0.0.1", 5406, None)
    spellings = ["2001:db8::5", "2001:0db8::5", "2001:DB8:0:0:0:0:0:5",
                 "[2001:0DB8::0005]"]
    taken = 0
    for index in range(MAX_INBOUND_PER_HOST):
        assert node._claim_host_slot(spellings[index % len(spellings)]) is True
        taken += 1
    assert taken == MAX_INBOUND_PER_HOST
    # Лимит выбран — любое написание того же адреса обязано получить отказ.
    for spelling in spellings:
        assert node._claim_host_slot(spelling) is False, spelling
    assert node.inbound_connections("2001:db8::5") == MAX_INBOUND_PER_HOST


def test_a_ban_covers_every_spelling_of_the_address():
    """Бан по одной форме обязан действовать на все остальные."""
    node = P2PNode("127.0.0.1", 5407, None)
    node.ban_peer("2001:0DB8:0000:0000:0000:0000:0000:0009")
    for spelling in ("2001:db8::9", "2001:0db8::9", "[2001:DB8::9]"):
        assert node.is_banned(spelling) is True, spelling
        assert node.add_peer(spelling, 5000) is False, spelling


def test_ipv4_keys_in_the_peers_file_are_byte_identical():
    """⚠️ Совместимость: узлы прежних версий обязаны читать записи как читали.

    Ключи IPv4 в файле пиров и в сообщении `peers` не меняются ни на байт —
    скобки появляются только у IPv6, которого раньше не было вовсе.
    """
    assert join_host_port("192.168.1.50", 5000) == "192.168.1.50:5000"
    assert join_host_port("10.0.0.1", 65535) == "10.0.0.1:65535"
    # И читаются обратно тем же разбором, что и раньше.
    assert split_host_port("192.168.1.50:5000") == ("192.168.1.50", 5000)
