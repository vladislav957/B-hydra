from cryptography.hazmat.primitives import hashes, serialization # type: ignore
from cryptography.hazmat.primitives.asymmetric import padding, rsa # type: ignore
from cryptography.hazmat.backends import default_backend # type: ignore
import wallet # type: ignore
import Blockchain # type: ignore
import socket
import math as m
from variables import * # type: ignore
import random


def inverse(a: int, m: int) -> int:
    m0, x0, x1 = m, 0, 1
    while a > 1:
        q = a // m
        m, a = a % m, m
        x0, x1 = x1 - q * x0, x0
    return x1 + m0 if x1 < 0 else x1


def gcd(a: int, b: int) -> int:
    return a if b == 0 else m.gcd(a, a % b)


def generate_keypair(p, q, bit_length):

    p = gen_prime(bit_length) # type: ignore
    q = gen_prime(bit_length) # type: ignore

    n = p * q
    phi = (p-1) * (q-1)

    # Выбираем открытый ключ e, такой что 1 < e < phi и e взаимно прост с phi
    e = random.randrange(2, phi)
    while m.gcd(e, phi) != 1:
        e = random.randrange(2, phi)

    # Вычисляем закрытый ключ d, такой что d * e ≡ 1 (mod phi)
    d = inverse(e, phi)

    return ((e, n), (d, n))

def encrypt(public_key, plaintext):
    e, n = public_key
    ciphertext = [pow(ord(char), e, n) for char in plaintext]
    return ciphertext



def decrypt(private_key, ciphertext):
    d, n = private_key
    plaintext = [chr(pow(char, d, n)) for char in ciphertext]
    return ''.join(plaintext)


def running():
    print("RSA is running...")

# Генерация пары ключей RSA
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend()
)

public_key = private_key.public_key()

# Сообщение, которое мы хотим подписать
message = b'wallet'

# Создание хеша SHA-512
digest = hashes.Hash(hashes.SHA512(), backend=default_backend()).encode('UTF-8')
digest.update(message)
hash_value = digest.finalize()

# Подпись хеша с использованием приватного ключа
signature = private_key.sign(
    hash_value,
    padding.PSS(
        mgf=padding.MGF1(hashes.SHA512()),
        salt_length=padding.PSS.MAX_LENGTH
    ),
    hashes.SHA512()
)

print(f"Подпись: {signature.hex()}")

# Проверка подписи с использованием открытого ключа
try:
    public_key.verify(
        signature,
        hash_value,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA512()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
        hashes.SHA512()
    )
    print("Подпись действительна!")
except Exception as e:
    print("Подпись недействительна:", e)




