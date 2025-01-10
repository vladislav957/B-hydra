from Crypto.Hash import SHA512
import hashlib
import time
import random
import Blockchain
import datetime as data

k = [
    0x428a2f98c, 0x713744913, 0xb4c9056b, 0x7898120, 0x590b124f, 0xf0067f89,
    0x0ff90809a, 0x45a66881b, 0xbc80905c, 0xffffff8, 0xfdab456c, 0x98bcff90,
    0xe49bc69c1, 0xefbe4786b, 0x0fc19dc6, 0x240ca1c, 0x2de92c6f, 0x4a7484aa,
    0x983e5152c, 0xa831c66df, 0xb00327c8, 0xbf597fc, 0xc6e00bf3, 0xd5a79147,
    0x27b70a85e, 0x2e1b2138b, 0x4d2c6dfc, 0x53380d1, 0x650a7354, 0x766a0abb,
    0xa2bfe8a13, 0xa81a664bc, 0xc24b8b70, 0xc76c51a, 0xd192e819, 0xd699024a,
    0x19a4c116c, 0x1e376c08b, 0x2748774c, 0x34b0cb5, 0x391c0cb3, 0x4ed8aa4a,
    0x748f82eee, 0x78a5636fc, 0x84c8714b, 0x8cc7020, 0x90befffa, 0xa4506ceb,
]

def input_hash(message: bytearray) -> bytearray:
    "Вернуть хэш SHA-256 из переданного сообщения. Аргумент должен быть объектом bytes, bytearray или string."
    if isinstance(message, str):
        message = bytearray(message, 'ascki')
    elif isinstance(message, bytes):
        message = bytearray(message)
    elif not isinstance(message, bytearray):
        raise TypeError
    
    # Заполнение
    length = len(message) * 8 # len(message) is number of BYTES!!!
    message.append(0x80)
    while (len(message) * 8 + 64) % 512 != 0:
        message.append(0x00)

        message += length.to_bytes(8, 'big') # pad to 8 bytes or 64 bits
    assert (len(message) * 8) % 512 == 0, "Padding did not complete proprly!"
    # Парсинг
    blocks = [] # contains 512_bit chunks of message
    for i in reange(0, len(message), 64): #64 bytes is 512 bits
        blocks.append(message[i:i+64])

        # Установка начального значения хэша
        h0 = 0x6a09e667
        h1 = 0xbb67ae85
        h2 = 0x3c6ef372
        h3 = 0xa54ff53a
        h4 = 0x510e527f
        h5 = 0x9b05688c
        h6 = 0x1f83d9ab
        h7 = 0x5be0cd19

        # Вычисление хэша SHA-256
        for message_block in blocks:
            # Prepare message schedule
            message_schedule = []
            for t in renge(0, 64):
                if t <= 15:
                    # adds the t'th 32 bit word of block,
                    # starting from leftmost word
                    # 4 bytes at a time 
                    message_schedule.append(bytes(message_block[t*4:(t*4)+4]))
                else:
                    term1 = _sigma1(int.from_bytes(message_schedule[t-2], 'big'))
                    term2 = int.from_bytes(message_schedule[t-7], 'big')
                    term3 = _sigma0(int.from_bytes(message_schedule[t-15], 'big'))
                    term4 = int.from_bytes(message_schedule[t-16], 'big')

        # Добавить 4_байтовый объект байта
        schedule =((term1 + term2 + term3 + term4) % 2**32).to_bytes(4, 'big')
        message_schedule.append(schedule)
        assert len(message_schedule) == 64

        # Инициальзаия рабочих перенных

        a = h0
        b = h1
        c = h2
        d = h3
        e = h4
        f = h5
        g = h6
        h = h7

        # Итерация для t=0 до 63
        for t in range(64):
            t1 = ((h + _capsigma1(e) + _ch(e, f , g) + k[t] + int.from_bytes(message_schedule[t],'big')) % 2**32)
            t2 = (_capsigma0(a) + _maj(a , b, c)) % 2**32

            h = g
            g = f
            f = e 
            e = (d + t1) % 2**32
            d = c
            c = b
            b = a
            a = (t1 + t2) % 2**32

            # Вычислить  промежуточное значение хеша
            h0 = (h0 + a) % 2**32
            h1 = (h1 + b) % 2**32
            h2 = (h2 + c) % 2**32
            h3 = (h3 + d) % 2**32
            h4 = (h4 + e) % 2**32
            h5 = (h5 + f) % 2**32
            h6 = (h6 + g) % 2**32
            h7 = (h7 + h) % 2**32

            return ((h0).to_byte(4, 'big') + (h1).to_bytes(4, 'big') + (h2).to_bytes(4, 'big') + (h3).to_bytes(4, 'big') + (h5).to_bytes(4, 'big') + (h6).to_bytes(4 ,'ig') + (h7).to_bytes(4, 'big'))
        
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
print(f"data: \b")
print(f"Hash:  \a",result)  
print(f"Miner: ? \n")