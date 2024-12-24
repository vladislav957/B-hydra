from Crypto.Hash import SHA512
import hashlib
import time
import random
import Blockchain
import datetime as data


# Комбинируем текущий момнт времени и случайное значение
input_data = str(time.time()) + str(random.randint(0, 31000000))

# Xешируем комбинировованные данные
result = hashlib.sha512(input_data.encode('UTF-8')).hexdigest()
previous_hash = '000.000.000.001'

# Пример строки
data ='{[]}'

# Создаем объект хеширования и обновляем его данными
sha512_hash = SHA512.new(data.encode('utf-8'))

# Получаем хеш в виде шестнадцатеричной строки
hash_hex = sha512_hash.hexdigest()
print("has been address to the blockhain!")
print(f"B-hydra Block: #\b ")# has been address to the blockhain!")
print(f"Hash:  \a",result)  
print(f"Miner: ? \n")


