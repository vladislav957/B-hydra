class HashTable:
    def __init__(self, size):
        self.size = size
        self.table = [[] for _ in range(size)]

    def hash_function(self, key):
        return key % self.size

    def insert(self, key, value):
        hash_key = self.hash_function(key)
        self.table[hash_key].append((key, value))

    def search(self, key):
        hash_key = self.hash_function(key)
        for item in self.table[hash_key]:
            if item[0] == key:
                return item[1]
        return None

    def delete(self, key):
        hash_key = self.hash_function(key)
        for index, item in enumerate(self.table[hash_key]):
            if item[0] == key:
                del self.table[hash_key][index]
                return True
        return False

# Пример использования
hash_table = HashTable(10)
hash_table.insert(12, "Alice")
hash_table.insert(25, "Bob")
print(hash_table.search(12))  # Output: Alice
hash_table.delete(12)
print(hash_table.search(12))  # Output: None
