"""Seed-подключение для кошельков: как телефон входит в сеть по одному адресу.

Seed-узлы у B-hydra были всегда — но только между УЗЛАМИ (`--seed`,
`bootstrap`). Кошелёк узлом не является и никогда им не станет: в браузере и
в WebView нет сырых TCP-сокетов, нашим кадровым протоколом оттуда не заговорить.
Значит, ему нужна HTTP-точка входа — и способ узнать остальные, иначе телефон
навсегда привязан к одному узлу: тот упал, и кошелёк слеп при работающей сети.

Здесь проверяется вся цепочка: узел объявляет свой REST-адрес соседям, соседи
разносят их дальше, `/api/nodes` отдаёт список кошельку, а клиентская часть
(`bhydra-net.js`) подтягивает его и не даёт затащить себя в чужую сеть.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from b_hydra.api import make_server
from b_hydra.node import BHydraNode
from b_hydra.p2p import P2PNode, local_ip, parse_seeds

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_JS = os.path.join(ROOT, "bhydra-net.js")
NODE = shutil.which("node")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_until(check, timeout=5.0):
    """Распространение по сети асинхронное — ждём результат, а не спим наугад."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


@pytest.fixture
def pair():
    """Два узла сети, у каждого свой REST-порт (как два компьютера)."""
    a = P2PNode("127.0.0.1", _free_port(), node=BHydraNode(difficulty=1),
                api_port=_free_port())
    b = P2PNode("127.0.0.1", _free_port(), node=BHydraNode(difficulty=1),
                api_port=_free_port())
    a.start()
    b.start()
    try:
        yield a, b
    finally:
        a.stop()
        b.stop()


# --- Узел объявляет свой REST-адрес -------------------------------------------
def test_hello_carries_the_rest_address(pair):
    """Адрес API уходит вместе с приветствием — другого случая его назвать нет."""
    a, _b = pair
    assert a._hello_message()["api"] == a.api_port
    assert a._hello_message()["api_tls"] is False
    # Узел без REST не объявляет ничего: пустое поле лучше выдуманного порта.
    quiet = P2PNode("127.0.0.1", 1, node=a.node)
    assert "api" not in quiet._hello_message()


def test_peers_learn_each_other_rest_addresses(pair):
    """После знакомства ОБА знают, где у другого кошелёк."""
    a, b = pair
    assert b.connect("127.0.0.1", a.port) is True
    assert _wait_until(lambda: len(a.api_nodes()) == 2)

    assert f"http://127.0.0.1:{b.api_port}" in a.api_nodes()
    # И в обратную сторону: инициатор знакомства тоже узнал адрес.
    assert f"http://127.0.0.1:{a.api_port}" in b.api_nodes()


def test_seed_bootstrap_also_learns_rest_addresses():
    """Вход в сеть по seed обязан давать кошельку то же, что и `connect`.

    У `bootstrap` свой путь — он здоровается со всеми сразу, — и без этого
    узел, поднятый ровно так, как задумано (`--seed`), знал бы адрес кошелька
    только своего соседа-инициатора, но не свой список.
    """
    a = P2PNode("127.0.0.1", _free_port(), node=BHydraNode(difficulty=1),
                api_port=_free_port())
    a.start()
    b = P2PNode("127.0.0.1", _free_port(), node=BHydraNode(difficulty=1),
                api_port=_free_port(), seeds=[("127.0.0.1", a.port)])
    b.start()
    try:
        assert b.bootstrap() == 1
        assert f"http://127.0.0.1:{a.api_port}" in b.api_nodes()
    finally:
        a.stop()
        b.stop()


def test_own_address_for_wallets_is_never_loopback():
    """Узел не должен звать телефоны на 127.0.0.1 — там сам телефон.

    В GUI это значение по умолчанию в поле «Хост», так что случай самый
    обычный, а не экзотика.
    """
    node = P2PNode("127.0.0.1", 5000, node=BHydraNode(difficulty=1),
                   api_port=8000)
    own = node.api_nodes()[0]
    assert own.endswith(":8000")
    assert "127.0.0.1" not in own and "localhost" not in own


