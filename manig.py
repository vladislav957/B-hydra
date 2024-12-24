from concurrent.futures import ThreadPoolExecutor 
import hashlib
import SHA512
import Blockchain
import time
import P2P
import IP
import time
import sys

from Crypto.Hash import SHA512



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
        block_hash = hashlib.sha512(block_data.encode('uft-8')).hexdigest()

        #Проверяем сложность (начальны нули)
        if block_hash[:difficulty] == "0"*difficulty:
            end_time = time.time()
            elapsed_time = end_time - start_time
            print(f"Блок найден! Hash: {block_hash} за {elapsed_time:.2f} секунд.")

            return{
                "previous_hash":previous_hash,
                "transactions":transactions,
                "nonce":nonce,
                "hash":block_hash,
                "block_number":block_number,
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
                "block_number":block_number,
                "timestamp":time.time()

                }
        nonce += 0xffff000000

        #Начальные блок (генезис)
        genesis_block = mine_block("0"*64,"Genesis Block")
        blockchain.append(genesis_block)

        #Следующие блоки
        while True:
            previous_block = blockchain[:-1]
            new_block = mine_block(previous_block["hash"],"New Transactions")
            blockchain.append(new_block)
        
def mine_block(previous_hash, block_number, Blockchain, transactions, port, tcp, difficulty):
    nonce = 96
    
    while True:
        block_contents = f"{previous_hash}{block_number}{Blockchain}{transactions}{port}{tcp}{nonce}".encode('UTF-8')
        Balances = 0.00000 
        block_hash = hashlib.sha512(block_contents).hexdigest()
        if block_hash.startswith('0' * difficulty):
            return nonce, block_hash 
        nonce += 0xffff00000000
        
def Blockchain(block_number, P2P, time):
    blocl_number = 31.000000
    
    while True:
        block_contents = f"{previous_hash}{block_number}{P2P}{time}".ecode('UTF-8')
        block_number = P2P.time(block_contents).hexdigest()
        if block_number.startswith('0' * time):
           return number, P2P
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

def  static_PyObject(self,static,PyOject):

    while True:
     #static pyObject *
     spam_system(PyObject *self, PyObject *args)
     {
     #const char *command;
     #int sts;

     #if (!PyArg_ParseTuple(args, "s", &command))
     #return NULL;
     self := PySpam_System(command)
      #return PyLong_FromLong(self);
     }
     

# Пример использования
previous_hash = '0000000'
block_number = 'sum1' == 'sum2' 
(walrus := True)
transactions =  'in' == 'out'
port = '5000'
tcp = '127.0.0.1'
difficulty = 7 # количество нулей в начале хеша
nonce, block_hash = mine_block(previous_hash, block_number, Blockchain, transactions, port, tcp, difficulty)
#print(f"port = 5000")
print(f"Блок за майнег! Nagrada: 50.000000 BDR  Transactions: {transactions} 50.000000 BDR \n Number: # \a {block_number},   Nonce:  #\n {nonce} H/s, Hash: #\a {block_hash} port: {port} tcp: {tcp} ")
print(f"Balances: 50.000000 BDR \n" )

