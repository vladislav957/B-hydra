# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# B-hydra Core

Учебная P2P-криптовалюта на модели UTXO. Ядро — чистый Python 3.9+ (стандартная
библиотека, ноль обязательных зависимостей); пакеты из `requirements.txt` — только
опциональные ускорители/утилиты (`coincurve`, `qrcode`, `pyinstaller`, …).

## Команды

```bash
pip install pytest                            # единственная dev-зависимость
python -m pytest -q                           # 200 тестов — держать зелёными
python -m pytest tests/test_node.py -q        # один файл
python -m pytest tests/test_node.py -k mempool -q   # один тест по имени

python -m b_hydra.cli wallet                  # создать кошелёк (адрес + приватный ключ)
python -m b_hydra.cli mine BHY<адрес>         # добыть блок (награда майнеру)
python -m b_hydra.cli send <ключ> <адрес> 10 --fee 0.5
python -m b_hydra.cli balance BHY<адрес>      # состояние CLI — bhydra_chain.json
python -m b_hydra.api                         # веб-сервер: http://0.0.0.0:8000
python bhydra_gui.py                          # десктоп-клиент (tkinter)
python P2P.py --demo                          # демо-сеть из трёх узлов
```

Линтера/форматтера в проекте нет. Сборка `.exe` — `pyinstaller`, см. `BUILD.md`.

Веб: `/` — обозреватель «живой сети» (`explorer.html`), `/wallet` — кошелёк
(`wallet.html`). Обе страницы работают и с API узла, и в демо-режиме офлайн.

## Архитектура (пакет `b_hydra/`)

| Модуль | Назначение |
|---|---|
| `blockchain.py` | цепочка, блоки, PoW-валидация, ретаргет, halving, `total_work` |
| `transaction.py` | UTXO: `TxInput`/`TxOutput`/`Transaction`, `TransactionPool` (мемпул) |
| `wallet.py` | ECDSA secp256k1 (свой на Python + опц. нативный бэкенд), адреса |
| `keystore.py` | шифрование приватного ключа паролем (KDF+CTR+HMAC на SHA-512, hashlib) |
| `pqcrypto.py` | пост-квантовые хеш-подписи: Lamport, WOTS, XMSS-lite, `QuantumWallet` (экспериментально) |
| `hashing.py`, `sha2.py` | SHA-256/512 с нуля + подключаемый бэкенд |
| `hashcash.py`, `economics.py` | proof-of-work, награда/эмиссия/halving |
| `merkle.py`, `qrcode_gen.py` | дерево Меркла (+ SPV-доказательства), QR с нуля |
| `contract.py` | `ContractManager`: эскроу и смарт-чеки НА ЦЕПОЧКЕ (+ учебные in-memory классы) |
| `node.py` | узел: блокчейн + мемпул + инкрементальные кэши UTXO/tx-индекса, майнинг, переводы |
| `blockdag.py` | blockDAG (GHOSTDAG-lite) + гибрид «DAG→PoW→линейная цепь» (экспериментально) |
| `p2p.py`, `tcp.py` | gossip-сеть, обмен пирами, фрейминг сообщений |
| `api.py`, `cli.py`, `gui.py`, `mobile_client.py` | REST/HTTP, CLI, tkinter-GUI, моб. клиент |

Корневые `*.py` (`cli.py`, `api.py`, `P2P.py`, `cache.py`, `IP.py`, …) — тонкие
запускалки/шимы поверх пакета; править логику нужно в `b_hydra/`. Тесты —
`tests/` (13 файлов, 107 тестов, `pytest`). Полная карта слоёв — `ARCHITECTURE.md`,
схема REST-подписи — `API.md`.

## REST API (`b_hydra/api.py`)

`GET /api/info | /api/chain | /api/block/<i> | /api/tx/<txid> | /api/proof/<txid>`
`| /api/address/<a> | /api/addresses?limit=N (rich list) | /api/balance/<a>`
`| /api/utxos/<a> | /api/mempool`
`POST /api/mine {miner} | /api/transaction {подписанная tx} | /api/send`
`{private_key,to,amount,fee} | /api/wallet {private_key}→адрес+баланс`.

