/*
 * bhydra-qr.js — генератор QR-кодов в браузере, без зависимостей.
 *
 * Порт `b_hydra/qrcode_gen.py`: байтовый режим, версии 1–10, уровень
 * коррекции M. Матрица совпадает с Python МОДУЛЬ В МОДУЛЬ — это проверяет
 * tests/test_qr_browser.py на общем корпусе строк.
 *
 * Зачем в браузере, если есть Python. Кошелёк на телефоне обязан показывать
 * QR своего адреса и БЕЗ СЕТИ: «получить деньги» — это ровно тот случай, когда
 * узел может быть недоступен, а показать адрес нужно. Запрос картинки у узла
 * такой экран сломал бы.
 *
 * Использование:
 *   const rows = BHydraQR.matrix("BHYD…");   // массив строк из 0/1
 *   BHydraQR.toSvg(rows, {size: 240});       // готовый <svg> строкой
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BHydraQR = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // --- Галуа GF(256), порождающий многочлен x^8+x^4+x^3+x^2+1 (0x11d) ------
  const EXP = new Array(512).fill(0);
  const LOG = new Array(256).fill(0);
  (function () {
    let x = 1;
    for (let i = 0; i < 255; i++) {
      EXP[i] = x;
      LOG[x] = i;
      x <<= 1;
      if (x & 0x100) x ^= 0x11d;
    }
    for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
  })();

  function gfMul(a, b) {
    if (a === 0 || b === 0) return 0;
    return EXP[LOG[a] + LOG[b]];
  }

  function rsGenerator(n) {
    let g = [1];
    for (let i = 0; i < n; i++) {
      const ng = new Array(g.length + 1).fill(0);
      for (let j = 0; j < g.length; j++) {
        ng[j] ^= g[j];
        ng[j + 1] ^= gfMul(g[j], EXP[i]);
      }
      g = ng;
    }
    return g;
  }

  function rsEncode(data, nEc) {
    const gen = rsGenerator(nEc);
    let res = new Array(nEc).fill(0);
    for (const byte of data) {
      const factor = byte ^ res[0];
      res = res.slice(1).concat([0]);
      for (let i = 0; i < nEc; i++) res[i] ^= gfMul(gen[i + 1], factor);
    }
    return res;
  }

  // --- Параметры версий, уровень коррекции M ------------------------------
  // (всего слов данных, EC-слов на блок, группы: [[блоков, слов данных], …])
  const VERSIONS_M = {
    1: [16, 10, [[1, 16]]],
    2: [28, 16, [[1, 28]]],
    3: [44, 26, [[1, 44]]],
    4: [64, 18, [[2, 32]]],
    5: [86, 24, [[2, 43]]],
    6: [108, 16, [[4, 27]]],
    7: [124, 18, [[4, 31]]],
    8: [154, 22, [[2, 38], [2, 39]]],
    9: [182, 22, [[3, 36], [2, 37]]],
    10: [216, 26, [[4, 43], [1, 44]]],
  };

  const ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
  };

  function chooseVersion(nBytes) {
    for (let v = 1; v <= 10; v++) {
      const dataCw = VERSIONS_M[v][0];
      const ccBits = v >= 10 ? 16 : 8;
      const capacity = dataCw * 8 - (4 + ccBits);
      if (nBytes * 8 <= capacity) return v;
    }
    throw new Error("данные слишком длинные для QR версии ≤10");
  }

  function utf8Bytes(text) {
    if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
    const out = [];                       // запасной путь для древних движков
    for (const ch of unescape(encodeURIComponent(text))) out.push(ch.charCodeAt(0));
    return Uint8Array.from(out);
  }

  function encodeData(text, version) {
    const raw = utf8Bytes(text);
    const dataCw = VERSIONS_M[version][0];
    const ccBits = version >= 10 ? 16 : 8;
    const bits = [];
    const put = (value, length) => {
      for (let i = length - 1; i >= 0; i--) bits.push((value >> i) & 1);
    };
    put(0b0100, 4);                        // байтовый режим
    put(raw.length, ccBits);
    for (const byte of raw) put(byte, 8);
    const cap = dataCw * 8;
    put(0, Math.min(4, cap - bits.length));   // терминатор
    while (bits.length % 8) bits.push(0);
    const codewords = [];
    for (let i = 0; i < bits.length; i += 8) {
      let value = 0;
      for (let j = 0; j < 8; j++) value = (value << 1) | bits[i + j];
      codewords.push(value);
    }
    const pad = [0xEC, 0x11];
    let k = 0;
    while (codewords.length < dataCw) codewords.push(pad[k++ % 2]);
    return codewords;
  }

  function interleave(codewords, version) {
    const [, ecPerBlock, groups] = VERSIONS_M[version];
    const blocks = [];
    let pos = 0;
    for (const [count, dwords] of groups) {
      for (let i = 0; i < count; i++) {
        const data = codewords.slice(pos, pos + dwords);
        pos += dwords;
        blocks.push([data, rsEncode(data, ecPerBlock)]);
      }
    }
    const result = [];
    const maxData = Math.max.apply(null, blocks.map(([d]) => d.length));
    for (let i = 0; i < maxData; i++)
      for (const [data] of blocks) if (i < data.length) result.push(data[i]);
    for (let i = 0; i < ecPerBlock; i++)
      for (const [, ec] of blocks) result.push(ec[i]);
    return result;
  }

  // --- Построение матрицы --------------------------------------------------
  function newMatrix(size) {
    const m = [];
    for (let r = 0; r < size; r++) m.push(new Array(size).fill(null));
    return m;
  }

  function placeFinder(m, r, c) {
    const size = m.length;
    for (let dr = -1; dr <= 7; dr++) {
      for (let dc = -1; dc <= 7; dc++) {
        const rr = r + dr, cc = c + dc;
        if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
        // Узор 7×7 с рамкой-разделителем.
        if (((dr === 0 || dr === 6) && dc >= 0 && dc <= 6) ||
            ((dc === 0 || dc === 6) && dr >= 0 && dr <= 6)) m[rr][cc] = 1;
        else if (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4) m[rr][cc] = 1;
        else m[rr][cc] = 0;
      }
    }
  }

  function placeAlignment(m, version) {
    const centers = ALIGN[version];
    const size = m.length;
    for (const r of centers) {
      for (const c of centers) {
        // Не накладывать на поисковые узоры.
        if ((r < 8 && c < 8) || (r < 8 && c > size - 9) || (r > size - 9 && c < 8))
          continue;
        for (let dr = -2; dr <= 2; dr++) {
          for (let dc = -2; dc <= 2; dc++) {
            const edge = Math.max(Math.abs(dr), Math.abs(dc));
            m[r + dr][c + dc] = (edge === 0 || edge === 2) ? 1 : 0;
          }
        }
      }
    }
  }

  function placeTiming(m) {
    const size = m.length;
    for (let i = 8; i < size - 8; i++) {
      const bit = 1 - (i % 2);
      if (m[6][i] === null) m[6][i] = bit;
      if (m[i][6] === null) m[i][6] = bit;
    }
  }

  function reserveFormat(m) {
    const size = m.length;
    for (let i = 0; i < 9; i++) {
      if (m[8][i] === null) m[8][i] = 0;
      if (m[i][8] === null) m[i][8] = 0;
    }
    for (let i = 0; i < 8; i++) {
      if (m[8][size - 1 - i] === null) m[8][size - 1 - i] = 0;
      if (m[size - 1 - i][8] === null) m[size - 1 - i][8] = 0;
    }
    m[size - 8][8] = 1;                    // тёмный модуль
  }

  function placeData(m, bits) {
    const size = m.length;
    let idx = 0, upward = true, col = size - 1;
    while (col > 0) {
      if (col === 6) col -= 1;             // пропустить вертикальный тайминг
      for (let step = 0; step < size; step++) {
        const r = upward ? size - 1 - step : step;
        for (const c of [col, col - 1]) {
          if (m[r][c] === null) {
            m[r][c] = idx < bits.length ? bits[idx] : 0;
            idx++;
          }
        }
      }
      upward = !upward;
      col -= 2;
    }
  }

  const MASKS = [
    (r, c) => (r + c) % 2 === 0,
    (r, c) => r % 2 === 0,
    (r, c) => c % 3 === 0,
    (r, c) => (r + c) % 3 === 0,
    (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
    (r, c) => ((r * c) % 2) + ((r * c) % 3) === 0,
    (r, c) => (((r * c) % 2) + ((r * c) % 3)) % 2 === 0,
    (r, c) => (((r + c) % 2) + ((r * c) % 3)) % 2 === 0,
  ];

  function applyMask(m, reserved, mask) {
    const size = m.length;
    const out = m.map((row) => row.slice());
    const fn = MASKS[mask];
    for (let r = 0; r < size; r++)
      for (let c = 0; c < size; c++)
        if (!reserved[r][c] && fn(r, c)) out[r][c] ^= 1;
    return out;
  }

  function penalty(m) {
    const size = m.length;
    let score = 0;
    const transposed = [];
    for (let c = 0; c < size; c++) {
      const col = [];
      for (let r = 0; r < size; r++) col.push(m[r][c]);
      transposed.push(col);
    }
    // Правило 1: серии из 5+ одинаковых модулей.
    for (const line of [m, transposed]) {
      for (const row of line) {
        let run = 1, prev = null;
        for (const v of row) {
          if (v === prev) run++;
          else {
            if (run >= 5) score += 3 + (run - 5);
            run = 1;
            prev = v;
          }
        }
        if (run >= 5) score += 3 + (run - 5);
      }
    }
    // Правило 2: блоки 2×2.
    for (let r = 0; r < size - 1; r++)
      for (let c = 0; c < size - 1; c++)
        if (m[r][c] === m[r][c + 1] && m[r][c] === m[r + 1][c] &&
            m[r][c] === m[r + 1][c + 1]) score += 3;
    // Правило 3: узор 1011101 с отступом.
    const pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
    const pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
    const same = (a, b) => a.every((v, i) => v === b[i]);
    for (const line of [m, transposed]) {
      for (const row of line) {
        for (let i = 0; i < size - 10; i++) {
          const seg = row.slice(i, i + 11);
          if (same(seg, pat1) || same(seg, pat2)) score += 40;
        }
      }
    }
    // Правило 4: отклонение доли тёмных модулей от 50 %.
    let dark = 0;
    for (const row of m) for (const v of row) dark += v;
    const ratio = Math.floor((dark * 100) / (size * size));
    score += 10 * Math.floor(Math.abs(ratio - 50) / 5);
    return score;
  }

  function formatBits(mask) {
    const fmt = (0b00 << 3) | mask;        // уровень M = 00
    const gen = 0b10100110111;
    let rem = fmt << 10;
    for (let i = 14; i >= 10; i--) if (rem & (1 << i)) rem ^= gen << (i - 10);
    const bits = ((fmt << 10) | rem) ^ 0b101010000010010;
    const out = [];
    for (let i = 14; i >= 0; i--) out.push((bits >> i) & 1);
    return out;
  }

  function placeFormat(m, mask) {
    const size = m.length;
    const bits = formatBits(mask);
    const coords1 = [[8, 0], [8, 1], [8, 2], [8, 3], [8, 4], [8, 5], [8, 7],
                     [8, 8], [7, 8], [5, 8], [4, 8], [3, 8], [2, 8], [1, 8], [0, 8]];
    coords1.forEach(([r, c], i) => { m[r][c] = bits[i]; });
    const coords2 = [[size - 1, 8], [size - 2, 8], [size - 3, 8], [size - 4, 8],
                     [size - 5, 8], [size - 6, 8], [size - 7, 8],
                     [8, size - 8], [8, size - 7], [8, size - 6], [8, size - 5],
                     [8, size - 4], [8, size - 3], [8, size - 2], [8, size - 1]];
    coords2.forEach(([r, c], i) => { m[r][c] = bits[i]; });
  }

  function matrix(text) {
    const version = chooseVersion(utf8Bytes(text).length);
    const final = interleave(encodeData(text, version), version);
    const bits = [];
    for (const cw of final) for (let i = 7; i >= 0; i--) bits.push((cw >> i) & 1);

    const size = 17 + version * 4;
    const m = newMatrix(size);
    placeFinder(m, 0, 0);
    placeFinder(m, 0, size - 7);
    placeFinder(m, size - 7, 0);
    placeAlignment(m, version);
    placeTiming(m);
    reserveFormat(m);

    // Карта функциональных модулей — строго до размещения данных.
    const reserved = m.map((row) => row.map((v) => v !== null));
    placeData(m, bits);

    let best = null, bestScore = null;
    for (let mask = 0; mask < 8; mask++) {
      const cand = applyMask(m, reserved, mask);
      placeFormat(cand, mask);
      const score = penalty(cand);
      if (bestScore === null || score < bestScore) {
        best = cand;
        bestScore = score;
      }
    }
    return best.map((row) => row.join(""));
  }

  /** Готовый SVG. Рисуется одним путём: так он остаётся чётким на любом
   *  экране и весит на порядок меньше, чем прямоугольник на каждый модуль. */
  function toSvg(rows, options) {
    const opts = options || {};
    const quiet = opts.quiet === undefined ? 2 : opts.quiet;
    const size = rows.length;
    const total = size + quiet * 2;
    const path = [];
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (rows[r][c] === "1") path.push(`M${c + quiet} ${r + quiet}h1v1h-1z`);
      }
    }
    const px = opts.size || 240;
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="${px}" ` +
      `viewBox="0 0 ${total} ${total}" shape-rendering="crispEdges" role="img" ` +
      `aria-label="QR-код адреса">` +
      `<rect width="${total}" height="${total}" fill="${opts.light || "#ffffff"}"/>` +
      `<path d="${path.join("")}" fill="${opts.dark || "#000000"}"/></svg>`;
  }

  return { matrix, toSvg, chooseVersion, _internals: { gfMul, rsEncode, formatBits } };
});
