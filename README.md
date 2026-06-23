# B-hydra- это одноранговая электронная кассовая система P2P.

 ---

Ключевые особенности :
Децентрализованная архитектура : Платформа работает без центрального сервера, обеспечивает полную независимость.
Простота использования : Интуитивно понятный интерфейс и легкая связь с другими группами.
Надёжность и безопасность : Испол
Масштабируемость : Возможность расширения сети для работ с указанными объемами данных.

![Снимок экрана (39)](https://github.com/user-attachments/assets/096c8c4e-2ddb-45cd-b01d-b5c89ee1980c)
![Снимок экрана (40)](https://github.com/user-attachments/assets/5daa4e3d-bff6-4995-b1f3-77bda0f0cbcf)

 ---

 Установка
Как установить и запустить :
Требования:
Python 3.9 или выше
Установленные в зависимости (см. requirements.txt)
Шаги:
Клонируйте репозитории


Шаги:

1.Клонируйте репозитории:

git clone https://github.com/vladislav957/B-hydra.git
cd B-hydra

2.Установите в зависимости:

pip install -r requirements.txt

3.Запустите проект:

python maing.py или maing.py

4.Основной алгоритом SHA-512

Установленные в зависимости на Linux:

Шаги:

1.Клонируйте

 git clone
https://github.com/vladislav957/B-hydra.git
cd B-hydra

Пример использования :
После запуска программы вы можете начать майнинг или создать транзакции.

Быстрый старт (полная демонстрация жизненного цикла):

    python manig.py

Пример кода для добавления блока:

from b_hydra import Blockchain

blockchain = Blockchain()
blockchain.add_block(data="Пример транзакции")
print(blockchain.chain)

Командная строка (CLI) — состояние хранится в bhydra_chain.json:

    python cli.py wallet                       # создать кошелёк
    python cli.py init                         # инициализировать цепочку
    python cli.py mine <АДРЕС_МАЙНЕРА>         # добыть блок (награда 50 BHY)
    python cli.py send <ПРИВ_КЛЮЧ> <АДРЕС> 10 --fee 0.5   # перевод
    python cli.py mine <АДРЕС_МАЙНЕРА>         # подтвердить транзакцию в блоке
    python cli.py balance <АДРЕС>             # проверить баланс
    python cli.py chain                        # показать цепочку

Параметры сети B-hydra: хеш SHA-512, консенсус Proof-of-Work, модель UTXO
(транзакции со входами и выходами, как в Bitcoin), награда 50 BHY,
интервал халвинга 310 000 блоков, максимальная эмиссия 31 000 000 BHY.
Пропорциональная сложность: PoW устроен как у Bitcoin — хеш блока (как 512-битное
число) должен быть не больше порога target. Чем больше разных майнеров в сети,
тем меньше target и тем труднее найти блок (чем меньше майнеров — тем проще).
Зависимость строго пропорциональна: требуемая работа = генезис × число_майнеров.
Порог каждого блока записан в его заголовок и проверяется всеми узлами.
Эмиссия как у Bitcoin: за блок майнер получает строго 50 BHY, и каждые
310 000 блоков награда делится пополам — 50 → 25 → 12.5 … Потолок
31 000 000 BHY; майнеры получают награду примерно до 3000 года.

Структура проекта — ядро оформлено как Python-пакет b_hydra/:

    b_hydra/
      hashing.py / sha2.py   — хеши SHA-512 (обёртки) и SHA-2 «с нуля»
      merkle.py, hashcash.py — дерево Меркла и proof-of-work
      wallet.py              — ключи ECDSA secp256k1 и адреса
      transaction.py         — UTXO-транзакции (входы/выходы), мемпул
      blockchain.py          — блоки, PoW, динамическая сложность
      economics.py           — награда, халвинг, конец эмиссии (3000)
      node.py                — узел: UTXO, балансы, майнинг, синхронизация
      p2p.py, tcp.py         — одноранговая сеть
      api.py, mobile_client.py — REST API и мобильный кошелёк
      contract.py            — смарт-контракт и эскроу
    cli.py, api.py, P2P.py, manig.py — точки входа (запускалки)
    explorer.html            — веб-обозреватель блоков
    tests/                   — автотесты (pytest)

Импорт из пакета:

    from b_hydra import Blockchain, BHydraNode, Wallet, Transaction

Полная карта системы (слои, консенсус, P2P-протокол, модель безопасности) —
в ARCHITECTURE.md.

Хеширование: по умолчанию весь проект использует реализацию SHA-256/512
«с нуля» (b_hydra/sha2.py, без hashlib). Она применяется на всех уровнях —
майнинг, дерево Меркла, txid, адреса. Значения хешей идентичны hashlib, меняется
только скорость (чистый Python заметно медленнее). Вернуть быстрый движок:

    BHYDRA_PURE_SHA=0 python manig.py        # через окружение
    # или в коде:
    from b_hydra import hashing
    hashing.use_pure_sha(False)

Мобильный кошелёк (REST API) + веб-обозреватель блоков — узел отдаёт JSON по
HTTP и страницу обозревателя; телефон подписывает транзакции локально
(приватный ключ не покидает устройство):

    python api.py --port 8000
    # http://<IP>:8000/         — обозреватель блоков (блоки, транзакции, адреса)
    # http://<IP>:8000/api/info — REST API

Эндпоинты и схема подписи описаны в API.md, эталонный клиент — mobile_client.py.

Десктоп-приложение (tkinter) — единое окно с кошельком, майнингом и сетью:

    python bhydra_gui.py
    # вкладки: 💼 Кошелёк · ⛏ Майнинг · 🌐 Сеть
    # (нужен tkinter: на Windows/macOS встроен, на Linux — пакет python3-tk)

P2P-сеть — несколько узлов синхронизируются между собой (рассылка блоков и
транзакций, правило самой длинной валидной цепочки):

    python P2P.py --port 5101                       # первый узел
    python P2P.py --port 5102 --peer 127.0.0.1:5101 # подключается к первому
    python P2P.py --demo                            # демо из трёх узлов

Автотесты (63 теста: кошелёк, транзакции, блокчейн, узел, экономика, сложность,
P2P, API):

    pip install pytest
    pytest

Планы на будущее :
Добавление интерфейса командной строки для системы управления.
Реализация функции автоматической настройки сложности.
Улучшение производительности за счет многоточности.
Интеграция с другими платёжными жизнью.
 ---
[B-hydra.docx](https://github.com/user-attachments/files/19970749/B-hydra.docx)

[B-hydra.pdf](https://github.com/user-attachments/files/20148652/B-hydra.pdf)

 ---
Контакты:
Если у вас есть вопросы или предложения, свяжитесь со мной через GitHub Issues или напишите на: Kovtunvladislav96@gmail.com killnetvladislav@outlook.com

# B-hydra is a peer-to-peer electronic cash register system P2P.

 ---

Key Features: Decentralized Architecture: The platform operates without a central server, ensuring complete independence. Ease of Use: Intuitive interface and easy communication with other groups. Reliability and Security: Execution Scalability: The ability to expand the network to work with specified data volumes.

![Снимок экрана (39)](https://github.com/user-attachments/assets/4f05102c-824f-42a4-b1f4-73bfbae0db3a)
![Снимок экрана (40)](https://github.com/user-attachments/assets/2f56edec-3b73-435f-8394-1870d88855ae)

Installation
How to install and run : Requirements: Python 3.9 or higher Installed in dependencies (see requirements.txt) Steps: Clone the repositories

 ---

Steps:

1.Clone the repositories:

git clone https://github.com/vladislav957/B-hydra.git
cd B-hydra

2.Set depending on:

pip install -r requirements.txt

3.Run the project:

python maing.py or maing.py

4.Basic algorithm SHA-512

Installed dependencies on Linux:

Steps:

1.Clone the repositories:

git clone
https://github.com/vladislav957/B-hydra.git
cd B-hydra

Example of usage: After running the program, you can start mining or create transactions.

Quick start (full lifecycle demo):

    python manig.py

Example code for adding a block:

from b_hydra import Blockchain

blockchain = Blockchain()
blockchain.add_block(data="Пример транзакции")
print(blockchain.chain)

Command line (CLI) — state is stored in bhydra_chain.json:

    python cli.py wallet                       # create a wallet
    python cli.py init                         # initialise the chain
    python cli.py mine <MINER_ADDRESS>         # mine a block (50 BHY reward)
    python cli.py send <PRIV_KEY> <ADDRESS> 10 --fee 0.5   # transfer
    python cli.py mine <MINER_ADDRESS>         # confirm the tx in a block
    python cli.py balance <ADDRESS>           # check balance
    python cli.py chain                        # show the chain

B-hydra network parameters: SHA-512 hashing, Proof-of-Work consensus, UTXO
model (transactions with inputs and outputs, like Bitcoin), 50 BHY reward,
halving interval 310,000 blocks, maximum supply 31,000,000 BHY.

Future plans: Adding a command line interface for the management system. Implementation of the automatic complexity adjustment function. Improving performance through multi-precision. Integration with other payment services.
 ---
[B-hydra.docx](https://github.com/user-attachments/files/19970757/B-hydra.docx)

[B-hydra.pdf](https://github.com/user-attachments/files/20148653/B-hydra.pdf)

 ---
Contacts: If you have any questions or suggestions, please contact me via GitHub Issues or write to: Kovtunvladislav96@gmail.com killnetvladislav@outlook.com
