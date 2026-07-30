// bhydra_ec.hpp — secp256k1 и ECDSA (RFC 6979) с нуля, без зависимостей.
//
// Байт-в-байт совпадает с `b_hydra/wallet.py`: нонс детерминированный, поэтому
// подпись воспроизводима и её можно сверять посимвольно (это и делает
// tests/test_cpp_secure.py). Одной проверки «подпись валидна» было бы мало —
// она прошла бы и при неверно выведенном нонсе, а неверный нонс раскрывает ключ.
#ifndef BHYDRA_EC_HPP
#define BHYDRA_EC_HPP

#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>

#include "bhydra_hash.hpp"

namespace bhydra {

// --------------------------------------------------------------- 256 бит
// Четыре 64-битных лимба, младший первым. Умножение даёт 512 бит.
struct U256 {
    uint64_t v[4] = {0, 0, 0, 0};

    U256() = default;
    explicit U256(uint64_t small) { v[0] = small; }

    bool is_zero() const { return !(v[0] | v[1] | v[2] | v[3]); }

    bool bit(int index) const {
        return (v[index >> 6] >> (index & 63)) & 1ULL;
    }

    static int cmp(const U256 &a, const U256 &b) {
        for (int i = 3; i >= 0; --i) {
            if (a.v[i] != b.v[i]) return a.v[i] < b.v[i] ? -1 : 1;
        }
        return 0;
    }

    bool operator==(const U256 &o) const { return cmp(*this, o) == 0; }
    bool operator!=(const U256 &o) const { return cmp(*this, o) != 0; }

    // Сложение/вычитание с переносом наружу — нужны и для редукции.
    static uint64_t add(U256 &out, const U256 &a, const U256 &b) {
        unsigned __int128 carry = 0;
        for (int i = 0; i < 4; ++i) {
            unsigned __int128 cur = (unsigned __int128)a.v[i] + b.v[i] + carry;
            out.v[i] = (uint64_t)cur;
            carry = cur >> 64;
        }
        return (uint64_t)carry;
    }

    static uint64_t sub(U256 &out, const U256 &a, const U256 &b) {
        unsigned __int128 borrow = 0;
        for (int i = 0; i < 4; ++i) {
            unsigned __int128 cur =
                (unsigned __int128)a.v[i] - b.v[i] - borrow;
            out.v[i] = (uint64_t)cur;
            borrow = (cur >> 64) ? 1 : 0;
        }
        return (uint64_t)borrow;
    }

    void shl1() {
        uint64_t carry = 0;
        for (int i = 0; i < 4; ++i) {
            uint64_t next = v[i] >> 63;
            v[i] = (v[i] << 1) | carry;
            carry = next;
        }
    }

    // Сдвиг вправо, старший бит приходит извне: нужен для обращения по
    // расширенному бинарному алгоритму Евклида, где промежуточное (x + m)
    // не влезает в 256 бит и перенос надо втянуть обратно.
    void shr1(uint64_t top_bit = 0) {
        for (int i = 0; i < 3; ++i) v[i] = (v[i] >> 1) | (v[i + 1] << 63);
        v[3] = (v[3] >> 1) | (top_bit << 63);
    }

    bool even() const { return (v[0] & 1ULL) == 0; }

    Bytes to_bytes() const {                       // 32 байта big-endian
        Bytes out(32);
        for (int i = 0; i < 4; ++i)
            for (int b = 0; b < 8; ++b)
                out[31 - (i * 8 + b)] = (uint8_t)(v[i] >> (8 * b));
        return out;
    }

    static U256 from_bytes(const uint8_t *data, size_t size) {
        U256 out;                                  // берём младшие 32 байта
        size_t take = size < 32 ? size : 32;
        for (size_t i = 0; i < take; ++i) {
            uint8_t byte = data[size - 1 - i];
            out.v[i >> 3] |= (uint64_t)byte << (8 * (i & 7));
        }
        return out;
    }

    static U256 from_hex(const std::string &text) {
        Bytes raw = bhydra::from_hex(text);
        return from_bytes(raw.data(), raw.size());
    }

