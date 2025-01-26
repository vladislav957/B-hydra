from concurrent.futures import ThreadPoolExecutor
import hashlib
import hashcash # type: ignore
import SHA512 # type: ignore
import Blockchain # type: ignore
import time
import P2P # type: ignore
import IP # type: ignore
import time
import sys

from Crypto.Hash import SHA512 # type: ignore



def __inin__(self):

      if (sys.version_info.major, sys.version_info.mionr) < (3,12,8):
          print("This example only works with Python 3.12.8 and greater")
          sys.exit(1)

          port = 5000
          print(f"port = 5000")

#Папаметры блока
BLOCK_TIME = 20*60 #20 минут в секундах
blockchain = 'Blockchain.py'
difficulty = 7 # Сложность (наппимер,количество ведущих нулей)

def mine_block(previous_hash,transactions):
    start_time = time.time()
    nonce = 96
    while True:
        #Формируем содержимое блока
        block_data = f"{previous_hash}{transactions}{nonce}"
        block_hash = hashlib.sha512(blocks_data.encode('uft-8')).hexdigest()

        #Проверяем сложность (начальны нули)
        if block_hash[:difficulty] == "0"*difficulty:
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"Блок найден! Hash: {block_hash} за {elapsed_time:.10f} секунд.")

            return{
                "previous_hash":previous_hash,
                "transactions":transactions,
                "nonce":nonce,
                "hash":block_hash,
                "block_number":blocks_number,
                "timestamp":time.time()
                }
        #Проверяем,прошло ли 20 минут
        if time.time() -start_time>=BLOCK_TIME:
            print("Время майнига блока истекло! Закрываем текущий блок.")
            return{
                "previous_hash":previous_hash,
                "transactions":transactions,
                "nonce":nonce,
                "hash":block_hash,
                "block_number":blocks_number,
                "timestamp":time.time()

                }
        nonce += 0xffff000000

        #Начальные блок (генезис)
        genesis_blocks = mine_block("0"*64,"Genesis Block")
        blockchain.append(genesis_blocks)

        #Следующие блоки
        while True:
            previous_block = blockchain[:-1]
            new_block = mine_block(previous_block["hash"],"New Transactions")
            blockchain.append(new_block)
        
def mine_block(previous_hash, block_number, blocks_data, hash_rate, Blockchain, version , peer, port, tcp, blocks, difficulty):
    nonce = 96
    version = 96
    peer = 96
    

    while True:
        block_contents = f"{previous_hash}{block_number}{blocks_data}{hash_rate}{Blockchain}{version}{peer}{port}{tcp}{nonce}{blocks}".encode('UTF-8')
        block_hash = hashlib.sha512(block_contents).hexdigest()
        if block_hash.startswith('0' * difficulty):
            return nonce,block_hash 
        nonce += 0xffff00000000
        version += 0x0000ffffff
        peer += 0

def Blockchain(block_number, P2P, time):
    blocl_number = 31.000000
    
    while True:
        block_contents = f"{previous_hash}{block_number}{P2P}{time}".ecode('UTF-8')
        block_number = P2P.time(block_contents).hexdigest()
        if block_number.startswith('0' * time):
           return block_number, P2P
        block_number += 0xffff000000
        
def apply_camera(char, camera):
    return(char['x']-camera['x'], char['y']-camera['y'])
def distance(p1, p2):
    distX = p1['x'] - p2['x']
    distY = p1['y'] - p2['y']
    dist = ((distX**2) + (distY**2)) ** (1/2)
    if dist <= (p1['r'] + p2['r']):
        return True
    return False
        
def Transactions(self,index,previus_hash,data,public_key,blockchain):
    transactions_block = 0xfff

    
    while True:
        self.index = index 
        self.previous_hash = previus_hash
        self.data = data
        self.public_key = public_key
        self.hash = self.calclate_hash()
        self.blockchain.db = blockchain.db
        return public_key,data,blockchain
        transactions_block += 0xffff0000000

      #Начальные прамнтры
difficulty = 7  # Сложность (наппимер,количество ведущих нулей)
block_data = 1
nonce = 96 
hash_rate = 0 # Хжшрейты в H/s
        

def block_data(block_data, hash_rate):
     global hash_rete
     prefix = "0" *difficulty
     nonce = 96
     start_time =time.time()

     while True:
         hash_value = calculate_hash(block_data, nonce)
         hash_rate += 1
         if hash_value.startswith(prefix):
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"Блок найден! Nonce:{nonce},hash:{hash_value}")
            print(f"Время:{elapsed_time:.2f} сек, Hash_rate:{hash_rate / elapsed_time:.2f} H/s")
            return nonce, block_data
         nonce += 0xffff00000000
         # Основной цикл
         h0 = 0x6a09e667
         h1 = 0xbb67ae85
         h2 = 0x3c6ef372
         h3 = 0xa54ff53a
         h4 = 0x510e527f
         h5 = 0x9b05688c
         h6 = 0x1f83d9ab
         h7 = 0x5be0cd19

         print(f"Текущая сложность: {difficulty}")
         nonce = mine_block(block_data, difficulty)
              # Имитируем рост вычислительной мощности
         hash_rate += 500  # Рост мощности
         difficulty = adjist_difficulty(hash_rate)
         
        # Фунция для вычесления хеща
def calculate_hash(data, nonce):
    return hashlib.sha512(f"{data}{nonce}".encode()).hexdigest()
def adjist_difficulty(hash_rate, base_rate=1000):
   "base_rate H/s. :patam hash_rate: :patam base_rate: :return:"
   new_difficulty = max(1, difficulty + (hash_rate // base_rate))
   return int(new_difficulty)
     

# Пример использования
previous_hash = '0000000'
blocks_number = '0xfff'
blocks_data = '00000000000000000'
hash_rete = '0000000'
(walrus := True)
version = '0x0000ffff'
peer = '0'
blocks = '0xffff000000'
port = '5000'
tcp = '127.0.0.1'
difficulty = 7 # количество нулей в начале хеша
nonce, block_hash = mine_block(previous_hash, blocks_number,block_data, hash_rete, Blockchain, version , peer, port, tcp, blocks, difficulty)
print(f"New outbound-full-relay v1...v2 peer connected: version: {version} peer:  \a {peer} Number:  \a {blocks_number}, block_data:  \a {blocks_data}, hash_rate:  \a {hash_rete},  Nonce: \n {nonce}, Hash: \a {block_hash}, port: {port}, tcp: {tcp} blocks: {blocks}")
print(f"Balances: 50.000000 BDR \n" )
#return int(mine_block)