Смарт-контракты: `GET /api/contract | /api/contract/escrow/<id>`
`| /api/contract/cheque/<id>`; `POST /api/contract/escrow (open) |`
`/api/contract/escrow/confirm | /api/contract/escrow/cancel |`
`/api/contract/cheque (write, возвращает секрет ОДИН раз) |`
`/api/contract/cheque/cash {cheque_id,secret,to} | /api/contract/cheque/refund`.

⚠️ `/api/send`, `/api/wallet` и контрактные POST принимают приватный ключ —
это для СВОЕГО локального узла. Для чужого узла нужна подпись на устройстве.

## Ключевые факты и подводные камни

- **Адрес**: `"BHY" + base58(0x1f || ripemd160(sha512(pub)) || double_sha512(payload)[:4])`,
  публичный ключ несжатый `0x04||X||Y`.
- **Подпись/txid** считаются от `signing_payload()` =
  `json.dumps({chain_id, vin:[{txid,index}], vout, timestamp}, sort_keys=True, ensure_ascii=False)`.
  `z = int(sha512(payload)[:32])`, подпись `r||s` (low-s).
  ⚠️ **Формат не языконезависимый**: Python пишет целые суммы как `10.0`, а JS —
  как `10`. Поэтому подписать транзакцию прямо в браузере «в лоб» нельзя — хеш
  не сойдётся. Настоящая браузерная подпись требует канонического формата.
- **Консенсус**: PoW (SHA-512, хеш ≤ target), выбор цепочки по `total_work`
  (не длине), ретаргет каждые `RETARGET_INTERVAL=100` блоков, halving каждые
  310 000 блоков (50 → 25 → …), `MAX_SUPPLY=31_000_000`, `chain_id` — защита
  от replay. Это **линейная цепочка**, не DAG.
- **Хеширование**: по умолчанию везде работает SHA-256/512 «с нуля»
  (`sha2.py`, `BHYDRA_PURE_SHA=1`) — байт-в-байт совпадает с `hashlib`, но
  заметно медленнее. Быстрый движок: `BHYDRA_PURE_SHA=0` или
  `hashing.use_pure_sha(False)`.
- **Мемпул** вмещает 10 000+ **связанных** транзакций (кэш «эффективного» UTXO —
  подтверждённые + мемпул — позволяет тратить неподтверждённую сдачу),
  `max_size=50000`; `mine_pending` берёт до `MAX_BLOCK_TRANSACTIONS-1` (4999).
- **Смарт-контракты** (`ContractManager`) НЕ меняют консенсус: у менеджера свой
  контрактный кошелёк, депозит/выплата/возврат — обычные UTXO-транзакции.
  Конвенция комиссий: плательщик тратит `amount + 2·fee` (депозит + выплата).
  Чек подписан ключом плательщика (`verify_cheque` — офлайн-проверка); секрет
  выдаётся один раз, хранится только его SHA-512. Состояние контрактов API
  сохраняет в `<state>.contracts` (там приватный ключ контракта — не терять).
- **Скорость подписи**: `Wallet.verify` использует нативный `coincurve`
  (libsecp256k1), если он установлен и прошёл self-test на байт-совместимость
  (иначе чистый Python). Ускорение проверки ~55× (20 мс → 0.36 мс). Активный
  бэкенд — `wallet._BACKEND`.

## Соглашения

- Разработка на ветке `claude/*` (для сессии её задаёт задание), PR в `main`;
  напрямую в `main` не пушить.
- **Дерево Меркла** — единая реализация в `merkle.py` (её же использует
  `blockchain.py`; корень байт-в-байт как раньше). Умеет доказательства
  включения (SPV): `merkle_proof`/`verify_proof`, `node.merkle_proof(txid)`,
  REST `GET /api/proof/<txid>`. CVE-2012-2459 закрыт запретом дублей txid в
  блоке (`has_duplicate_promotion` помечает дерево с дублированием).