    std::string hex() const { return to_hex(to_bytes()); }
};

// p = 2^256 - 2^32 - 977, n — порядок группы.
inline const U256 &field_p() {
    static const U256 value = U256::from_hex(
        "fffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2f");
    return value;
}
inline const U256 &group_n() {
    static const U256 value = U256::from_hex(
        "fffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141");
    return value;
}

// Произведение 256×256 → 512 бит (восемь лимбов).
inline void mul_wide(const U256 &a, const U256 &b, uint64_t out[8]) {
    for (int i = 0; i < 8; ++i) out[i] = 0;
    for (int i = 0; i < 4; ++i) {
        unsigned __int128 carry = 0;
        for (int j = 0; j < 4; ++j) {
            unsigned __int128 cur = (unsigned __int128)a.v[i] * b.v[j] +
                                    out[i + j] + carry;
            out[i + j] = (uint64_t)cur;
            carry = cur >> 64;
        }
        out[i + 4] += (uint64_t)carry;
    }
}

// Редукция по модулю p с использованием особой формы простого:
// 2^256 ≡ 2^32 + 977 (mod p), поэтому старшую половину произведения можно
// «сложить» с младшей, умножив на маленькую константу. Общая редукция делением
// была бы в сотни раз дороже — и весь смысл нативной реализации пропал бы.
inline U256 reduce_p(const uint64_t wide[8]) {
    const uint64_t kFold = 0x1000003D1ULL;         // 2^32 + 977
    uint64_t acc[6] = {wide[0], wide[1], wide[2], wide[3], 0, 0};
    uint64_t hi[4] = {wide[4], wide[5], wide[6], wide[7]};

    auto fold = [&](const uint64_t src[4]) {        // acc += src * kFold
        unsigned __int128 carry = 0;
        for (int i = 0; i < 4; ++i) {
            unsigned __int128 cur = (unsigned __int128)src[i] * kFold + acc[i] + carry;
            acc[i] = (uint64_t)cur;
            carry = cur >> 64;
        }
        for (int i = 4; i < 6 && carry; ++i) {
            unsigned __int128 cur = (unsigned __int128)acc[i] + carry;
            acc[i] = (uint64_t)cur;
            carry = cur >> 64;
        }
    };

    fold(hi);
    while (acc[4] | acc[5]) {                       // складываем, пока есть перенос
        uint64_t rest[4] = {acc[4], acc[5], 0, 0};
        acc[4] = acc[5] = 0;
        fold(rest);
    }
    U256 out;
    for (int i = 0; i < 4; ++i) out.v[i] = acc[i];
    while (U256::cmp(out, field_p()) >= 0) {
        U256 tmp;
        U256::sub(tmp, out, field_p());
        out = tmp;
    }
    return out;
}

// Редукция 512 бит по произвольному модулю — двоичным делением. Медленно, но
// используется только в арифметике по модулю n (единицы вызовов на подпись).
inline U256 reduce_mod(const uint64_t wide[8], const U256 &modulus) {
    U256 rest;
    for (int bit = 511; bit >= 0; --bit) {
        uint64_t carry_out = rest.v[3] >> 63;
        rest.shl1();
        rest.v[0] |= (wide[bit >> 6] >> (bit & 63)) & 1ULL;
        if (carry_out || U256::cmp(rest, modulus) >= 0) {
            U256 tmp;
            U256::sub(tmp, rest, modulus);
            rest = tmp;
        }
    }
    return rest;
}

inline U256 add_mod(const U256 &a, const U256 &b, const U256 &m) {
    U256 out;
    uint64_t carry = U256::add(out, a, b);
    if (carry || U256::cmp(out, m) >= 0) {
        U256 tmp;
        U256::sub(tmp, out, m);
        out = tmp;
    }
    return out;
}

inline U256 sub_mod(const U256 &a, const U256 &b, const U256 &m) {
    U256 out;
    if (U256::sub(out, a, b)) {                     // ушли ниже нуля — добавляем модуль
        U256 tmp;
        U256::add(tmp, out, m);
        out = tmp;
    }
    return out;
}

inline U256 mul_p(const U256 &a, const U256 &b) {
    uint64_t wide[8];
    mul_wide(a, b, wide);
    return reduce_p(wide);
}

inline U256 mul_n(const U256 &a, const U256 &b) {
    uint64_t wide[8];
    mul_wide(a, b, wide);
    return reduce_mod(wide, group_n());
}

// Возведение в степень по модулю — для обращения по малой теореме Ферма.
inline U256 pow_mod(const U256 &base, const U256 &exponent, const U256 &m,
                    bool fast_p) {
    U256 result(1), factor = base;
    for (int bit = 0; bit < 256; ++bit) {
        if (exponent.bit(bit))
            result = fast_p ? mul_p(result, factor) : mul_n(result, factor);
        factor = fast_p ? mul_p(factor, factor) : mul_n(factor, factor);
    }
    return result;
}

// Обращение по расширенному БИНАРНОМУ алгоритму Евклида — только сдвиги,
// сложения и вычитания, ни одного умножения по модулю.
//
// Замер объясняет, зачем это вместо малой теоремы Ферма: подпись через Ферма
// занимала 3,1 мс при том, что само умножение точки — 0,27 мс. То есть 90%
// времени уходило не на кривую, а на обращение по модулю n, где у нас нет
// быстрой редукции и каждое умножение считается делением в 512 итераций.
// Оба модуля (p и n) нечётные, поэтому алгоритм применим к обоим.
inline U256 inverse_mod(const U256 &value, const U256 &m) {
    if (value.is_zero()) return U256();
    U256 u = value, v = m, x1(1), x2;
    auto halve = [&m](U256 &x) {
        // x/2 mod m: у нечётного добавляем модуль, и перенос из сложения
        // становится старшим битом сдвига — иначе он бы потерялся.
        if (x.even()) {
            x.shr1();
        } else {
            U256 sum;
            uint64_t carry = U256::add(sum, x, m);
            sum.shr1(carry);
            x = sum;
        }
    };
    const U256 one(1);
    while (u != one && v != one) {
        while (u.even()) {
            u.shr1();
            halve(x1);
        }
        while (v.even()) {
            v.shr1();
            halve(x2);
        }
        if (U256::cmp(u, v) >= 0) {
            U256 tmp;
            U256::sub(tmp, u, v);
            u = tmp;
            x1 = sub_mod(x1, x2, m);
        } else {
            U256 tmp;
            U256::sub(tmp, v, u);
            v = tmp;
            x2 = sub_mod(x2, x1, m);
        }
    }
    return (u == one) ? x1 : x2;
}

inline U256 inverse_p(const U256 &a) { return inverse_mod(a, field_p()); }
inline U256 inverse_n(const U256 &a) { return inverse_mod(a, group_n()); }

// --------------------------------------------------------------- точки кривой
struct Point {                                      // аффинные координаты
    U256 x, y;
    bool infinity = false;
};

inline const Point &generator() {
    static Point g = [] {
        Point p;
        p.x = U256::from_hex(
            "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798");
        p.y = U256::from_hex(
            "483ada7726a3c4655da4fbfc0e1108a8fd17b448a68554199c47d08ffb10d4b8");
        return p;
    }();
    return g;
}

inline bool on_curve(const Point &p) {
    if (p.infinity) return true;
    if (U256::cmp(p.x, field_p()) >= 0 || U256::cmp(p.y, field_p()) >= 0)
        return false;
    U256 left = mul_p(p.y, p.y);
    U256 right = add_mod(mul_p(mul_p(p.x, p.x), p.x), U256(7), field_p());
    return left == right;
}

// Умножение точки на скаляр идёт в проективных координатах Якоби: там нет
// обращения на каждом шаге, а оно дороже умножения в сотни раз. Одно обращение
// нужно только в самом конце, при переводе обратно в аффинные.
struct Jacobian {
    U256 x, y, z;                                   // z == 0 — бесконечность
};

inline Jacobian to_jacobian(const Point &p) {
    Jacobian j;
    if (p.infinity) return j;                       // z = 0
    j.x = p.x;
    j.y = p.y;
    j.z = U256(1);
    return j;
}

inline Point to_affine(const Jacobian &j) {
    Point p;
    if (j.z.is_zero()) {
        p.infinity = true;
        return p;
    }
    U256 zi = inverse_p(j.z);
    U256 zi2 = mul_p(zi, zi);
    p.x = mul_p(j.x, zi2);
    p.y = mul_p(j.y, mul_p(zi2, zi));
    return p;
}

inline Jacobian jacobian_double(const Jacobian &j) {
    if (j.z.is_zero() || j.y.is_zero()) return Jacobian();
    const U256 &m = field_p();
    U256 a = mul_p(j.x, j.x);
    U256 b = mul_p(j.y, j.y);
    U256 c = mul_p(b, b);
    U256 xb = add_mod(j.x, b, m);
    U256 d = mul_p(xb, xb);
    d = sub_mod(sub_mod(d, a, m), c, m);
    d = add_mod(d, d, m);                           // d = 2*((x+b)^2 - a - c)
    U256 e = add_mod(add_mod(a, a, m), a, m);       // e = 3a
    U256 f = mul_p(e, e);
    Jacobian out;
    out.x = sub_mod(f, add_mod(d, d, m), m);
    U256 c8 = add_mod(c, c, m);
    c8 = add_mod(c8, c8, m);
    c8 = add_mod(c8, c8, m);                        // 8c
    out.y = sub_mod(mul_p(e, sub_mod(d, out.x, m)), c8, m);
    out.z = add_mod(mul_p(j.y, j.z), mul_p(j.y, j.z), m);
    return out;
}

inline Jacobian jacobian_add(const Jacobian &a, const Jacobian &b) {
    if (a.z.is_zero()) return b;
    if (b.z.is_zero()) return a;
    const U256 &m = field_p();
    U256 z1z1 = mul_p(a.z, a.z);
    U256 z2z2 = mul_p(b.z, b.z);
    U256 u1 = mul_p(a.x, z2z2);
    U256 u2 = mul_p(b.x, z1z1);
    U256 s1 = mul_p(a.y, mul_p(b.z, z2z2));
    U256 s2 = mul_p(b.y, mul_p(a.z, z1z1));
    U256 h = sub_mod(u2, u1, m);
    U256 r = sub_mod(s2, s1, m);
    if (h.is_zero()) {
        if (r.is_zero()) return jacobian_double(a);
        return Jacobian();                          // P + (−P) = бесконечность
    }
    r = add_mod(r, r, m);
    U256 h2 = add_mod(h, h, m);
    U256 i = mul_p(h2, h2);
    U256 j = mul_p(h, i);
    U256 v = mul_p(u1, i);
    Jacobian out;
    out.x = sub_mod(sub_mod(mul_p(r, r), j, m), add_mod(v, v, m), m);
    U256 s1j = mul_p(s1, j);
    out.y = sub_mod(mul_p(r, sub_mod(v, out.x, m)), add_mod(s1j, s1j, m), m);
    U256 zsum = add_mod(a.z, b.z, m);
    out.z = mul_p(sub_mod(sub_mod(mul_p(zsum, zsum), z1z1, m), z2z2, m), h);
    return out;
}

inline Point scalar_mul(const U256 &k, const Point &p) {
    Jacobian result;                                // бесконечность
    Jacobian base = to_jacobian(p);
    for (int bit = 255; bit >= 0; --bit) {
        result = jacobian_double(result);
        if (k.bit(bit)) result = jacobian_add(result, base);
    }
    return to_affine(result);
}

// --------------------------------------------------------------- ECDSA
inline Bytes public_key_bytes(const U256 &priv) {
    Point pub = scalar_mul(priv, generator());
    Bytes out{0x04};
    Bytes x = pub.x.to_bytes(), y = pub.y.to_bytes();
    out.insert(out.end(), x.begin(), x.end());
    out.insert(out.end(), y.begin(), y.end());
    return out;
}

// z = старшие 256 бит SHA-512 сообщения (как _hash_to_int в wallet.py).
inline U256 hash_to_int(const Bytes &payload) {
    Bytes digest = sha512(payload);
    return U256::from_bytes(digest.data(), 32);
}

// Нонс по RFC 6979 на HMAC-SHA512: тот же HMAC-DRBG, что в wallet.py.
// Генератор кандидатов, а не одно значение: RFC предписывает продолжать ту же
// цепочку, если кандидат не подошёл.
class Rfc6979 {
public:
    Rfc6979(const U256 &priv, const U256 &z) {
        Bytes material = priv.to_bytes();
        uint64_t wide[8] = {z.v[0], z.v[1], z.v[2], z.v[3], 0, 0, 0, 0};
        Bytes zmod = reduce_mod(wide, group_n()).to_bytes();
        material.insert(material.end(), zmod.begin(), zmod.end());
        v_.assign(64, 0x01);
        k_.assign(64, 0x00);
        Bytes seed = v_;
        seed.push_back(0x00);
        seed.insert(seed.end(), material.begin(), material.end());
        k_ = hmac_sha512(k_, seed);
        v_ = hmac_sha512(k_, v_);
        seed = v_;
        seed.push_back(0x01);
        seed.insert(seed.end(), material.begin(), material.end());
        k_ = hmac_sha512(k_, seed);
        v_ = hmac_sha512(k_, v_);
    }

