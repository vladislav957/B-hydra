"""Синхронизация кошелька с сетью: несколько узлов, выбор по работе, SPV.

Кошелёк ходил в ОДИН узел — тот, что отдал страницу. Это и полное доверие
одному серверу, и полная от него зависимость. Здесь проверяется, что клиент
выбирает цепочку по ТОМУ ЖЕ правилу, что и узлы (суммарная работа, а не
высота), переживает отказ узла и проверяет включение своей транзакции
доказательством Меркла, а не верит на слово.

Тесты JS пропускаются без node, браузерные — без playwright.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import urllib.request

import pytest

from b_hydra.api import make_server
from b_hydra.merkle import verify_proof
from b_hydra.node import BHydraNode
from b_hydra.wallet import generate_wallet

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET_JS = os.path.join(ROOT, "bhydra-net.js")
SIGN_JS = os.path.join(ROOT, "bhydra-sign.js")
WALLET_HTML = os.path.join(ROOT, "wallet.html")
NODE = shutil.which("node")


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _run_js(tmp_path, script):
    """Гоняет кусок JS в node с загруженными bhydra-sign.js и bhydra-net.js."""
    path = tmp_path / "case.js"
    path.write_text(
        f'global.BHydra = require({json.dumps(SIGN_JS)});\n'
        f'const BHydraNet = require({json.dumps(NET_JS)});\n'
        + script, encoding="utf-8")
    result = subprocess.run([NODE, str(path)], capture_output=True, text=True,
                            timeout=180)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --- Узел сообщает то, по чему можно выбирать ---------------------------------
def test_info_exposes_work_and_genesis(tmp_path):
    """Без суммарной работы клиент не смог бы выбрать цепочку по правилу
    консенсуса, а без генезиса — отличить свою сеть от чужой."""
    port = _free_port()
    server = make_server("127.0.0.1", port, str(tmp_path / "chain.json"),
                         difficulty=2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        info = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/info"))
        assert isinstance(info["total_work"], int) and info["total_work"] > 0
        assert len(info["genesis"]) == 128        # SHA-512 в hex
        assert info["chain_id"]
    finally:
        server.shutdown()


# --- Выбор узла --------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="нет node")
def test_best_node_is_chosen_by_work_not_height(tmp_path):
    """Длинная цепочка дешёвых блоков не должна выигрывать.

    Иначе клиент и сеть считали бы главной РАЗНЫЕ цепочки: узлы сравнивают
    работу, а клиент — длину.
    """
    result = _run_js(tmp_path, """
      const fake = async (url) => {
        const table = {
          "http://tall/api/info":  {height: 100, total_work: 100, genesis: "aa"},
          "http://heavy/api/info": {height: 10,  total_work: 999, genesis: "aa"},
        };
        return {ok: true, status: 200, body: table[url]};
      };
      const net = new BHydraNet.Network({nodes: ["http://tall", "http://heavy"],
                                         fetchJson: fake});
      net.survey().then((report) => console.log(JSON.stringify(
        {best: report.best, height: report.height, work: report.work,
         behind: report.behind})));
    """)
    assert result["best"] == "http://heavy"
    assert result["work"] == 999
    assert result["behind"] == 1              # более длинный, но лёгкий — отстал


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_node_from_another_network_is_ignored(tmp_path):
    """Узел с чужим генезисом не должен участвовать ни в выборе, ни в запросах.

    Своей считается сеть большинства ответивших: иначе один подставной узел
    переопределил бы её для клиента.
    """
    result = _run_js(tmp_path, """
      const fake = async (url) => ({ok: true, status: 200, body: {
        "http://a/api/info": {height: 5, total_work: 50, genesis: "ours"},
        "http://b/api/info": {height: 6, total_work: 60, genesis: "ours"},
        "http://evil/api/info": {height: 999, total_work: 10 ** 9, genesis: "alien"},
      }[url]});
      const net = new BHydraNet.Network(
        {nodes: ["http://a", "http://b", "http://evil"], fetchJson: fake});
      net.survey().then((report) => console.log(JSON.stringify({
        best: report.best,
        foreign: report.nodes.filter((n) => n.foreign).map((n) => n.url),
        order: net.order(),
      })));
    """)
    assert result["best"] == "http://b"        # чужак не выиграл, хоть и «тяжелее»
    assert result["foreign"] == ["http://evil"]
    assert "http://evil" not in result["order"]


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_request_falls_over_to_the_next_node(tmp_path):
    """Отказ узла — не отказ кошелька: запрос уходит следующему."""
    result = _run_js(tmp_path, """
      const tried = [];
      const fake = async (url) => {
        tried.push(url);
        if (url.startsWith("http://dead")) throw new Error("нет связи");
        if (url.endsWith("/api/info")) return {ok: true, status: 200,
          body: {height: 3, total_work: 30, genesis: "g"}};
        return {ok: true, status: 200, body: {answer: "живой"}};
      };
      const net = new BHydraNet.Network({nodes: ["http://dead", "http://alive"],
                                         fetchJson: fake});
      (async () => {
        await net.survey();
        const got = await net.get("/api/mempool");
        console.log(JSON.stringify({ok: got.ok, node: got.node,
                                    body: got.body, tried}));
      })();
    """)
    assert result["ok"] is True
    assert result["node"] == "http://alive"


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_client_error_is_not_retried_on_other_nodes(tmp_path):
    """404 — это ответ по существу, а не поломка узла.

    Перебирать из-за него остальные узлы бессмысленно: они ответят так же, а
    пользователь ждёт лишние таймауты.
    """
    result = _run_js(tmp_path, """
      let calls = 0;
      const fake = async (url) => {
        if (url.endsWith("/api/info")) return {ok: true, status: 200,
          body: {height: 1, total_work: 10, genesis: "g"}};
        calls++;
        return {ok: false, status: 404, body: {error: "нет такой транзакции"}};
      };
      const net = new BHydraNet.Network({nodes: ["http://a", "http://b"],
                                         fetchJson: fake});
      (async () => {
        await net.survey();
        const got = await net.get("/api/tx/deadbeef");
        console.log(JSON.stringify({ok: got.ok, status: got.status, calls}));
      })();
    """)
    assert result["ok"] is False and result["status"] == 404
    assert result["calls"] == 1               # второй узел не опрашивали


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_node_address_is_normalised(tmp_path):
    """«192.168.0.10:8000» — тоже адрес узла: человек так и напишет."""
    result = _run_js(tmp_path, """
      console.log(JSON.stringify([
        BHydraNet.normalise("192.168.0.10:8000"),
        BHydraNet.normalise("https://node.example/"),
        BHydraNet.normalise("  http://x:1/  "),
        BHydraNet.normalise("   "),
      ]));
    """)
    assert result == ["http://192.168.0.10:8000", "https://node.example",
                      "http://x:1", ""]


# --- SPV ----------------------------------------------------------------------
@pytest.mark.skipif(NODE is None, reason="нет node")
def test_browser_merkle_proof_matches_python(tmp_path):
    """Проверка доказательства в браузере совпадает с Python на НАСТОЯЩИХ данных.

    Проверяется и то, что подделанный путь отвергается: иначе «проверка» была
    бы украшением.
    """
    node = BHydraNode(difficulty=1)
    miner = generate_wallet()
    node.mine_pending(miner.address)
    tx = node.create_transaction(miner, generate_wallet().address, 5, fee=0.1)
    node.add_transaction(tx)
    node.mine_pending(miner.address)
    proof = node.merkle_proof(tx.txid)
    assert proof and verify_proof(bytes.fromhex(proof["leaf"]),
                                  proof["proof"], proof["merkle_root"])
    assert proof["proof"], "нужен блок с несколькими транзакциями"

    tampered = json.loads(json.dumps(proof))
    first = tampered["proof"][0]["hash"]
    tampered["proof"][0]["hash"] = ("00" if first[:2] != "00" else "11") + first[2:]

    result = _run_js(tmp_path, f"""
      const net = new BHydraNet.Network({{nodes: []}});
      const good = {json.dumps(proof)};
      const bad = {json.dumps(tampered)};
      console.log(JSON.stringify({{
        good: net.verifyProof(good.leaf, good.proof, good.merkle_root),
        bad: net.verifyProof(bad.leaf, bad.proof, bad.merkle_root),
        wrongRoot: net.verifyProof(good.leaf, good.proof, "00".repeat(64)),
        missingPath: net.verifyProof(good.leaf, undefined, good.merkle_root),
      }}));
    """)
    assert result["good"] is True
    assert result["bad"] is False
    assert result["wrongRoot"] is False
    # Пропавший путь обязан быть отказом, а не «нулём шагов».
    assert result["missingPath"] is False


@pytest.mark.skipif(NODE is None, reason="нет node")
def test_odd_hex_in_a_proof_is_refused(tmp_path):
    """Hex нечётной длины — отказ, а не «отбросим последний символ».

    Молчаливое усечение дало бы другой хеш, и проверка провалилась бы с
    невнятным «не сошлось» вместо честной ошибки разбора.
    """
    result = _run_js(tmp_path, """
      let refused = false;
      try { BHydraNet.fromHex("abc"); } catch (e) { refused = true; }
      console.log(JSON.stringify({refused}));
    """)
    assert result["refused"] is True


# --- Кошелёк на устройстве -----------------------------------------------------
def test_wallet_derives_the_address_from_the_key():
    """Регрессия. Раньше newWallet() генерировал ДВЕ независимые случайные
    строки — адрес отдельно, ключ отдельно. Деньги, присланные на такой адрес,
    нельзя было потратить никогда: ключа от него не существовало."""
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert 'wallet={address:"BHY"+rstr(34,B58), key:rstr(64,HEX)' not in page
    assert "BHydra.walletFromPrivateKey(key).address" in page


def test_wallet_key_is_generated_by_a_crypto_rng():
    """Math.random() для приватного ключа — это чужие деньги."""
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert "crypto.getRandomValues" in page
    assert "function randomKey" in page


def test_wallet_persists_the_key():
    """Кошелёк, забывающий ключ при закрытии, бесполезен на телефоне."""
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert "function saveWallet" in page and "function loadWallet" in page
    assert "localStorage" in page
    # И даёт его удалить — с подтверждением, потому что это необратимо.
    assert "function forgetWallet" in page and "confirm(" in page


def test_wallet_talks_to_the_network_not_to_one_node():
    page = open(WALLET_HTML, encoding="utf-8").read()
    assert "bhydra-net.js" in page and "BHydraNet.Network" in page
    # Прямых обращений к одному узлу остаться не должно.
    assert "fetch('/api" not in page


# --- Живая проверка в браузере -------------------------------------------------
def _browser_path():
    for candidate in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
                      "/opt/pw-browsers/chromium/chrome-linux/chrome"):
        if os.path.exists(candidate):
            return candidate
    return None


@pytest.mark.skipif(_browser_path() is None, reason="нет браузера для playwright")
def test_wallet_survives_restart_and_picks_the_best_node(tmp_path):
    """Сквозная проверка в настоящем браузере: адрес выводится из ключа,
    переживает перезагрузку, и кошелёк уходит на самый тяжёлый узел."""
    playwright = pytest.importorskip("playwright.sync_api",
                                     reason="playwright не установлен")
    port = _free_port()
    server = make_server("127.0.0.1", port, str(tmp_path / "chain.json"),
                         difficulty=2)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with playwright.sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=_browser_path())
            context = browser.new_context(**pw.devices["Pixel 5"])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/wallet", wait_until="networkidle")
            assert errors == []

            # `networkidle` говорит только про запросы: скрипт страницы под
            # нагрузкой мог ещё не выполниться. Ждём то, чем пользуемся.
            page.wait_for_function("() => typeof syncNetwork === 'function'")

            # Адрес обязан соответствовать ключу.
            assert page.evaluate(
                "() => BHydra.walletFromPrivateKey(wallet.key).address"
                "      === wallet.address") is True

            before = page.evaluate("wallet.address")
            page.reload(wait_until="networkidle")
            page.wait_for_function("() => wallet && wallet.address")
            assert page.evaluate("wallet.address") == before

            report = page.evaluate("async () => await syncNetwork()")
            assert report["reachable"] == 1
            assert report["best"] == f"http://127.0.0.1:{port}"
            browser.close()
    finally:
        server.shutdown()
