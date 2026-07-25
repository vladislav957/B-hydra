/*
 * bhydra-sign.js — подпись транзакций B-hydra ПРЯМО В БРАУЗЕРЕ.
 *
 * Зачем: страница кошелька раньше отправляла приватный ключ на сервер
 * (POST /api/send). Для своего узла это терпимо, для чужого — нет. Здесь всё
 * нужное для подписи на устройстве: ключ не покидает браузер, наружу уходит
 * только готовая подписанная транзакция (POST /api/transaction).
 *
 * Главная сложность — не криптография, а СЕРИАЛИЗАЦИЯ. Подпись и txid берутся
 * от json.dumps(..., sort_keys=True, ensure_ascii=False), а Python и JS пишут
 * числа по-разному: Python — "10.0" и "1e-08", JS — "10" и "1e-8". Промахнись
 * на один символ, и хеш не сойдётся. Поэтому pythonFloatRepr ниже повторяет
 * правила repr() из CPython, а тест tests/test_browser_signing.py сверяет
 * результат с настоящим Python байт-в-байт на фаззинге.
 *
 * Зависимостей нет: SHA-512, HMAC, RIPEMD-160, base58 и secp256k1 — свои, как
 * и весь остальной проект. Работает и в браузере (window.BHydra), и в Node.
 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;               // Node — для сверки с Python в тестах
  } else {
    root.BHydra = api;                  // браузер
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ==========================================================================
  // SHA-512
  // ==========================================================================
  const MASK64 = (1n << 64n) - 1n;

  // Раундовые константы — первые 64 бита дробных частей кубических корней
  // первых 80 простых; начальное состояние — то же от квадратных корней.
  const K512 = [
    0x428a2f98d728ae22n, 0x7137449123ef65cdn, 0xb5c0fbcfec4d3b2fn, 0xe9b5dba58189dbbcn,
    0x3956c25bf348b538n, 0x59f111f1b605d019n, 0x923f82a4af194f9bn, 0xab1c5ed5da6d8118n,
    0xd807aa98a3030242n, 0x12835b0145706fben, 0x243185be4ee4b28cn, 0x550c7dc3d5ffb4e2n,
    0x72be5d74f27b896fn, 0x80deb1fe3b1696b1n, 0x9bdc06a725c71235n, 0xc19bf174cf692694n,
    0xe49b69c19ef14ad2n, 0xefbe4786384f25e3n, 0x0fc19dc68b8cd5b5n, 0x240ca1cc77ac9c65n,
    0x2de92c6f592b0275n, 0x4a7484aa6ea6e483n, 0x5cb0a9dcbd41fbd4n, 0x76f988da831153b5n,
    0x983e5152ee66dfabn, 0xa831c66d2db43210n, 0xb00327c898fb213fn, 0xbf597fc7beef0ee4n,
    0xc6e00bf33da88fc2n, 0xd5a79147930aa725n, 0x06ca6351e003826fn, 0x142929670a0e6e70n,
    0x27b70a8546d22ffcn, 0x2e1b21385c26c926n, 0x4d2c6dfc5ac42aedn, 0x53380d139d95b3dfn,
    0x650a73548baf63den, 0x766a0abb3c77b2a8n, 0x81c2c92e47edaee6n, 0x92722c851482353bn,
    0xa2bfe8a14cf10364n, 0xa81a664bbc423001n, 0xc24b8b70d0f89791n, 0xc76c51a30654be30n,
    0xd192e819d6ef5218n, 0xd69906245565a910n, 0xf40e35855771202an, 0x106aa07032bbd1b8n,
    0x19a4c116b8d2d0c8n, 0x1e376c085141ab53n, 0x2748774cdf8eeb99n, 0x34b0bcb5e19b48a8n,
    0x391c0cb3c5c95a63n, 0x4ed8aa4ae3418acbn, 0x5b9cca4f7763e373n, 0x682e6ff3d6b2b8a3n,
    0x748f82ee5defb2fcn, 0x78a5636f43172f60n, 0x84c87814a1f0ab72n, 0x8cc702081a6439ecn,
    0x90befffa23631e28n, 0xa4506cebde82bde9n, 0xbef9a3f7b2c67915n, 0xc67178f2e372532bn,
    0xca273eceea26619cn, 0xd186b8c721c0c207n, 0xeada7dd6cde0eb1en, 0xf57d4f7fee6ed178n,
    0x06f067aa72176fban, 0x0a637dc5a2c898a6n, 0x113f9804bef90daen, 0x1b710b35131c471bn,
    0x28db77f523047d84n, 0x32caab7b40c72493n, 0x3c9ebe0a15c9bebcn, 0x431d67c49c100d4cn,
    0x4cc5d4becb3e42b6n, 0x597f299cfc657e2an, 0x5fcb6fab3ad6faecn, 0x6c44198c4a475817n,
  ];
  const H512 = [
    0x6a09e667f3bcc908n, 0xbb67ae8584caa73bn, 0x3c6ef372fe94f82bn, 0xa54ff53a5f1d36f1n,
    0x510e527fade682d1n, 0x9b05688c2b3e6c1fn, 0x1f83d9abfb41bd6bn, 0x5be0cd19137e2179n,
  ];

  const rotr64 = (x, n) => ((x >> n) | (x << (64n - n))) & MASK64;

  function sha512Bytes(data) {
    // Паддинг: 0x80, нули до 112 mod 128, затем длина в битах 128-битным BE.
    const padLen = (((112 - (data.length + 1)) % 128) + 128) % 128;
    const total = data.length + 1 + padLen + 16;
    const msg = new Uint8Array(total);
    msg.set(data);
    msg[data.length] = 0x80;
    let bitLen = BigInt(data.length) * 8n;
    for (let i = total - 1; i >= total - 16; i--) {
      msg[i] = Number(bitLen & 0xffn);
      bitLen >>= 8n;
    }

    let h = H512.slice();
    const w = new Array(80);
    for (let off = 0; off < total; off += 128) {
      for (let i = 0; i < 16; i++) {
        let v = 0n;
        for (let j = 0; j < 8; j++) v = (v << 8n) | BigInt(msg[off + i * 8 + j]);
        w[i] = v;
      }
      for (let i = 16; i < 80; i++) {
        const a = w[i - 15], b = w[i - 2];
        const s0 = rotr64(a, 1n) ^ rotr64(a, 8n) ^ (a >> 7n);
        const s1 = rotr64(b, 19n) ^ rotr64(b, 61n) ^ (b >> 6n);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) & MASK64;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 80; i++) {
        const S1 = rotr64(e, 14n) ^ rotr64(e, 18n) ^ rotr64(e, 41n);
        const ch = (e & f) ^ (~e & MASK64 & g);
        const t1 = (hh + S1 + ch + K512[i] + w[i]) & MASK64;
        const S0 = rotr64(a, 28n) ^ rotr64(a, 34n) ^ rotr64(a, 39n);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) & MASK64;
        hh = g; g = f; f = e; e = (d + t1) & MASK64;
        d = c; c = b; b = a; a = (t1 + t2) & MASK64;
      }
      const next = [a, b, c, d, e, f, g, hh];
      h = h.map((x, i) => (x + next[i]) & MASK64);
    }

    const out = new Uint8Array(64);
    h.forEach((word, i) => {
      for (let j = 7; j >= 0; j--) {
        out[i * 8 + j] = Number(word & 0xffn);
        word >>= 8n;
      }
    });
    return out;
  }

  const doubleSha512 = (data) => sha512Bytes(sha512Bytes(data));

  // --- HMAC-SHA512 (RFC 2104) — строительный блок RFC 6979 ------------------
  const HMAC_BLOCK = 128;

  function hmacSha512(key, message) {
    if (key.length > HMAC_BLOCK) key = sha512Bytes(key);
    const padded = new Uint8Array(HMAC_BLOCK);
    padded.set(key);
    const inner = new Uint8Array(HMAC_BLOCK + message.length);
    const outer = new Uint8Array(HMAC_BLOCK + 64);
    for (let i = 0; i < HMAC_BLOCK; i++) {
      inner[i] = padded[i] ^ 0x36;
      outer[i] = padded[i] ^ 0x5c;
    }
    inner.set(message, HMAC_BLOCK);
    outer.set(sha512Bytes(inner), HMAC_BLOCK);
    return sha512Bytes(outer);
  }

  // ==========================================================================
  // RIPEMD-160 — нужен для адреса (единственный не-SHA хеш в проекте)
  // ==========================================================================
  const RL = [
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
    3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12,
    1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
    4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13,
  ];
  const RR = [
    5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12,
    6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
    15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13,
    8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
    12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11,
  ];
  const SL = [
    11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8,
    7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
    11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5,
    11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
    9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6,
  ];
  const SR = [
    8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6,
    9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
    9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5,
    15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
    8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11,
  ];
  const KL = [0x00000000, 0x5a827999, 0x6ed9eba1, 0x8f1bbcdc, 0xa953fd4e];
  const KR = [0x50a28be6, 0x5c4dd124, 0x6d703ef3, 0x7a6d76e9, 0x00000000];

  const rotl32 = (x, n) => ((x << n) | (x >>> (32 - n))) >>> 0;

  function ripemdF(round, x, y, z) {
    switch (round) {
      case 0: return (x ^ y ^ z) >>> 0;
      case 1: return ((x & y) | (~x & z)) >>> 0;
      case 2: return ((x | ~y) ^ z) >>> 0;
      case 3: return ((x & z) | (y & ~z)) >>> 0;
      default: return (x ^ (y | ~z)) >>> 0;
    }
  }

  function ripemd160(data) {
    // Паддинг как у MD4/MD5: 0x80, нули до 56 mod 64, длина в битах LE.
    const padLen = (((56 - (data.length + 1)) % 64) + 64) % 64;
    const total = data.length + 1 + padLen + 8;
    const msg = new Uint8Array(total);
    msg.set(data);
    msg[data.length] = 0x80;
    let bitLen = BigInt(data.length) * 8n;
    for (let i = 0; i < 8; i++) {
      msg[total - 8 + i] = Number(bitLen & 0xffn);
      bitLen >>= 8n;
    }

    let h = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0];
    const x = new Array(16);
    for (let off = 0; off < total; off += 64) {
      for (let i = 0; i < 16; i++) {
        x[i] = (msg[off + i * 4] | (msg[off + i * 4 + 1] << 8) |
                (msg[off + i * 4 + 2] << 16) | (msg[off + i * 4 + 3] << 24)) >>> 0;
      }
      let [a, b, c, d, e] = h;
      let [a2, b2, c2, d2, e2] = h;
      for (let j = 0; j < 80; j++) {
        const round = Math.floor(j / 16);
        let t = (a + ripemdF(round, b, c, d) + x[RL[j]] + KL[round]) >>> 0;
        t = (rotl32(t, SL[j]) + e) >>> 0;
        a = e; e = d; d = rotl32(c, 10); c = b; b = t;
        // Правая ветвь идёт по тем же раундам в обратном порядке.
        let t2v = (a2 + ripemdF(4 - round, b2, c2, d2) + x[RR[j]] + KR[round]) >>> 0;
        t2v = (rotl32(t2v, SR[j]) + e2) >>> 0;
        a2 = e2; e2 = d2; d2 = rotl32(c2, 10); c2 = b2; b2 = t2v;
      }
      const t = (h[1] + c + d2) >>> 0;
      h[1] = (h[2] + d + e2) >>> 0;
      h[2] = (h[3] + e + a2) >>> 0;
      h[3] = (h[4] + a + b2) >>> 0;
      h[4] = (h[0] + b + c2) >>> 0;
      h[0] = t;
    }

    const out = new Uint8Array(20);
    h.forEach((word, i) => {
      out[i * 4] = word & 0xff;
      out[i * 4 + 1] = (word >>> 8) & 0xff;
      out[i * 4 + 2] = (word >>> 16) & 0xff;
      out[i * 4 + 3] = (word >>> 24) & 0xff;
    });
    return out;
  }

  // ==========================================================================
  // Base58 и адрес
  // ==========================================================================
  const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

  function base58Encode(data) {
    let num = 0n;
    for (const byte of data) num = (num << 8n) | BigInt(byte);
    let encoded = "";
    while (num > 0n) {
      const rem = num % 58n;
      num /= 58n;
      encoded = B58[Number(rem)] + encoded;
    }
    let pad = 0;
    while (pad < data.length && data[pad] === 0) pad++;
    return "1".repeat(pad) + encoded;
  }

  const ADDR_VERSION = 0x1f;            // обычный ECDSA-кошелёк

  function addressFromPublicKeyBytes(pubBytes) {
    const digest = ripemd160(sha512Bytes(pubBytes));
    const payload = new Uint8Array(21);
    payload[0] = ADDR_VERSION;
    payload.set(digest, 1);
    const checksum = doubleSha512(payload).slice(0, 4);
    const full = new Uint8Array(25);
    full.set(payload);
    full.set(checksum, 21);
    return "BHY" + base58Encode(full);
  }

  // ==========================================================================
  // secp256k1
  // ==========================================================================
  const P = 0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2fn;
  const N = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141n;
  const GX = 0x79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798n;
  const GY = 0x483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8n;
  const G = [GX, GY];

  const mod = (a, m) => ((a % m) + m) % m;

  function inverseMod(a, m) {
    // Расширенный алгоритм Евклида: у BigInt нет pow(a, -1, m).
    let [oldR, r] = [mod(a, m), m];
    let [oldS, s] = [1n, 0n];
    while (r !== 0n) {
      const q = oldR / r;
      [oldR, r] = [r, oldR - q * r];
      [oldS, s] = [s, oldS - q * s];
    }
    return mod(oldS, m);
  }

  function pointAdd(p1, p2) {
    if (p1 === null) return p2;
    if (p2 === null) return p1;
    const [x1, y1] = p1, [x2, y2] = p2;
    if (x1 === x2 && mod(y1 + y2, P) === 0n) return null;   // точка на бесконечности
    const m = x1 === x2
      ? mod(3n * x1 * x1 * inverseMod(2n * y1, P), P)
      : mod((y1 - y2) * inverseMod(x1 - x2, P), P);
    const x3 = mod(m * m - x1 - x2, P);
    return [x3, mod(m * (x1 - x3) - y1, P)];
  }

  function scalarMult(k, point) {
    let result = null;
    let addend = point;
    while (k > 0n) {
      if (k & 1n) result = pointAdd(result, addend);
      addend = pointAdd(addend, addend);
      k >>= 1n;
    }
    return result;
  }

  // ==========================================================================
  // Хеш сообщения и детерминированный нонс (RFC 6979)
  // ==========================================================================
  const QLEN = 256;                      // N.bit_length()

  function hashToInt(payloadBytes) {
    const digest = sha512Bytes(payloadBytes);
    let z = 0n;
    for (const byte of digest) z = (z << 8n) | BigInt(byte);
    return z >> BigInt(digest.length * 8 - QLEN);   // усечение до битности N
  }

  const intToBytes = (value, length) => {
    const out = new Uint8Array(length);
    for (let i = length - 1; i >= 0; i--) {
      out[i] = Number(value & 0xffn);
      value >>= 8n;
    }
    return out;
  };

  const concat = (...parts) => {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const part of parts) { out.set(part, at); at += part.length; }
    return out;
  };

  function* rfc6979Nonces(priv, z) {
    // Тот же HMAC-DRBG, что и в b_hydra/wallet.py: k выводится из ключа и хеша
    // сообщения, а не из ГСЧ. Благодаря этому подпись в браузере совпадает с
    // питоновской БАЙТ-В-БАЙТ — это и делает сверку в тестах возможной.
    // Именно целочисленное деление: у JS «/» даёт 32.875, а дробная длина
    // молча превращает запись в Uint8Array в никуда — material остался бы
    // нулевым, и k стал бы одинаковым для всех сообщений (утечка ключа).
    const rlen = (QLEN + 7) >> 3;
    const material = concat(intToBytes(priv, rlen), intToBytes(mod(z, N), rlen));
    let v = new Uint8Array(64).fill(0x01);
    let k = new Uint8Array(64).fill(0x00);
    k = hmacSha512(k, concat(v, Uint8Array.of(0x00), material));
    v = hmacSha512(k, v);
    k = hmacSha512(k, concat(v, Uint8Array.of(0x01), material));
    v = hmacSha512(k, v);
    for (;;) {
      let temp = new Uint8Array(0);
      while (temp.length * 8 < QLEN) {
        v = hmacSha512(k, v);
        temp = concat(temp, v);
      }
      let candidate = 0n;
      for (const byte of temp) candidate = (candidate << 8n) | BigInt(byte);
      candidate >>= BigInt(Math.max(0, temp.length * 8 - QLEN));
      if (candidate >= 1n && candidate < N) yield candidate;
      k = hmacSha512(k, concat(v, Uint8Array.of(0x00)));
      v = hmacSha512(k, v);
    }
  }

  // ==========================================================================
  // Сериализация «как в Python» — самое хрупкое место
  // ==========================================================================
  function pythonFloatRepr(value) {
    if (!Number.isFinite(value)) {
      throw new Error("сумма должна быть конечным числом: " + value);
    }
    if (value === 0) return Object.is(value, -0) ? "-0.0" : "0.0";
    const negative = value < 0;
    // toExponential() без аргумента даёт кратчайшую запись, однозначно
    // восстанавливающую число, — ровно то же семейство цифр, что и repr().
    const [mantissa, expPart] = Math.abs(value).toExponential().split("e");
    const digits = mantissa.replace(".", "");
    const decpt = parseInt(expPart, 10) + 1;   // позиция точки в строке цифр

    let out;
    if (decpt <= -4 || decpt > 16) {
      // CPython переходит на экспоненту ровно на этих границах, и пишет
      // порядок минимум двумя цифрами со знаком: 1e-08, 1e+17.
      const exp = decpt - 1;
      const head = digits.length > 1
        ? digits[0] + "." + digits.slice(1)
        : digits;
      const magnitude = String(Math.abs(exp)).padStart(2, "0");
      out = head + "e" + (exp < 0 ? "-" : "+") + magnitude;
    } else if (decpt <= 0) {
      out = "0." + "0".repeat(-decpt) + digits;
    } else if (decpt >= digits.length) {
      out = digits + "0".repeat(decpt - digits.length) + ".0";
    } else {
      out = digits.slice(0, decpt) + "." + digits.slice(decpt);
    }
    return negative ? "-" + out : out;
  }

  // Строки в payload — hex, base58 и chain_id, то есть чистый ASCII без
  // экранирования; на этом множестве JSON.stringify совпадает с json.dumps.
  const jsonString = (text) => JSON.stringify(String(text));

  const CHAIN_ID = "b-hydra-mainnet";

  function canonicalPayload(tx) {
    // json.dumps(..., sort_keys=True) — ключи по алфавиту, разделители
    // ", " и ": " (пробелы обязательны, это умолчание Python).
    const vin = tx.vin
      .map((i) => `{"index": ${BigInt(i.index)}, "txid": ${jsonString(i.txid)}}`)
      .join(", ");
    const vout = tx.vout
      .map((o) => `{"address": ${jsonString(o.address)}, "amount": ${pythonFloatRepr(o.amount)}}`)
      .join(", ");
    return `{"chain_id": ${jsonString(CHAIN_ID)}, "timestamp": ${pythonFloatRepr(tx.timestamp)}, ` +
           `"vin": [${vin}], "vout": [${vout}]}`;
  }

  const utf8 = (text) => new TextEncoder().encode(text);
  const toHex = (bytes) =>
    Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

  const txid = (tx) => toHex(sha512Bytes(utf8(canonicalPayload(tx))));

  // ==========================================================================
  // Публичный API
  // ==========================================================================
  function normalizePrivateKey(input) {
    let cleaned = String(input == null ? "" : input).replace(/\s+/g, "");
    if (cleaned.slice(0, 2).toLowerCase() === "0x") cleaned = cleaned.slice(2);
    if (cleaned.length !== 64) {
      throw new Error(
        `приватный ключ должен быть 64 hex-символа, а тут ${cleaned.length}`);
    }
    if (!/^[0-9a-fA-F]{64}$/.test(cleaned)) {
      throw new Error("приватный ключ содержит не-hex символы");
    }
    const value = BigInt("0x" + cleaned);
    if (value <= 0n || value >= N) {
      throw new Error("приватный ключ вне диапазона кривой secp256k1");
    }
    return value;
  }

  /** Кошелёк из приватного ключа: публичный ключ и адрес, без обращения к сети. */
  function walletFromPrivateKey(privateKeyHex) {
    const priv = normalizePrivateKey(privateKeyHex);
    const [x, y] = scalarMult(priv, G);
    const pubBytes = concat(
      Uint8Array.of(0x04), intToBytes(x, 32), intToBytes(y, 32));
    return {
      privateKey: priv.toString(16).padStart(64, "0"),
      publicKey: toHex(pubBytes),
      address: addressFromPublicKeyBytes(pubBytes),
    };
  }

  /** Подписывает произвольные байты/строку: hex r||s (low-s), как в Python. */
  function signPayload(privateKeyHex, payload) {
    const priv = normalizePrivateKey(privateKeyHex);
    const bytes = typeof payload === "string" ? utf8(payload) : payload;
    const z = hashToInt(bytes);
    for (const k of rfc6979Nonces(priv, z)) {
      const point = scalarMult(k, G);
      const r = mod(point[0], N);
      if (r === 0n) continue;
      let s = mod(inverseMod(k, N) * (z + r * priv), N);
      if (s === 0n) continue;
      if (s > N / 2n) s = N - s;              // low-s против ковкости подписи
      return toHex(intToBytes(r, 32)) + toHex(intToBytes(s, 32));
    }
  }

  /**
   * Собирает и подписывает транзакцию — целиком на устройстве.
   *
   * utxos: [{txid, index, amount}] — что тратим (их отдаёт GET /api/utxos/<адрес>).
   * Сдача возвращается на свой же адрес. Возвращает готовый объект для
   * POST /api/transaction; приватный ключ никуда не уходит.
   */
  function buildSignedTransaction(options) {
    const { privateKey, to, amount, fee = 0, utxos, timestamp } = options;
    const wallet = walletFromPrivateKey(privateKey);
    const value = Number(amount), feeValue = Number(fee);
    if (!(value > 0)) throw new Error("сумма перевода должна быть больше нуля");
    if (feeValue < 0) throw new Error("комиссия не может быть отрицательной");

    // Набираем входы, пока не покроем сумму с комиссией.
    const needed = round8(value + feeValue);
    const chosen = [];
    let collected = 0;
    for (const utxo of utxos || []) {
      chosen.push(utxo);
      collected = round8(collected + Number(utxo.amount));
      if (collected >= needed) break;
    }
    if (collected < needed) {
      throw new Error(
        `не хватает средств: нужно ${needed}, доступно ${collected}`);
    }

    const vout = [{ address: to, amount: round8(value) }];
    const change = round8(collected - needed);
    if (change > 0) vout.push({ address: wallet.address, amount: change });

    const tx = {
      vin: chosen.map((u) => ({ txid: u.txid, index: Number(u.index) })),
      vout,
      // Метка времени всегда трактуется как float: JS сериализует 1785009903.0
      // как «1785009903», поэтому Transaction на узле приводит её к float —
      // иначе payload разошёлся бы с подписанным раз на тысячу переводов.
      timestamp: typeof timestamp === "number" ? timestamp : Date.now() / 1000,
    };

    const signature = signPayload(privateKey, canonicalPayload(tx));
    return {
      txid: txid(tx),
      timestamp: tx.timestamp,
      vin: tx.vin.map((i) => ({
        txid: i.txid,
        index: i.index,
        public_key: wallet.publicKey,
        signature,
      })),
      vout: tx.vout.map((o) => ({ amount: o.amount, address: o.address })),
    };
  }

  // Суммы кратны 1e-8 (DECIMALS=8); округление гасит хвосты double-арифметики.
  const round8 = (value) => Math.round(value * 1e8) / 1e8;

  return {
    sha512Bytes, doubleSha512, hmacSha512, ripemd160, base58Encode,
    addressFromPublicKeyBytes, scalarMult, rfc6979Nonces,
    pythonFloatRepr, canonicalPayload, txid,
    walletFromPrivateKey, signPayload, buildSignedTransaction,
    toHex, utf8, round8, CHAIN_ID, G, N, P,
  };
});
