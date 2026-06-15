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

from Blockchain import Blockchain

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

Параметры сети B-hydra: хеш SHA-512, консенсус Proof-of-Work, награда 50 BHY,
интервал халвинга 310 000 блоков, максимальная эмиссия 31 000 000 BHY.

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

from Blockchain import Blockchain

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

B-hydra network parameters: SHA-512 hashing, Proof-of-Work consensus, 50 BHY
reward, halving interval 310,000 blocks, maximum supply 31,000,000 BHY.

Future plans: Adding a command line interface for the management system. Implementation of the automatic complexity adjustment function. Improving performance through multi-precision. Integration with other payment services.
 ---
[B-hydra.docx](https://github.com/user-attachments/files/19970757/B-hydra.docx)

[B-hydra.pdf](https://github.com/user-attachments/files/20148653/B-hydra.pdf)

 ---
Contacts: If you have any questions or suggestions, please contact me via GitHub Issues or write to: Kovtunvladislav96@gmail.com killnetvladislav@outlook.com