def test_api_address_is_taken_from_the_connection_not_the_message(pair):
    """Хост берётся у ПИРА, а не из тела сообщения.

    Иначе любой узел объявлял бы REST на чужом адресе и уводил кошельки куда
    угодно — адрес в теле сообщения ничем не подтверждён.
    """
    a, _b = pair
    a.add_peer("10.1.2.3", 5000)
    a.remember_api("10.1.2.3", 5000,
                   {"api": 8000, "host": "зловред.example", "api_tls": False})
    assert "http://10.1.2.3:8000" in a.api_nodes()
    assert not any("зловред" in url for url in a.api_nodes())


@pytest.mark.parametrize("value", [0, -1, 70000, "восемь", None, {"a": 1}])
def test_nonsense_api_port_is_refused(pair, value):
    a, _b = pair
    a.add_peer("10.1.2.3", 5000)
    assert a.remember_api("10.1.2.3", 5000, {"api": value}) is False
    assert a.api_nodes(include_self=False) == []


def test_addresses_of_forgotten_peers_are_not_offered(pair):
    """Соседа выкинули из таблицы — его адрес кошелькам больше не отдаём."""
    a, _b = pair
    a.add_peer("10.1.2.3", 5000)
    a.remember_api("10.1.2.3", 5000, {"api": 8000})
    assert "http://10.1.2.3:8000" in a.api_nodes()
    a.remove_peer("10.1.2.3", 5000)
    assert "http://10.1.2.3:8000" not in a.api_nodes()


def test_peer_api_about_strangers_is_ignored(pair):
    """В `peer_api` слушаем только про тех, кто уже наш сосед.

    Без этого один узел раздавал бы кошелькам произвольные адреса — тот же
    механизм отравления, от которого закрыт список пиров.
    """
    a, _b = pair
    a.add_peer("10.1.2.3", 5000)
    a._absorb_peer_api({"peer_api": {"10.1.2.3:5000": [8000, False],
                                     "10.9.9.9:5000": [8000, False]}})
    assert "http://10.1.2.3:8000" in a.api_nodes()
    assert not any("10.9.9.9" in url for url in a.api_nodes())


def test_tls_nodes_are_offered_over_https(pair):
    a, _b = pair
    a.add_peer("10.1.2.3", 5000)
    a.remember_api("10.1.2.3", 5000, {"api": 8443, "api_tls": True})
    assert "https://10.1.2.3:8443" in a.api_nodes()


def test_rest_addresses_survive_a_restart(tmp_path, pair):
    """Иначе после перезапуска кошельки снова знали бы один узел — вписанный
    руками."""
    a, _b = pair
    path = str(tmp_path / "peers.json")
    a.add_peer("10.1.2.3", 5000)
    a.remember_api("10.1.2.3", 5000, {"api": 8000})
    assert a.save_peers(path) is True

    fresh = P2PNode("127.0.0.1", _free_port(), node=BHydraNode(difficulty=1),
                    peers_file=path)
    fresh.load_peers()
    assert "http://10.1.2.3:8000" in fresh.api_nodes()


# --- REST: /api/nodes ---------------------------------------------------------
def _serve(port, p2p=None, state=None, host="127.0.0.1"):
    server = make_server(host, port, state, p2p=p2p)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_api_nodes_lists_the_network(pair):
    """Кошелёк спрашивает ОДИН узел и получает адреса остальных."""
    a, b = pair
    server = _serve(a.api_port, p2p=a)
    try:
        assert b.connect("127.0.0.1", a.port) is True
        assert _wait_until(lambda: len(a.api_nodes()) == 2)
        body = _get(f"http://127.0.0.1:{a.api_port}/api/nodes")
        assert f"http://127.0.0.1:{b.api_port}" in body["nodes"]
        assert body["peers"] == 1
        assert body["p2p"] == f"127.0.0.1:{a.port}"
        # Отпечаток сети обязателен: по нему клиент отличит свою цепочку.
        assert body["genesis"] == a.node.blockchain.chain[0].hash
    finally:
        server.shutdown()