- **Пост-квантовая крипта** (`pqcrypto.py`, экспериментально, НЕ в консенсусе):
  хеш-подписи на нашем SHA — Lamport (OTS), WOTS (Winternitz OTS + контрольная
  сумма), XMSS-lite (дерево Меркла над WOTS-ключами → многоразовая подпись,
  переиспользует `merkle.py`), `QuantumWallet` (адрес `BHYQ…`). Квантово-
  устойчивы: Шор ломает ECDSA, но не хеши; Гровер лишь вдвое ослабляет.
  Два режима (оба наших хеша): `P256` (элементы SHA-256, 128 бит, по умолчанию,
  компактно) и `P512`/`strong=True` (SHA-512, 256 бит даже после Гровера).
  Подписи с СОСТОЯНИЕМ (ключ WOTS одноразовый).
- **Гибридные квантово-защищённые кошельки** (`HybridWallet`, В КОНСЕНСУСЕ):
  адрес версии `0x2f` (тоже `BHY…`) привязан к ДВУМ ключам — ECDSA + XMSS-корню
  (`hybrid_address`, `is_hybrid_address`). Трата требует ОБЕ подписи
  (`tx.sign_hybrid`, `node.create_hybrid_transaction`); узел проверяет их в
  `_verify_input_auth` и ведёт учёт израсходованных одноразовых XMSS-ключей
  (`node.pq_used_indices`, накопитель `pq_used` в `validate_transaction` /
  `mine_pending` / `_validate_block_transactions` / `_validate_chain`).
  Квант ломает лишь ECDSA — монеты на гибридном адресе недоступны. Обычные
  ECDSA-кошельки (`0x1f`) работают как раньше (обратная совместимость).
- **Перед push — `python -m pytest -q` должно быть зелёным** (152/152).
- Коммиты по-русски, осмысленные; заканчиваются трейлерами
  `Co-Authored-By:` и `Claude-Session:`.
- Не хардкодить идентификатор модели в коде/коммитах/артефактах.

## Состояние и направления

Сделано (PR #1–#4 влиты в `main`): чинка багов ядра, мемпул на 10k+, живой
веб-обозреватель и кошелёк, настоящие переводы через узел с понятными ошибками,
нативный ECDSA-ускоритель, смарт-контракты на цепочке (эскроу + смарт-чеки,
REST-эндпоинты `/api/contract/...`).

Дальше (по приоритету пользователя — «сначала скорость, потом DAG»):
1. скорость — сделано: ECDSA-ускоритель, инкрементальный кэш UTXO
   (`utxo_set` применяет только новые блоки, реорг → пересборка; попутно
   tx-индекс O(1) и PQ-учёт цепочки) и LRU-кэш проверенных подписей
   (`_verify_ecdsa_cached`: мемпул→блок→прунинг не перепроверяют ECDSA;
   mine_pending со 100 tx: 1364→8 мс). Параллелизм не делаем: GIL не даёт
   выигрыша чистому Python, а нативный путь уже покрыт coincurve;
2. **blockDAG** (GHOSTDAG-lite) — СДЕЛАНО как отдельный модуль `blockdag.py`
   (не трогает линейную цепочку): блок ссылается на все вершины-tips (много
   родителей), k-кластерная раскраска синий/красный, blue_score вместо высоты,
   тотальный порядок. Плюс **гибрид** `HybridDagChain`: блоки накапливаются в
   DAG, а PoW-нонс (`Checkpoint.mine`) финализирует их в линейную контрольную
   точку и схлопывает DAG (новый якорь — хеш точки). Цепочка точек = линейный
   PoW-блокчейн из DAG-пачек. Улучшает блоки/с, крипту не ускоряет. Дальше:
   привязать реальные UTXO-транзакции и дедуп по синему набору.
3. настоящая браузерная подпись — требует канонической сериализации транзакции
   (одинаковой в Python и JS) + secp256k1 на JS.
