/*
 * js_bridge.js — мост для сверки браузерной подписи с Python.
 *
 * Читает со stdin JSON вида {"op": "...", "cases": [...]} и печатает результат
 * JSON-ом. Используется из tests/test_browser_signing.py: тот же самый корпус
 * данных считается на JS и на Python, результаты сравниваются байт-в-байт.
 */
"use strict";

const path = require("path");
const B = require(path.join(__dirname, "..", "bhydra-sign.js"));

const fromHex = (hex) => Uint8Array.from(Buffer.from(hex, "hex"));

const OPS = {
  // Хеши и кодирование — примитивы, на которых стоит всё остальное.
  primitives: (cases) => cases.map((c) => ({
    sha512: B.toHex(B.sha512Bytes(fromHex(c.hex))),
    double_sha512: B.toHex(B.doubleSha512(fromHex(c.hex))),
    ripemd160: B.toHex(B.ripemd160(fromHex(c.hex))),
    hmac_sha512: B.toHex(B.hmacSha512(fromHex(c.key), fromHex(c.hex))),
    base58: B.base58Encode(fromHex(c.hex)),
  })),

  // Формат чисел — самое хрупкое место совместимости с json.dumps.
  floats: (cases) => cases.map((v) => B.pythonFloatRepr(v)),

  // Кошелёк из приватного ключа: публичный ключ и адрес.
  wallet: (cases) => cases.map((c) => {
    const w = B.walletFromPrivateKey(c.private_key);
    return { public_key: w.publicKey, address: w.address };
  }),

  // Полный конвейер подписи транзакции.
  sign: (cases) => cases.map((c) => {
    const tx = { vin: c.vin, vout: c.vout, timestamp: c.timestamp };
    const payload = B.canonicalPayload(tx);
    return {
      payload,
      txid: B.txid(tx),
      signature: B.signPayload(c.private_key, payload),
    };
  }),

  // Сборка транзакции целиком на устройстве (как это делает кошелёк).
  build: (cases) => cases.map((c) => B.buildSignedTransaction(c)),
};

let raw = "";
process.stdin.on("data", (chunk) => (raw += chunk));
process.stdin.on("end", () => {
  const request = JSON.parse(raw);
  const op = OPS[request.op];
  if (!op) throw new Error("неизвестная операция: " + request.op);
  process.stdout.write(JSON.stringify(op(request.cases)));
});