def test_api_nodes_on_a_lonely_server(tmp_path):
    """Сервер без P2P отвечает честно: узлов нет, соседей неизвестно.

    Пустой список здесь — не ошибка, а факт: такой узел живёт сам по себе,
    и кошельку важно это видеть, а не гадать.
    """
    port = _free_port()
    server = _serve(port, state=str(tmp_path / "chain.json"))
    try:
        body = _get(f"http://127.0.0.1:{port}/api/nodes")
        assert body["nodes"] == [] and body["peers"] is None
        assert body["p2p"] is None
        assert body["genesis"]
    finally:
        server.shutdown()


def test_rest_server_shares_the_chain_of_the_network_node(pair):
    """REST обязан отвечать про цепочку СЕТЕВОГО узла, а не про свою копию.

    Иначе телефон видел бы одну цепочку, а компьютеры — другую, при том что
    процесс один и тот же.
    """
    a, _b = pair
    server = _serve(a.api_port, p2p=a)
    try:
        a.node.mine_pending("BHYtest")
        body = _get(f"http://127.0.0.1:{a.api_port}/api/info")
        assert body["height"] == len(a.node.blockchain.chain)
        assert body["height"] > 1
    finally:
        server.shutdown()


# --- Разбор seed-адресов ------------------------------------------------------
def test_parse_seeds_keeps_only_valid_pairs():
    assert parse_seeds(["10.0.0.1:5000", "", "мусор", "host:порт",
                        " 10.0.0.2:5001 "]) == [("10.0.0.1", 5000),
                                                ("10.0.0.2", 5001)]


def test_local_ip_is_not_loopback_when_there_is_a_network():
    """Узел, представившийся 127.0.0.1, для сети бесполезен: соседи не смогут
    подключиться обратно, а телефон не откроет кошелёк."""
    address = local_ip()
    assert address and address.count(".") == 3


