"""Тесты REST API и подписи на устройстве."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from b_hydra.api import make_server
from b_hydra.mobile_client import MobileWallet


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(url, path, body):
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read())


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    # Низкая сложность: майнинг быстрый даже на чистом Python SHA.
    srv = make_server("127.0.0.1", port, str(tmp_path / "state.json"),
                      difficulty=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def test_info_endpoint(server):
    info = MobileWallet(server).info()
    assert info["network"] == "B-hydra"
    assert info["model"] == "UTXO"
    assert info["hash_algorithm"] == "SHA-512"


def test_mine_and_balance(server):
    w = MobileWallet(server)
    _post(server, "/api/mine", {"miner": w.address})
    assert w.balance() == 50.0


def test_mine_requires_miner(server):
    assert "error" in _post(server, "/api/mine", {})


def test_private_key_never_leaves_device(server):
    alice = MobileWallet(server)
    bob = MobileWallet(server)
    _post(server, "/api/mine", {"miner": alice.address})

    captured = {}
    original_post = alice._post

    def spy(path, payload):
        if path == "/api/transaction":
            captured["payload"] = payload
        return original_post(path, payload)

    alice._post = spy
    result = alice.send(bob.address, 12, fee=0.5)

    assert result["accepted"]
    blob = json.dumps(captured["payload"])
    # Главное: приватный ключ НЕ уходит на сервер.
    assert alice.private_key_hex not in blob
    # Но публичный ключ и подпись во входах присутствуют.
    assert all(i["public_key"] and i["signature"]
               for i in captured["payload"]["vin"])

    _post(server, "/api/mine", {"miner": bob.address})
    assert bob.balance() == 62.5
    assert alice.balance() == 37.5


def test_utxos_endpoint(server):
    w = MobileWallet(server)
    _post(server, "/api/mine", {"miner": w.address})
    utxos = w.utxos()
    assert len(utxos) == 1
    assert utxos[0]["amount"] == 50.0


def test_mine_rejects_invalid_address(server):
    res = _post(server, "/api/mine", {"miner": "<script>alert(1)</script>"})
    assert "error" in res          # узел не принимает адрес с инъекцией


def _get(url, path):
    return urllib.request.urlopen(url + path, timeout=5).read().decode("utf-8")


def test_explorer_html_served(server):
    html = _get(server, "/")
    assert "обозреватель блоков" in html
    assert "<html" in html.lower()


def test_block_endpoint(server):
    w = MobileWallet(server)
    _post(server, "/api/mine", {"miner": w.address})
    block = json.loads(_get(server, "/api/block/1"))
    assert block["index"] == 1
    assert block["hash"].startswith("0" * block["difficulty"])  # PoW выполнен


def test_block_not_found(server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/block/999")
    assert exc.value.code == 404


def test_tx_and_address_endpoints(server):
    alice = MobileWallet(server)
    bob = MobileWallet(server)
    _post(server, "/api/mine", {"miner": alice.address})
    alice.send(bob.address, 10, fee=0.5)
    _post(server, "/api/mine", {"miner": bob.address})

    block2 = json.loads(_get(server, "/api/block/2"))
    txid = block2["data"][1]["txid"]
    found = json.loads(_get(server, "/api/tx/" + txid))
    assert found["block_index"] == 2

    addr = json.loads(_get(server, "/api/address/" + alice.address))
    assert addr["balance"] == 39.5
    assert len(addr["history"]) == 2
