"""Сверка браузерной подписи (bhydra-sign.js) с Python — байт-в-байт.

Подпись и txid считаются от json.dumps(..., sort_keys=True), а Python и JS
пишут числа по-разному ("10.0" против "10", "1e-08" против "1e-8"). Поэтому
одной проверки «подпись валидна» мало: она прошла бы и при неверно выведенном
нонсе. Здесь тот же корпус данных считается обеими реализациями и результаты
сравниваются посимвольно — благо RFC 6979 сделал подпись воспроизводимой.

Именно так и нашлась настоящая ошибка: в JS `(256 + 7) / 8` даёт 32.875, а не
32, а дробная длина Uint8Array молча превращает запись в никуда — материал для
HMAC оставался нулевым, и k выходил ОДИНАКОВЫМ для всех сообщений (мгновенная
утечка приватного ключа). Подписи при этом были совершенно валидными.

Тесты пропускаются, если в системе нет node.
"""

import json
import os
import random
import shutil
import struct
import subprocess

import pytest

from b_hydra import hashing
from b_hydra.transaction import Transaction, TxInput, TxOutput
from b_hydra.wallet import Wallet, _b58encode, _ripemd160, generate_wallet

_BRIDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "js_bridge.js")
_NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    _NODE is None, reason="нужен node для сверки браузерной подписи")


def _js(op, cases):
    """Считает op на JS для набора cases и возвращает разобранный ответ."""
    result = subprocess.run(
        [_NODE, _BRIDGE],
        input=json.dumps({"op": op, "cases": cases}).encode("utf-8"),
        capture_output=True, timeout=300)
    if result.returncode != 0:
        pytest.fail("js_bridge упал:\n" + result.stderr.decode("utf-8", "replace"))
    return json.loads(result.stdout.decode("utf-8"))


def _random_transaction(rnd):
    """Транзакция со случайными входами/выходами в домене проекта."""
    return {
        "vin": [{"txid": "%064x%064x" % (rnd.getrandbits(256), rnd.getrandbits(256)),
                 "index": rnd.randrange(0, 5)}
                for _ in range(rnd.randint(1, 3))],
        "vout": [{"address": generate_wallet().address,
                  "amount": round(rnd.uniform(0.00000001, 1000), 8)}
                 for _ in range(rnd.randint(1, 3))],
        "timestamp": round(rnd.uniform(1e9, 2e9), 7),
    }


# --- Примитивы ---------------------------------------------------------------
def test_hash_primitives_match_python():
    """SHA-512, RIPEMD-160, HMAC и base58 совпадают на границах паддинга."""
    rnd = random.Random(1)
    lengths = [0, 1, 55, 56, 57, 63, 64, 65, 111, 112, 113, 127, 128, 129, 200]
    cases = [{"hex": bytes(rnd.randrange(256) for _ in range(n)).hex(),
              "key": bytes(rnd.randrange(256) for _ in range(n % 150 + 1)).hex()}
             for n in lengths]

    import hashlib
    import hmac as ref_hmac
    for case, got in zip(cases, _js("primitives", cases)):
        data, key = bytes.fromhex(case["hex"]), bytes.fromhex(case["key"])
        assert got["sha512"] == hashing.sha512_bytes(data).hex()
        assert got["double_sha512"] == hashing.double_sha512(data).hex()
        assert got["ripemd160"] == _ripemd160(data).hex()
        assert got["hmac_sha512"] == ref_hmac.new(key, data, hashlib.sha512).hexdigest()
        assert got["base58"] == _b58encode(data)


def test_float_format_matches_python_repr():
    """Формат чисел совпадает с repr() — иначе не сойдётся хеш payload."""
    rnd = random.Random(2)
    values = [10.0, 39.5, 0.5, 0.00000001, 1e-08, 1e-05, 1e-04, 0.0, 1.0,
              31000000.0, 1e15, 1e16, 1e17, 1785009903.5018888, 1785009903.0]
    values += [round(rnd.uniform(0, 31_000_000), 8) for _ in range(400)]
    # Случайные double за пределами домена — ловим границы перехода к экспоненте.
    while len(values) < 700:
        candidate = struct.unpack("<d", struct.pack("<Q", rnd.getrandbits(64)))[0]
        if candidate == candidate and abs(candidate) != float("inf"):
            values.append(candidate)

    for value, got in zip(values, _js("floats", values)):
        assert got == repr(value), f"{value!r}: Python {repr(value)}, JS {got}"


# --- Кошелёк и подпись -------------------------------------------------------
def test_wallet_derivation_matches_python():
    """Публичный ключ и адрес выводятся из ключа так же, как в Python."""
    wallets = [generate_wallet() for _ in range(5)]
    cases = [{"private_key": w.private_key_hex} for w in wallets]
    for wallet, got in zip(wallets, _js("wallet", cases)):
        assert got["public_key"] == wallet.public_key_hex
        assert got["address"] == wallet.address


