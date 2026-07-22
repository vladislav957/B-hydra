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
    assert "B-hydra" in html
    assert "<html" in html.lower()


def test_block_endpoint(server):
    w = MobileWallet(server)
    _post(server, "/api/mine", {"miner": w.address})
    block = json.loads(_get(server, "/api/block/1"))
    assert block["index"] == 1
    # PoW выполнен: хеш (как число) не больше target.
    assert int(block["hash"], 16) <= int(block["target"], 16)


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


def test_contract_cheque_flow_over_api(server):
    payer = MobileWallet(server)
    holder = MobileWallet(server)
    _post(server, "/api/mine", {"miner": payer.address})

    cheque = _post(server, "/api/contract/cheque", {
        "private_key": payer.private_key_hex, "amount": 7, "fee": 0.5,
    })
    assert cheque["status"] == "active"
    assert cheque["secret"]                      # секрет выдан один раз

    # Неверный секрет — понятная ошибка, деньги не уходят.
    err = _post(server, "/api/contract/cheque/cash", {
        "cheque_id": cheque["cheque_id"], "secret": "xxx",
        "to": holder.address,
    })
    assert "секрет" in err["error"]

    cashed = _post(server, "/api/contract/cheque/cash", {
        "cheque_id": cheque["cheque_id"], "secret": cheque["secret"],
        "to": holder.address,
    })
    assert cashed["status"] == "cashed"
    _post(server, "/api/mine", {"miner": MobileWallet(server).address})
    assert holder.balance() == 7

    # Хеш секрета публичен, сам секрет узел не хранит.
    info = json.loads(_get(server, "/api/contract"))
    assert info["cheques"][0]["cheque_id"] == cheque["cheque_id"]
    assert "secret" not in info["cheques"][0]


def test_contract_escrow_flow_over_api(server):
    buyer = MobileWallet(server)
    seller = MobileWallet(server)
    _post(server, "/api/mine", {"miner": buyer.address})

    escrow = _post(server, "/api/contract/escrow", {
        "private_key": buyer.private_key_hex, "seller": seller.address,
        "amount": 10,
    })
    assert escrow["status"] == "open"

    for key in (buyer.private_key_hex, seller.private_key_hex):
        escrow = _post(server, "/api/contract/escrow/confirm", {
            "escrow_id": escrow["escrow_id"], "private_key": key,
        })
    assert escrow["status"] == "completed"
    _post(server, "/api/mine", {"miner": MobileWallet(server).address})
    assert seller.balance() == 10

    got = json.loads(_get(server, "/api/contract/escrow/"
                          + escrow["escrow_id"]))
    assert got["status"] == "completed"


def test_addresses_endpoint(server):
    w = MobileWallet(server)
    _post(server, "/api/mine", {"miner": w.address})
    d = json.loads(_get(server, "/api/addresses?limit=5"))
    assert d["count"] == 1
    assert d["total_supply"] == 50.0
    top = d["addresses"][0]
    assert top["address"] == w.address
    assert top["balance"] == 50.0
    assert top["tx_count"] == 1


def test_merkle_proof_endpoint(server):
    from b_hydra.merkle import verify_proof
    alice = MobileWallet(server)
    bob = MobileWallet(server)
    _post(server, "/api/mine", {"miner": alice.address})
    alice.send(bob.address, 10, fee=0.5)
    _post(server, "/api/mine", {"miner": bob.address})

    block2 = json.loads(_get(server, "/api/block/2"))
    txid = block2["data"][1]["txid"]
    pr = json.loads(_get(server, "/api/proof/" + txid))
    assert pr["block_index"] == 2
    assert pr["merkle_root"] == block2["merkle_root"]
    # SPV-проверка на стороне клиента по audit-пути.
    assert verify_proof(bytes.fromhex(pr["leaf"]), pr["proof"], pr["merkle_root"])

    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server, "/api/proof/" + "00" * 64)
    assert exc.value.code == 404


def test_dashboard_html_served(server):
    html = _get(server, "/dashboard")
    assert "B-HYDRA" in html or "B·HYDRA" in html
    assert "NETWORK ACTIVITY" in html
    assert "<html" in html.lower()
