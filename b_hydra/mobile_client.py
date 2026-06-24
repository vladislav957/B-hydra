"""
mobile_client.py — эталонный мобильный кошелёк B-hydra (подпись на устройстве).

Демонстрирует правильную модель безопасности: приватный ключ НИКОГДА не уходит
на сервер. Клиент сам строит транзакцию из UTXO, подписывает её локально и
отправляет на узел уже подписанную (с публичным ключом и подписью, но без
приватного ключа).

Это reference-реализация на Python. На телефоне ту же логику повторяют на
Kotlin/Swift/JS (см. схему подписи в API.md): ECDSA secp256k1 + SHA-512.

Пример:
    from b_hydra.mobile_client import MobileWallet
    w = MobileWallet("http://192.168.0.10:8000")   # адрес узла
    print(w.address, w.balance())
    w.send("BHY<получатель>", 10, fee=0.5)
"""

import json
import urllib.request

if __name__ == "__main__" and __package__ in (None, ""):
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "b_hydra"

from .wallet import Wallet
from .transaction import Transaction, TxInput, TxOutput


class MobileWallet:
    """Кошелёк, который держит ключ у себя и подписывает транзакции локально."""

    def __init__(self, api_url, private_key_hex=None):
        self.api = api_url.rstrip("/")
        # Ключ создаётся/восстанавливается на устройстве и остаётся на нём.
        self.wallet = (Wallet.from_private_hex(private_key_hex)
                       if private_key_hex else Wallet())

    # --- Идентификация (вычисляется локально) ----------------------------
    @property
    def address(self):
        return self.wallet.address

    @property
    def private_key_hex(self):
        """Хранить надёжно! На сервер НЕ отправляется."""
        return self.wallet.private_key_hex

    # --- Доступ к API ----------------------------------------------------
    def _get(self, path):
        with urllib.request.urlopen(f"{self.api}{path}", timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return json.loads(exc.read().decode("utf-8"))

    # --- Операции --------------------------------------------------------
    def info(self):
        return self._get("/api/info")

    def balance(self):
        return self._get(f"/api/balance/{self.address}")["balance"]

    def utxos(self):
        return self._get(f"/api/utxos/{self.address}")["utxos"]

    def send(self, recipient, amount, fee=0.0):
        """
        Строит и ПОДПИСЫВАЕТ транзакцию ЛОКАЛЬНО, затем отправляет на узел.

        Возвращает ответ сервера {"accepted": bool, "txid": str}.
        """
        need = amount + fee
        chosen, gathered = [], 0.0
        for utxo in self.utxos():
            chosen.append(utxo)
            gathered += utxo["amount"]
            if gathered >= need:
                break
        if gathered < need:
            return {"accepted": False, "error": "insufficient funds"}

        vin = [TxInput(u["txid"], u["index"]) for u in chosen]
        vout = [TxOutput(amount, recipient)]
        change = gathered - need
        if change > 0:
            vout.append(TxOutput(change, self.address))  # сдача себе

        tx = Transaction(vin=vin, vout=vout)
        tx.sign(self.wallet)            # ← ПОДПИСЬ НА УСТРОЙСТВЕ (ключ не уходит)

        # На сервер уходит tx.to_dict(): vin (с public_key + signature) и vout.
        # Приватного ключа в этом словаре НЕТ.
        return self._post("/api/transaction", tx.to_dict())


def main(argv: "list[str] | None" = None) -> None:
    import sys
    args = argv if argv is not None else sys.argv[1:]
    api_url = args[0] if args else "http://127.0.0.1:8000"
    wallet = MobileWallet(api_url)
    print("Адрес      :", wallet.address)
    print("Приватный  :", wallet.private_key_hex, "(хранится только на устройстве)")
    print("Сеть       :", wallet.info())
    print("Баланс     :", wallet.balance(), "BHY")


if __name__ == "__main__":
    main()
