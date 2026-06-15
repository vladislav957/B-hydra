"""
RSA.py — RSA в B-hydra: учебная реализация и подпись через `cryptography`.

Содержит:
  * чистую («школьную») реализацию RSA для демонстрации математики;
  * рабочую подпись/проверку SHA-512 + PSS на базе библиотеки `cryptography`.
"""

import math
import random


# --- Учебная реализация RSA --------------------------------------------------
def _inverse(a: int, m: int) -> int:
    """Модульная инверсия a по модулю m (расширенный алгоритм Евклида)."""
    g, x = m, 0
    x1, a1 = 1, a
    while a1 > 1:
        q = a1 // g
        g, a1 = a1 % g, g
        x, x1 = x1 - q * x, x
    return x1 + m if x1 < 0 else x1


def _is_prime(n: int, k: int = 20) -> bool:
    """Тест Миллера — Рабина на простоту."""
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(k):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def gen_prime(bit_length: int) -> int:
    """Генерирует случайное простое число заданной битности."""
    while True:
        candidate = random.getrandbits(bit_length) | (1 << (bit_length - 1)) | 1
        if _is_prime(candidate):
            return candidate


def generate_keypair(bit_length: int = 512):
    """Генерирует пару ключей RSA. Возвращает (public, private)."""
    p = gen_prime(bit_length)
    q = gen_prime(bit_length)
    while q == p:
        q = gen_prime(bit_length)

    n = p * q
    phi = (p - 1) * (q - 1)

    e = 65537
    if math.gcd(e, phi) != 1:
        e = random.randrange(3, phi, 2)
        while math.gcd(e, phi) != 1:
            e = random.randrange(3, phi, 2)

    d = _inverse(e, phi)
    return (e, n), (d, n)


def encrypt(public_key, plaintext: str):
    e, n = public_key
    return [pow(ord(ch), e, n) for ch in plaintext]


def decrypt(private_key, ciphertext):
    d, n = private_key
    return "".join(chr(pow(ch, d, n)) for ch in ciphertext)


# --- Подпись на базе cryptography (SHA-512 + PSS) ---------------------------
def sign_message(message: bytes):
    """Подписывает сообщение RSA-2048 + SHA-512/PSS. Возвращает (sig, public_key)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    if isinstance(message, str):
        message = message.encode("utf-8")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA512()),
                    salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA512(),
    )
    return signature, private_key.public_key()


def verify_message(public_key, message: bytes, signature: bytes) -> bool:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.exceptions import InvalidSignature
    if isinstance(message, str):
        message = message.encode("utf-8")
    try:
        public_key.verify(
            signature, message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA512()),
                        salt_length=padding.PSS.MAX_LENGTH),
            hashes.SHA512(),
        )
        return True
    except InvalidSignature:
        return False


if __name__ == "__main__":
    # Учебный RSA.
    pub, priv = generate_keypair(256)
    cipher = encrypt(pub, "B-hydra")
    print("Расшифровано:", decrypt(priv, cipher))

    # Подпись через cryptography.
    sig, pk = sign_message(b"wallet")
    print("Подпись действительна:", verify_message(pk, b"wallet", sig))
