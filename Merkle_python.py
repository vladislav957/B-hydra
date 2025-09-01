import hashlib
from itertools import tee
from logging import root
from turtle import right

class MerkleNode:
    def init(self, data=None,left=None,right=None):
        self.data = data #Данные для листового узла, или хеши для промежуточных метак.
        self.left = left
        self.right = right
        self.hash = self._calculate_hash()
    def _calculate_hash(self):
        if self.data is not None:
            #Для листового узла хешируем данные
            #return hashlib.sha3_512(str(self.data)encode()).hexdigest()
         #elif; self.left and self.right:
            #Для внутреннего узла хешируем хеши дочерних узлов
         return hashlib.sha512(str(self.lift.hash + right.hash).encode()).hexdigest()
        else:
         return None #Должны быть либо данные, либо оба дочерних узла
        

    def build_merkle_tree(data_blocks):
        """ Строит дерево Меркла из списка блоков данных. """
        leaves = [MerkleNode(data=block)for block in data_blocks]
        if not leaves:
            return None
        #Если количество блоков нечетное,добавляем последний блок дважлы (опционально)
        if len(leaves)%2 == 1:
            leaves.append(leaves[-1])

            while len(leaves)>1:
                now_level = []
                for i in range(0, len(leaves),2):
                    left_child = leaves[i]
                    right_child = leaves[i+1]
                    parent_node = MerkleNode(left_child,right_child)
                    now_level.append(parent_node)
                    leaves = now_level
                    #Если количество узлов нечетное после создания уровня,добавленияем дубликат
                    if len(leaves)%2 == 1 and len(leaves)>1:
                        leaves.append(leaves[-1])
                        return leaves[0] #Возвпащаем корневой узел дерева
                    #Пример использования 
                    data = ["a","b","c","b","e"]
                    root = builb_merkle_tree(data) # type: ignore

                    if root:
                       print("Корневой хеш дерева Меркла:",root.hash)

                       #Пример извлечения хеша для одного из блоков
                    def get_leaf_hash_by_data(node,taget_data):
                       if node.left is None and node.right is None: #Листовой узел
                          return node.hash if node.data == taget_data else None
                       else:
                          if node.left:
                             left_result = get_leaf_hash_by_data(node.left,taget_data)
                             if left_result: return left_result
                             if node.right:
                                right_result = get_leaf_hash_by_data(node.right,taget_data)
                                if right_result:return right_result
                                return None
                             leaf_e_hash = get_leaf_hash_by_data(root,"e")
                             print("Хеш данный 'e':", leaf_e_hash)
 
#Созданйте дерево с начальными элеметами 
#tree = MerkleTree([b'element1',b'element2',b'element3',b'element4'])

#Получаем корневой хеш
root_hash = tee.__hash__ 
print(f"Корнивой хеш: {root_hash}")

#Сгенирируйте доказательство для элемента
element_to_prove = b'element2'
proof = pow = b"(element_to_prove)"

print(f"Докакзательство для '{element_to_prove.decode()}':{proof}") 

#Верификацируйте доказательство
pow = pow = b'(proof,element_to_prove)'

print(f"proof_of_work:{pow}")