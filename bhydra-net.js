/*
 * bhydra-net.js — сетевой слой кошелька: несколько узлов, выбор лучшего,
 * отказоустойчивость и SPV-проверка включения транзакции. Без зависимостей.
 *
 * Зачем. Кошелёк ходил в ОДИН узел — тот, который отдал страницу. Это значит
 * полное доверие одному серверу и полная от него зависимость: узел выключили —
 * кошелёк ослеп; узел отстал или соврал — кошелёк показывает его версию мира
 * и молчит об этом.
 *
 * Здесь узлов несколько, и лучший выбирается ПО ТОМУ ЖЕ ПРАВИЛУ, что у самих
 * узлов: по суммарной работе цепочки (как `replace_chain`), а не по высоте.
 * Длинная цепочка дешёвых блоков не должна выигрывать ни в узле, ни здесь —
 * иначе клиент и сеть считали бы главной разные цепочки.
 *
 * Узлы с ЧУЖИМ генезисом отбрасываются: `chain_id` общий у всей сети, а
 * генезис различает несовместимые цепочки (у сети с другой базовой сложностью
 * он другой). Это опознание сети, а не аутентификация — ровно как в p2p.
 *
 * SPV: подтверждение платежа проверяется доказательством Меркла против корня
 * из ЗАГОЛОВКА блока, а не по слову узла «я её видел». Узел может соврать про
 * баланс, но подделать путь Меркла к настоящему корню — нет.
 *
 * ⚠️ Чего это НЕ даёт: заголовки блоков мы не проверяем на proof-of-work и не
 * скачиваем всю цепочку заголовков, поэтому узел, придумавший блок целиком,
 * обманет SPV-проверку. Полная защита — сверка одного и того же txid у
 * НЕСКОЛЬКИХ независимых узлов (`confirmAcross`) плюс собственный узел.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BHydraNet = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const TIMEOUT = 6000;             // на один запрос к узлу, мс
  const STORAGE_KEY = "bhydra.nodes";

  function normalise(url) {
    const text = String(url || "").trim().replace(/\/+$/, "");
    if (!text) return "";
    if (/^https?:\/\//i.test(text)) return text;
    return "http://" + text;        // «192.168.0.10:8000» тоже адрес узла
  }

  async function fetchJson(url, options) {
    const settings = Object.assign({cache: "no-store"}, options || {});
    // AbortController есть везде, где есть fetch; таймаут обязателен —
    // недоступный узел иначе держал бы обновление до бесконечности.
    if (typeof AbortController !== "undefined") {
      const stop = new AbortController();
      settings.signal = stop.signal;
      const timer = setTimeout(() => stop.abort(), TIMEOUT);
      try {
        const response = await fetch(url, settings);
        return {ok: response.ok, status: response.status, body: await response.json()};
      } finally {
        clearTimeout(timer);
      }
    }
    const response = await fetch(url, settings);
    return {ok: response.ok, status: response.status, body: await response.json()};
  }

  class Network {
    constructor(options) {
      const opts = options || {};
      this.nodes = (opts.nodes || []).map(normalise).filter(Boolean);
      this.storage = opts.storage || null;      // localStorage или свой объект
      this.status = new Map();                  // url → сведения из /api/info
      this.chosen = null;                       // url лучшего узла
      this.genesis = opts.genesis || null;      // «своя» сеть, если уже известна
      this._fetch = opts.fetchJson || fetchJson;
      this.load();
    }

    // --- список узлов ----------------------------------------------------
    load() {
      if (!this.storage) return this.nodes;
      try {
        const saved = JSON.parse(this.storage.getItem(STORAGE_KEY) || "[]");
        for (const url of saved) this.add(url, {save: false});
      } catch (e) { /* испорченная запись — не беда, начнём с умолчаний */ }
      return this.nodes;
    }

    save() {
      if (!this.storage) return false;
      try {
        this.storage.setItem(STORAGE_KEY, JSON.stringify(this.nodes));
        return true;
      } catch (e) { return false; }
    }

    add(url, options) {
      const clean = normalise(url);
      if (!clean || this.nodes.includes(clean)) return false;
      this.nodes.push(clean);
      if (!options || options.save !== false) this.save();
      return true;
    }

    remove(url) {
      const clean = normalise(url);
      const at = this.nodes.indexOf(clean);
      if (at < 0) return false;
      this.nodes.splice(at, 1);
      this.status.delete(clean);
      if (this.chosen === clean) this.chosen = null;
      this.save();
      return true;
    }

    // --- опрос сети ------------------------------------------------------
    /** Спрашивает /api/info у ВСЕХ узлов параллельно и выбирает лучший.
     *
     *  Параллельно, а не по очереди: один недоступный узел иначе задерживал бы
     *  весь опрос на свой таймаут — та же ошибка, что когда-то была в обходе
     *  пиров у самого узла.
     */
    async survey() {
      const results = await Promise.all(this.nodes.map(async (url) => {
        const started = Date.now();
        try {
          const answer = await this._fetch(url + "/api/info");
          if (!answer.ok) throw new Error("статус " + answer.status);
          const info = answer.body || {};
          return {url, ok: true, latency: Date.now() - started,
                  height: Number(info.height) || 0,
                  work: Number(info.total_work) || 0,
                  genesis: info.genesis || null,
                  chainId: info.chain_id || null,
                  info};
        } catch (error) {
          return {url, ok: false, latency: Date.now() - started,
                  error: String(error && error.message || error)};
        }
      }));

      // Своя сеть — та, что у большинства ответивших узлов. Иначе один
      // подставной узел с чужим генезисом переопределил бы «свою» сеть.
      if (!this.genesis) this.genesis = majorityGenesis(results);
      for (const item of results) {
        item.foreign = !!(item.ok && this.genesis && item.genesis &&
                          item.genesis !== this.genesis);
        this.status.set(item.url, item);
      }

      const usable = results.filter((r) => r.ok && !r.foreign);
      usable.sort((a, b) => (b.work - a.work) || (b.height - a.height) ||
                            (a.latency - b.latency));
      this.chosen = usable.length ? usable[0].url : null;
      return {nodes: results, best: this.chosen,
              height: usable.length ? usable[0].height : 0,
              work: usable.length ? usable[0].work : 0,
              // «Отставший» — тот, у кого работы меньше, чем у лучшего.
              behind: usable.filter((r) => r.work < (usable[0] || {}).work).length,
              reachable: usable.length, total: this.nodes.length};
    }

    /** Порядок обхода: сначала выбранный, потом остальные живые. */
    order() {
      const rest = this.nodes.filter((url) => url !== this.chosen &&
        !(this.status.get(url) || {}).foreign);
      return (this.chosen ? [this.chosen] : []).concat(rest);
    }

    /** Запрос с отказоустойчивостью: не ответил один узел — пробуем следующий. */
    async request(path, options) {
      const errors = [];
      for (const url of this.order()) {
        try {
          const answer = await this._fetch(url + path, options);
          if (!answer.ok) {
            errors.push(url + ": статус " + answer.status);
            // Ошибка узла (4xx) — ответ по существу, дальше идти незачем.
            if (answer.status >= 400 && answer.status < 500)
              return {ok: false, node: url, status: answer.status, body: answer.body};
            continue;
          }
          return {ok: true, node: url, status: answer.status, body: answer.body};
        } catch (error) {
          errors.push(url + ": " + (error && error.message || error));
        }
      }
      return {ok: false, node: null, status: 0, error: errors.join("; ")};
    }

    get(path) { return this.request(path); }

    post(path, body) {
      return this.request(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body),
      });
    }

    // --- SPV -------------------------------------------------------------
    /** Проверяет доказательство Меркла: лист + путь дают заявленный корень.
     *
     *  Требует bhydra-sign.js (двойной SHA-512) — тот же хеш, что и в дереве
     *  на узле.
     */
    verifyProof(leafHex, path, root) {
      if (typeof BHydra === "undefined") return false;
      // Отсутствующий путь — ОТКАЗ, а не «путь из нуля шагов». Иначе проверка
      // вырождается в «лист равен корню», что верно для блока из одной
      // транзакции, — и SPV молча подтверждает что угодно. Так и было: поле
      // называется proof, а читалось path, и всё «работало».
      if (!Array.isArray(path) || !leafHex || !root) return false;
      try {
        let current = fromHex(leafHex);
        for (const step of path) {
          const sibling = fromHex(step.hash);
          const joined = step.position === "left"
            ? concat(sibling, current) : concat(current, sibling);
          current = BHydra.doubleSha512(joined);
        }
        return BHydra.toHex(current) === root;
      } catch (error) { return false; }
    }

    /** Подтверждение транзакции: доказательство включения + корень из блока.
     *
     *  Корень берётся из ЗАГОЛОВКА блока, а не из самого доказательства:
     *  иначе узел прислал бы согласованную пару «путь + корень», и проверка
     *  подтверждала бы сама себя.
     */
    async confirm(txid) {
      const proof = await this.get("/api/proof/" + encodeURIComponent(txid));
      if (!proof.ok || !proof.body || !proof.body.leaf)
        return {ok: false, reason: "нет доказательства"};
      const index = proof.body.block_index;
      const block = await this.get("/api/block/" + index);
      if (!block.ok || !block.body) return {ok: false, reason: "нет блока"};
      const root = block.body.merkle_root;
      const valid = this.verifyProof(proof.body.leaf, proof.body.proof, root);
      const height = (this.status.get(this.chosen) || {}).height || 0;
      return {ok: valid, blockIndex: index, root,
              confirmations: valid ? Math.max(0, height - index) : 0,
              node: proof.node,
              reason: valid ? null : "путь Меркла не сошёлся с корнем блока"};
    }

    /** Сколько НЕЗАВИСИМЫХ узлов подтверждают ту же транзакцию в том же блоке.
     *
     *  Это и есть защита от одного лгущего узла: подделать блок целиком он
     *  может, но заставить остальных согласиться — нет.
     */
    async confirmAcross(txid) {
      const answers = await Promise.all(this.order().map(async (url) => {
        try {
          const proof = await this._fetch(url + "/api/proof/" + encodeURIComponent(txid));
          if (!proof.ok || !proof.body || !proof.body.leaf) return null;
          const block = await this._fetch(url + "/api/block/" + proof.body.block_index);
          if (!block.ok || !block.body) return null;
          const valid = this.verifyProof(proof.body.leaf, proof.body.proof,
                                         block.body.merkle_root);
          return valid ? {url, blockIndex: proof.body.block_index,
                          hash: block.body.hash} : null;
        } catch (error) { return null; }
      }));
      const good = answers.filter(Boolean);
      const hashes = new Set(good.map((item) => item.hash));
      return {confirmed: good.length, asked: this.order().length,
              // Узлы разошлись в том, В КАКОМ блоке лежит транзакция, —
              // это развилка, и доверять такому подтверждению нельзя.
              agree: hashes.size <= 1, nodes: good};
    }
  }

  /** hex → байты. Нечётная длина — ОШИБКА, а не «отбросим последний символ»:
   *  молчаливое усечение дало бы не тот хеш, и проверка «не сошлась» вместо
   *  честного отказа. */
  function fromHex(text) {
    const value = String(text || "");
    if (value.length % 2 !== 0) throw new Error("hex нечётной длины");
    const out = new Uint8Array(value.length / 2);
    for (let i = 0; i < out.length; i++) {
      const byte = parseInt(value.substr(i * 2, 2), 16);
      if (Number.isNaN(byte)) throw new Error("не hex");
      out[i] = byte;
    }
    return out;
  }

  function concat(a, b) {
    const out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
  }

  function majorityGenesis(results) {
    const votes = new Map();
    for (const item of results) {
      if (!item.ok || !item.genesis) continue;
      votes.set(item.genesis, (votes.get(item.genesis) || 0) + 1);
    }
    let best = null, bestCount = 0;
    for (const [genesis, count] of votes) {
      if (count > bestCount) { best = genesis; bestCount = count; }
    }
    return best;
  }

  return {Network, normalise, majorityGenesis, fromHex, TIMEOUT, STORAGE_KEY};
});