def test_payload_txid_and_signature_match_python():
    """Главная сверка: payload, txid и сама подпись совпадают посимвольно."""
    rnd = random.Random(3)
    wallets = [generate_wallet() for _ in range(6)]
    cases = []
    for wallet in wallets:
        case = _random_transaction(rnd)
        case["private_key"] = wallet.private_key_hex
        cases.append(case)

    for case, wallet, got in zip(cases, wallets, _js("sign", cases)):
        tx = Transaction(
            vin=[TxInput(i["txid"], i["index"]) for i in case["vin"]],
            vout=[TxOutput(o["amount"], o["address"]) for o in case["vout"]],
            timestamp=case["timestamp"])
        payload = tx.signing_payload()
        assert got["payload"] == payload.decode("utf-8")
        assert got["txid"] == tx.txid
        assert got["signature"] == wallet.sign(payload)
        assert Wallet.verify(wallet.public_key_hex, payload, got["signature"])


def test_browser_nonces_differ_between_transactions():
    """Регрессия: нонс обязан зависеть от сообщения.

    При ошибке с дробной длиной материал для HMAC оставался нулевым и k был
    один и тот же для всех транзакций — подписи проходили проверку, но два
    разных сообщения с общим k раскрывают приватный ключ.
    """
    rnd = random.Random(4)
    wallet = generate_wallet()
    cases = []
    for _ in range(5):
        case = _random_transaction(rnd)
        case["private_key"] = wallet.private_key_hex
        cases.append(case)
    # r — x-координата k·G, поэтому разные r означают разные k.
    r_values = {got["signature"][:64] for got in _js("sign", cases)}
    assert len(r_values) == len(cases)


# --- Сборка транзакции целиком на устройстве ---------------------------------
def test_browser_built_transaction_is_accepted_by_node():
    """Транзакция, собранная и подписанная в браузере, проходит на узле.

    Это и есть смысл всей работы: приватный ключ остаётся на устройстве, узел
    получает только готовую подписанную транзакцию.
    """
    from b_hydra.node import BHydraNode

    node = BHydraNode(difficulty=1)
    alice, bob = generate_wallet(), generate_wallet()
    node.mine_pending(alice.address)          # даём Алисе монеты

    utxos = [{"txid": txid, "index": index, "amount": data["amount"]}
             for (txid, index), data in node.utxo_set().items()
             if data["address"] == alice.address]

    built = _js("build", [{
        "privateKey": alice.private_key_hex,
        "to": bob.address,
        "amount": 10,
        "fee": 0.5,
        "utxos": utxos,
    }])[0]

    # Узел принимает её как обычную транзакцию — никаких поблажек.
    assert node.add_transaction(Transaction.from_dict(built)), built
    node.mine_pending(alice.address)
    assert node.get_balance(bob.address) == 10.0
    assert node.is_valid()


def test_browser_transaction_txid_survives_json_roundtrip():
    """txid не меняется после JSON-передачи на сервер.

    Тонкость: JS отправляет 10.0 как «10», и Python получил бы int — тогда
    payload сериализовался бы иначе, чем его подписали. Транзакция приводит
    суммы и метку времени к float, поэтому txid устойчив.
    """
    alice, bob = generate_wallet(), generate_wallet()
    built = _js("build", [{
        "privateKey": alice.private_key_hex,
        "to": bob.address,
        "amount": 10,
        "fee": 0,
        "utxos": [{"txid": "aa" * 64, "index": 0, "amount": 50}],
    }])[0]

    # Ровно то, что уходит по сети: сериализация и разбор на стороне узла.
    restored = Transaction.from_dict(json.loads(json.dumps(built)))
    assert restored.txid == built["txid"]
    assert Wallet.verify(
        built["vin"][0]["public_key"], restored.signing_payload(),
        built["vin"][0]["signature"])


def test_integral_timestamp_still_verifies():
    """Целая метка времени не ломает подпись.

    Date.now() / 1000 попадает на целое примерно раз на тысячу переводов, и
    тогда JS отправляет «1785009903» вместо «1785009903.0». Пока Transaction
    не приводил метку к float, узел собирал другой payload и отвергал перевод.
    """
    alice, bob = generate_wallet(), generate_wallet()
    built = _js("build", [{
        "privateKey": alice.private_key_hex,
        "to": bob.address,
        "amount": 10,
        "fee": 0,
        "utxos": [{"txid": "aa" * 64, "index": 0, "amount": 50}],
        "timestamp": 1785009903,          # ровно целое, без дробной части
    }])[0]
    assert json.dumps(built["timestamp"]) == "1785009903"   # так уходит по сети

    restored = Transaction.from_dict(json.loads(json.dumps(built)))
    assert isinstance(restored.timestamp, float)
    assert restored.txid == built["txid"]
    assert Wallet.verify(
        built["vin"][0]["public_key"], restored.signing_payload(),
        built["vin"][0]["signature"])
