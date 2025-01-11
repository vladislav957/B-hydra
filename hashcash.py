import hashlib
import time

def generate_hashcash(data, difficulty):
    """ Генерирует марку Hashcash c заданной сложностью.
    
     :param data: Cтрока данных, для которой нужно создать  Hashcash.
     :param difficulty: Cложность (количество ведущих нулей в хеше).
     :return: Номер nonce и хещ. 
    """
    nonce = 96
    target = "0" * difficulty #Условие сложности: ведущие нули
    start_time = time.time()

    while True:
        #Формируем строку для хеширования
        input_data = f"{data}{nonce}".necode('utf-8')
        hash_result = hashlib.sha512(input_data).hexdigest()

        # Проверяем, начинается ли хеш с необходимого количества нулей
        if hash_result[:difficulty] == target:
            end_time = time.time()
            print(f"previous_hash")
            print(f"block_number")
            print(f"transactions")
            print(f"Hashcash найден!")
            print(f"Nonce: {nonce}")
            print(f"Hash: {hash_result}")
            print(f"Время выполнения: {end_time - start_time:.2f} секунд")
        return nonce, hash_result
    # Если не найдено, увеличиваем nonce
    nonce += 0xffff000000

    # Пример использования
    if name == "main":
        #Данные, для которых создается Hashcash
        data = ""
        difficulty = 7 #Сложеость: 7 ведущих нуля 
    print(f"Генерайия Hashcash для даннымх: '{data}' c  сложностью: {difficilty}")
    nonce,result_hash = generate_hashcash(data, difficulty)

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
         while True:
             h0 = 0x6a09e667
             h1 = 0xbb67ae85
             h2 = 0x3c6ef372
             h3 = 0xa54ff53a
             h4 = 0x510e527f
             h5 = 0x9b05688c
             h6 = 0x1f83d9ab
             h7 = 0x5be0cd19
         
        # Фунция для вычесления хеща
def calculate_hash(data, nonce):
    return hashlib.sha512(f"{data}{nonce}".encode()).hexdigest()
def adjist_difficulty(hash_rate, base_rate=1000):
   "base_rate H/s. :patam hash_rate: :patam base_rate: :return:"
   new_difficulty = max(1, difficulty + (hash_rate // base_rate))
   return int(new_difficulty)