    U256 next() {
        while (true) {
            v_ = hmac_sha512(k_, v_);               // 64 байта ≥ 256 бит сразу
            U256 candidate = U256::from_bytes(v_.data(), 32);
            Bytes tail = v_;
            tail.push_back(0x00);
            Bytes nk = hmac_sha512(k_, tail);
            if (!candidate.is_zero() &&
                U256::cmp(candidate, group_n()) < 0) {
                pending_k_ = nk;
                has_pending_ = true;
                return candidate;
            }
            k_ = nk;
            v_ = hmac_sha512(k_, v_);
        }
    }

    void reject() {                                 // кандидат не подошёл
        if (has_pending_) {
            k_ = pending_k_;
            has_pending_ = false;
        }
        v_ = hmac_sha512(k_, v_);
    }

private:
    Bytes k_, v_, pending_k_;
    bool has_pending_ = false;
};

// Подпись r||s (64 байта, low-s) — как Wallet.sign.
inline Bytes sign(const U256 &priv, const Bytes &payload) {
    U256 z = hash_to_int(payload);
    Rfc6979 nonces(priv, z);
    for (int attempt = 0; attempt < 64; ++attempt) {
        U256 k = nonces.next();
        Point point = scalar_mul(k, generator());
        uint64_t wide[8] = {point.x.v[0], point.x.v[1], point.x.v[2],
                            point.x.v[3], 0, 0, 0, 0};
        U256 r = reduce_mod(wide, group_n());
        if (r.is_zero()) {
            nonces.reject();
            continue;
        }
        uint64_t zwide[8] = {z.v[0], z.v[1], z.v[2], z.v[3], 0, 0, 0, 0};
        U256 zmod = reduce_mod(zwide, group_n());
        U256 s = mul_n(inverse_n(k), add_mod(zmod, mul_n(r, priv), group_n()));
        if (s.is_zero()) {
            nonces.reject();
            continue;
        }
        U256 half = group_n();
        {                                           // half = n / 2
            uint64_t carry = 0;
            for (int i = 3; i >= 0; --i) {
                uint64_t next = half.v[i] & 1ULL;
                half.v[i] = (half.v[i] >> 1) | (carry << 63);
                carry = next;
            }
        }
        if (U256::cmp(s, half) > 0) {               // low-s против ковкости
            U256 flipped;
            U256::sub(flipped, group_n(), s);
            s = flipped;
        }
        Bytes out = r.to_bytes();
        Bytes sb = s.to_bytes();
        out.insert(out.end(), sb.begin(), sb.end());
        return out;
    }
    throw std::runtime_error("не удалось подписать");
}

inline bool verify(const Bytes &public_key, const Bytes &payload,
                   const Bytes &signature) {
    if (public_key.size() != 65 || public_key[0] != 0x04) return false;
    if (signature.size() != 64) return false;
    Point pub;
    pub.x = U256::from_bytes(public_key.data() + 1, 32);
    pub.y = U256::from_bytes(public_key.data() + 33, 32);
    if (!on_curve(pub)) return false;               // защита от invalid-curve
    U256 r = U256::from_bytes(signature.data(), 32);
    U256 s = U256::from_bytes(signature.data() + 32, 32);
    if (r.is_zero() || s.is_zero()) return false;
    if (U256::cmp(r, group_n()) >= 0 || U256::cmp(s, group_n()) >= 0) return false;
    U256 z = hash_to_int(payload);
    uint64_t zwide[8] = {z.v[0], z.v[1], z.v[2], z.v[3], 0, 0, 0, 0};
    U256 zmod = reduce_mod(zwide, group_n());
    U256 w = inverse_n(s);
    Point p1 = scalar_mul(mul_n(zmod, w), generator());
    Point p2 = scalar_mul(mul_n(r, w), pub);
    Jacobian sum = jacobian_add(to_jacobian(p1), to_jacobian(p2));
    Point point = to_affine(sum);
    if (point.infinity) return false;
    uint64_t xwide[8] = {point.x.v[0], point.x.v[1], point.x.v[2],
                         point.x.v[3], 0, 0, 0, 0};
    return reduce_mod(xwide, group_n()) == r;
}

}  // namespace bhydra
#endif
