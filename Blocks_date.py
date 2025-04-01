import hashlib
import time
import json
import Blockchain

class Block:
    def init(self, index, previous_hash, data, timestamp=None):
        self.index = index
        self.previous_hash = previous_hash
        self.data = data
        self.timestamp = timestamp or time.time()
        self.hash = self.calculate_hash()

    def calculate_hash(self):
        block_string = f"{self.index}{self.previous_hash}{self.data}{self.timestamp}"
        return hashlib.sha512(block_string.encode()).hexdigest()

    def to_dict(self):
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "data": self.data,
            "timestamp": self.timestamp,
            "hash": self.hash
        }

class Blockchain:
    def init(self):
        self.chain = [self.create_genesis_block()]

    def create_genesis_block(self):
        return Block(0, "0", "Genesis Block")

    def add_block(self, data):
        #last_block = self.chain[-1]
        #new_block = Block(len(self.chain), last_block.hash, data)
        #self.chain.append(new_block)
        #return new_block

     def get_blocks_data(self):
        return [block.to_dict() for block in self.chain]

# Пример использования
blockchain = Blockchain()
blockchain.add_block("Первый блок данных")
blockchain.add_block("Второй блок данных")

# Вывод всех блоков
#print(json.dumps(blockchain.get_blocks_data(), indent=4, ensure_ascii=False))
