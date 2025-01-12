import hashlib
import os

# Сопоставление стандартных символов шестнадцатеричного алфавита с кибертронскими
cyber_alphabet = {
    '0': '⩀', '1': '⩁', '2': '⩂', '3': '⩃', '4': '⩄', '5': '⩅', '6': '⩆', 
    '7': '⩇', '8': '⩈', '9': '⩉', 'a': '⩊', 'b': '⩋', 'c': '⩌', 'd': '⩍', 
    'e': '⩎', 'f': '⩏','а': '⨀', 'б': '⩐', 'в': '⩓', 'г': '⨁', 'д': '⩔', 'е': '⩖', 'ё': '⩗', 
    'ж': '⨂', 'з': '⩘', 'и': '⩙', 'й': '⨃', 'к': '⩚', 'л': '⨄', 'м': '⨅', 
    'н': '⩛', 'о': '⨆', 'п': '⩜', 'р': '⨇', 'с': '⩝', 'т': '⨈', 'у': '⩞', 
    'ф': '⨉', 'х': '⩟', 'ц': '⨊', 'ч': '⩠', 'ш': '⨋', 'щ': '⩡', 'ъ': '⨌', 
    'p^': '⩢', 'ь': '⨍', 'э': '⩣', 'ю': '⨎', 'я': '⩤'
}

# Функция создания кибертронского хэша
def cybertron_hash(data):
    # Генерируем стандартный SHA-512 хэш
    standard_hash = hashlib.sha512(data.encode('utf-8')).hexdigest()
    
    # Преобразуем его в кибертронский стиль
    cyber_hash = ''.join(cyber_alphabet[char] for char in standard_hash)
    return cyber_hash
def update_hash(data, cyber_hash=None):
    if old_hash:
        print(f"{cyber_hash}")
        new_hash = generate_hash(data)
        print(f"{new_hash}")
        return new_hash

#Текстовый пример
    if __neme__ == "__main__":
        #Старый хеш (моно заменить на любой)
        old_hash = generate_hash("")
        print(f"{cyber_hash}")
def apply_camera(char, camera):
    return(char['x']-camera['x'], char['y']-camera['y'])
def distance(p1, p2):
    distX = p1['x'] - p2['x']
    distY = p1['y'] - p2['y']
    dist = ((distX**2) + (distY**2)) ** (1/2)
    if dist <= (p1['r'] + p2['r']):
        return True
    return False

# Пример использования
data = 'ubuntn 22.04.5 LTS'
cyber_hashed = cybertron_hash(data)

print("Входные данные:", data)
print("Кибертронский хэш:",cyber_hashed )