# --- Клиентская часть: bhydra-net.js -----------------------------------------
def _run_js(tmp_path, script):
    path = tmp_path / "case.js"
    path.write_text(f'const BHydraNet = require({json.dumps(NET_JS)});\n' + script,
                    encoding="utf-8")
    result = subprocess.run([NODE, str(path)], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


FAKE = """
function fake(table){
  return async (url) => {
    for (const key of Object.keys(table)) {
      if (url.startsWith(key)) return table[key](url);
    }
    throw new Error("нет узла " + url);
  };
}
"""


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_wallet_learns_other_nodes_from_the_first_one(tmp_path):
    """Один адрес на входе — весь список на выходе."""
    result = _run_js(tmp_path, FAKE + """
    const net = new BHydraNet.Network({
      nodes: ["http://10.0.0.1:8000"], genesis: "G",
      fetchJson: fake({
        "http://10.0.0.1:8000/api/nodes": async () => ({ok: true, status: 200,
          body: {nodes: ["http://10.0.0.1:8000", "http://10.0.0.2:8000"],
                 genesis: "G"}}),
      })});
    net.discover().then((added) =>
      console.log(JSON.stringify({added, nodes: net.nodes})));
    """)
    assert result["added"] == 1
    assert result["nodes"] == ["http://10.0.0.1:8000", "http://10.0.0.2:8000"]


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_wallet_refuses_nodes_from_a_foreign_network(tmp_path):
    """Иначе одного подставного адреса хватило бы, чтобы увести кошелёк
    в чужую цепочку — и «баланс» там был бы чужой."""
    result = _run_js(tmp_path, FAKE + """
    const net = new BHydraNet.Network({
      nodes: ["http://10.0.0.1:8000"], genesis: "G",
      fetchJson: fake({
        "http://10.0.0.1:8000/api/nodes": async () => ({ok: true, status: 200,
          body: {nodes: ["http://evil:8000"], genesis: "ДРУГОЙ"}}),
      })});
    net.discover().then((added) =>
      console.log(JSON.stringify({added, nodes: net.nodes})));
    """)
    assert result["added"] == 0
    assert result["nodes"] == ["http://10.0.0.1:8000"]


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_wallet_survives_a_node_without_the_endpoint(tmp_path):
    """Узел постарше `/api/nodes` не знает. Это не ошибка — просто нечего взять,
    и кошелёк обязан продолжить работать со старым узлом как раньше."""
    result = _run_js(tmp_path, FAKE + """
    const net = new BHydraNet.Network({
      nodes: ["http://10.0.0.1:8000", "http://10.0.0.2:8000"], genesis: "G",
      fetchJson: fake({
        "http://10.0.0.1:8000/api/nodes": async () => ({ok: false, status: 404,
                                                        body: {}}),
        "http://10.0.0.2:8000/api/nodes": async () => ({ok: true, status: 200,
          body: {nodes: ["http://10.0.0.3:8000"], genesis: "G"}}),
      })});
    net.discover().then((added) =>
      console.log(JSON.stringify({added, nodes: net.nodes})));
    """)
    assert result["added"] == 1
    assert "http://10.0.0.3:8000" in result["nodes"]


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_discovery_has_a_ceiling(tmp_path):
    """Список узлов не должен раздуваться без предела: адреса ничего не стоят,
    а каждый лишний — это ещё один запрос при каждом обновлении."""
    result = _run_js(tmp_path, FAKE + """
    const many = [];
    for (let i = 0; i < 50; i++) many.push("http://10.0.0." + i + ":8000");
    const net = new BHydraNet.Network({
      nodes: ["http://10.0.0.1:8000"], genesis: "G",
      fetchJson: fake({
        "http://10.0.0.1:8000/api/nodes": async () => ({ok: true, status: 200,
          body: {nodes: many, genesis: "G"}}),
      })});
    net.discover({limit: 8}).then(() =>
      console.log(JSON.stringify({count: net.nodes.length})));
    """)
    assert result["count"] <= 8


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_wallet_discovers_two_real_nodes(tmp_path, pair):
    """Сквозной путь: настоящие узлы, настоящий REST, настоящий клиент."""
    a, b = pair
    server = _serve(a.api_port, p2p=a)
    try:
        assert b.connect("127.0.0.1", a.port) is True
        assert _wait_until(lambda: len(a.api_nodes()) == 2)
        result = _run_js(tmp_path, f"""
        const net = new BHydraNet.Network({{
          nodes: ["http://127.0.0.1:{a.api_port}"],
          genesis: {json.dumps(a.node.blockchain.chain[0].hash)}}});
        net.discover().then((added) =>
          console.log(JSON.stringify({{added, nodes: net.nodes}})));
        """)
        # Узел A назвал и себя (по адресу в сети — тому, что годится телефону),
        # и соседа B.
        assert result["added"] == 2
        assert f"http://127.0.0.1:{b.api_port}" in result["nodes"]
    finally:
        server.shutdown()


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_one_node_under_two_addresses_counts_once(tmp_path, pair):
    """`localhost` и адрес в сети — ОДИН узел, а не два независимых.

    Кошелёк, открытый на самом компьютере, знает узел как `127.0.0.1`, а через
    /api/nodes получает его же по адресу в сети. Посчитать это за два
    подтверждения — соврать в самую опасную сторону: «подтвердили двое», когда
    узел один, а на числе независимых узлов держится SPV.
    """
    a, _b = pair
    # На всех интерфейсах: оба адреса обязаны вести в ОДИН И ТОТ ЖЕ узел, иначе
    # проверялась бы недоступность второго, а не распознавание дубля.
    server = _serve(a.api_port, p2p=a, host="0.0.0.0")
    try:
        own = a.api_nodes()[0]              # свой адрес в сети (не loopback)
        result = _run_js(tmp_path, f"""
        const net = new BHydraNet.Network({{nodes: [
          "http://127.0.0.1:{a.api_port}", {json.dumps(own)}]}});
        net.survey().then((report) => console.log(JSON.stringify({{
          reachable: report.reachable, total: report.total,
          order: net.order().length}})));
        """)
        assert result["total"] == 2         # адреса разные — оба в списке
        assert result["reachable"] == 1     # а узел за ними один
        assert result["order"] == 1
    finally:
        server.shutdown()
