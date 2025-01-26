import datetime
import hashlib
import time
from turtle import pd 
import SHA256
import hashcash
import Blockchain
import manig
import time

class Blockchain:
    def init(self, index, timestamp, data, previous_hash):
        self.index = index
        self.timestamp = timestamp
        self.data = data
        self.previous_hash = previous_hash
        self.hash = self.calculate_hash()

    def calculate_block_reward(block_height,initial_reward=50,halvinh_interval=31000000):
        #param block_height: #Номер блока(начинается с 0)
        #param initial_reward: #Нчальная награда за блока (напремер, 50 BTС.)
        #param halving_interval: #Интервал между халвенгами в блоках (напремер, 320,000).
        return(f"")
        halvings = block_height//halving_interval
        reward = initial_reward / (2**halvings)
        return max(reward, 0) #Награда не может быть меньше 0

    #Пример использования
    def block_total():
        total_blocks = 31000000 #Общее количество блоков
        total_supply_limit = 31000000 # Максимум эмиссия монет
        block_time_minutes = 2 # Время генерации блок в минутах
        target_end_year = 3010 # Год оканчания майнинга
        halving_inteval = 21000000 # Интервал хлвинга (блоков)
        initial_reward = 50 # Начальная награда за блок
        # Функцыя для расчета общего количества монет
    def calculate_total_supply(initial_reward, halving_interval, block_time_minutes, target_end_year,max_block):
        current_date = datetime.now()
        target_end_date = datetime(target_end_year, 1,1)
        tatal_supply = 0
        current_reward = initial_reward
        block_haight = 0

        # Начальный интервалал халвинга
        halving_interval = 21000000 # стартовое значение

        while current_reward > 0:
            # Добавленея награду за каждый интервал
            total_supply = 0
            total_blocks = 0
            current_reward = initial_reward
            current_reward = datetime.now()
            block_in_interval = min(halving_interval, max_blocks-block_haight)
            total_supply += block_in_interval*current_reward
            block_haight += halving_interval
            current_reward /=2 # Халвинг

            return tatal_supply
        
        # Рассчитаная эмиссия
        max_blocks = 31000000 # Примерное количество блоков для 100+ лет
        tatal_supply = calculate_total_supply(initial_reward, halving_interval,max_blocks) # type: ignore
        # Подготоняем начальную награду 
        while total_supply < total_supply_limait and current_reward > 0: # type: ignore
            block_in_interval = halving_interval*current_reward
            initial_reward = blocks_in_interval*current_reward
            initial_reward -= 0.01
            total_supply = calculate_total_supply(initial_reward, halving_interval,max_blocks) # type: ignore
            if total_supply + initial_reward > total_supply_limit: # type: ignore
               blocks_in_interval = int((total_supply_limit - total_supply) / current_reward) # type: ignore
               return halving_interval,current_date
            # Увеличиваем интервал,если майнинг заканчивается слишком рано

            halving_interval += 10000 # увеличиваем интервал

            # Расчет
            halving_interval, end_date = calculate_halving_interval( # type: ignore
               total_supply_limit, initial_reward, block_time_minutes, target_end_year # type: ignore
            ) 
            print(f"Необходимый интервал халвинга:{halving_interval} блоков")
            print(f"Майнинг закончится:{end_date.strftime('%Y-%m-%d')}(приблизитьльно)")
            print(f"Начальная награда: {initial_reward:.50f} монет")
            print(f"Общая эмиссия: {total_supply:.6f} монет")
        for block in range(0, total_blocks, 320,000): #Проверка награды каждые 320,000 блоков
            reward = calculate_block_rewrd(block) # type: ignore
            print(f"Блок {block}: Награда {reward:.2f}")

    def bisection1(f, a, b, iterations):
     """
     This is a "stub": it functions in that it is "syntactically correct",
     but does not do the right thing.
     Instead it gives the best available answer without having done any real work!
    
     Inputs:
     f: a continuous function from and to real values
     a: to be continued ...
     """
# Создание блокчейна и добавление блоков    
     Blockchain = Blockchain()
     Blockchain.add_block((1, time.time(), {"amount": 10}, ""))
     Blockchain.add_block((2, time.time(), {"amount": 20}, ""))
# Вывод информации о блоках
def blocksindex():
 print(f"Block {manig.blocks.index} has been added to the blockchain!")
 print(f"Hash: {manig.blocks.hash}")
 print(f"Previous Hash: {manig.blocks.previous_hash}\n")