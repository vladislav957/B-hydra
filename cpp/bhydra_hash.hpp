// bhydra_hash.hpp — SHA-512, SHAKE-256 и HMAC-SHA512 с нуля, без зависимостей.
//
// Те же байты, что и у Python-реализации B-hydra (`b_hydra/sha2.py`,
// `hashlib.shake_256`): значения сверяются с ней в tests/test_cpp_secure.py.
#ifndef BHYDRA_HASH_HPP
#define BHYDRA_HASH_HPP

#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace bhydra {

using Bytes = std::vector<uint8_t>;

// ---------------------------------------------------------------- SHA-512
class Sha512 {
public:
    Sha512() { reset(); }

    void reset() {
        static const uint64_t iv[8] = {
            0x6a09e667f3bcc908ULL, 0xbb67ae8584caa73bULL, 0x3c6ef372fe94f82bULL,
            0xa54ff53a5f1d36f1ULL, 0x510e527fade682d1ULL, 0x9b05688c2b3e6c1fULL,
            0x1f83d9abfb41bd6bULL, 0x5be0cd19137e2179ULL};
        std::memcpy(state_, iv, sizeof(iv));
        buffer_.clear();
        length_ = 0;
    }

    void update(const uint8_t *data, size_t size) {
        length_ += size;
        buffer_.insert(buffer_.end(), data, data + size);
        size_t at = 0;
        while (buffer_.size() - at >= 128) {
            compress(buffer_.data() + at);
            at += 128;
        }
        buffer_.erase(buffer_.begin(), buffer_.begin() + at);
    }

    void update(const Bytes &data) { update(data.data(), data.size()); }

    Bytes digest() const {
        Sha512 copy(*this);
        // Дополнение: 0x80, нули, затем длина в БИТАХ 128-битным полем.
        uint64_t bits = copy.length_ * 8;
        Bytes tail{0x80};
        size_t rest = (copy.length_ + 1) % 128;
        size_t pad = (rest <= 112) ? (112 - rest) : (240 - rest);
        tail.insert(tail.end(), pad, 0x00);
        for (int i = 0; i < 8; ++i) tail.push_back(0);       // старшие 64 бита длины
        for (int i = 7; i >= 0; --i) tail.push_back((uint8_t)(bits >> (i * 8)));
        copy.update(tail);
        Bytes out;
        out.reserve(64);
        for (int i = 0; i < 8; ++i)
            for (int b = 7; b >= 0; --b)
                out.push_back((uint8_t)(copy.state_[i] >> (b * 8)));
        return out;
    }

private:
    static uint64_t ror(uint64_t x, int n) { return (x >> n) | (x << (64 - n)); }

    void compress(const uint8_t *block) {
        static const uint64_t K[80] = {
            0x428a2f98d728ae22ULL, 0x7137449123ef65cdULL, 0xb5c0fbcfec4d3b2fULL,
            0xe9b5dba58189dbbcULL, 0x3956c25bf348b538ULL, 0x59f111f1b605d019ULL,
            0x923f82a4af194f9bULL, 0xab1c5ed5da6d8118ULL, 0xd807aa98a3030242ULL,
            0x12835b0145706fbeULL, 0x243185be4ee4b28cULL, 0x550c7dc3d5ffb4e2ULL,
            0x72be5d74f27b896fULL, 0x80deb1fe3b1696b1ULL, 0x9bdc06a725c71235ULL,
            0xc19bf174cf692694ULL, 0xe49b69c19ef14ad2ULL, 0xefbe4786384f25e3ULL,
            0x0fc19dc68b8cd5b5ULL, 0x240ca1cc77ac9c65ULL, 0x2de92c6f592b0275ULL,
            0x4a7484aa6ea6e483ULL, 0x5cb0a9dcbd41fbd4ULL, 0x76f988da831153b5ULL,
            0x983e5152ee66dfabULL, 0xa831c66d2db43210ULL, 0xb00327c898fb213fULL,
            0xbf597fc7beef0ee4ULL, 0xc6e00bf33da88fc2ULL, 0xd5a79147930aa725ULL,
            0x06ca6351e003826fULL, 0x142929670a0e6e70ULL, 0x27b70a8546d22ffcULL,
            0x2e1b21385c26c926ULL, 0x4d2c6dfc5ac42aedULL, 0x53380d139d95b3dfULL,
            0x650a73548baf63deULL, 0x766a0abb3c77b2a8ULL, 0x81c2c92e47edaee6ULL,
            0x92722c851482353bULL, 0xa2bfe8a14cf10364ULL, 0xa81a664bbc423001ULL,
            0xc24b8b70d0f89791ULL, 0xc76c51a30654be30ULL, 0xd192e819d6ef5218ULL,
            0xd69906245565a910ULL, 0xf40e35855771202aULL, 0x106aa07032bbd1b8ULL,
            0x19a4c116b8d2d0c8ULL, 0x1e376c085141ab53ULL, 0x2748774cdf8eeb99ULL,
            0x34b0bcb5e19b48a8ULL, 0x391c0cb3c5c95a63ULL, 0x4ed8aa4ae3418acbULL,
            0x5b9cca4f7763e373ULL, 0x682e6ff3d6b2b8a3ULL, 0x748f82ee5defb2fcULL,
            0x78a5636f43172f60ULL, 0x84c87814a1f0ab72ULL, 0x8cc702081a6439ecULL,
            0x90befffa23631e28ULL, 0xa4506cebde82bde9ULL, 0xbef9a3f7b2c67915ULL,
            0xc67178f2e372532bULL, 0xca273eceea26619cULL, 0xd186b8c721c0c207ULL,
            0xeada7dd6cde0eb1eULL, 0xf57d4f7fee6ed178ULL, 0x06f067aa72176fbaULL,
            0x0a637dc5a2c898a6ULL, 0x113f9804bef90daeULL, 0x1b710b35131c471bULL,
            0x28db77f523047d84ULL, 0x32caab7b40c72493ULL, 0x3c9ebe0a15c9bebcULL,
            0x431d67c49c100d4cULL, 0x4cc5d4becb3e42b6ULL, 0x597f299cfc657e2aULL,
            0x5fcb6fab3ad6faecULL, 0x6c44198c4a475817ULL};
        uint64_t w[80];
        for (int i = 0; i < 16; ++i) {
            uint64_t value = 0;
            for (int b = 0; b < 8; ++b) value = (value << 8) | block[i * 8 + b];
            w[i] = value;
        }
        for (int i = 16; i < 80; ++i) {
            uint64_t s0 = ror(w[i - 15], 1) ^ ror(w[i - 15], 8) ^ (w[i - 15] >> 7);
            uint64_t s1 = ror(w[i - 2], 19) ^ ror(w[i - 2], 61) ^ (w[i - 2] >> 6);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint64_t a = state_[0], b = state_[1], c = state_[2], d = state_[3];
        uint64_t e = state_[4], f = state_[5], g = state_[6], h = state_[7];
        for (int i = 0; i < 80; ++i) {
            uint64_t S1 = ror(e, 14) ^ ror(e, 18) ^ ror(e, 41);
            uint64_t ch = (e & f) ^ (~e & g);
            uint64_t t1 = h + S1 + ch + K[i] + w[i];
            uint64_t S0 = ror(a, 28) ^ ror(a, 34) ^ ror(a, 39);
            uint64_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint64_t t2 = S0 + maj;
            h = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        uint64_t add[8] = {a, b, c, d, e, f, g, h};
        for (int i = 0; i < 8; ++i) state_[i] += add[i];
    }

    uint64_t state_[8];
    Bytes buffer_;
    uint64_t length_;
};

inline Bytes sha512(const Bytes &data) {
    Sha512 h;
    h.update(data);
    return h.digest();
}

// ---------------------------------------------------------------- SHAKE-256
// Keccak-f[1600]; для SHAKE-256 ёмкость 512 бит, значит скорость (rate) 136 байт,
// доменное дополнение — 0x1F (FIPS 202).
inline void keccak_f(uint64_t s[25]) {
    static const uint64_t RC[24] = {
        0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
        0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
        0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
        0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
        0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
        0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
        0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
        0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL};
    static const int R[25] = {0,  1,  62, 28, 27, 36, 44, 6,  55, 20, 3, 10, 43,
                              25, 39, 41, 45, 15, 21, 8,  18, 2,  61, 56, 14};
    // Перестановка ρ+π и соседи для χ посчитаны заранее: деление по модулю 5
    // внутри самого горячего цикла стоило дороже самих операций (замер:
    // поток шифра 43 → 100+ МиБ/с после выноса).
    // Индексы считаются прямо в цикле, через % 5. Попытка вынести их в
    // статические таблицы сделала ХУЖЕ (замер: 43 → 33 МиБ/с): деление на
    // константу компилятор превращает в умножение со сдвигом, а таблицы — это
    // обращения в память и косвенная запись, из-за которой состояние b[]
    // перестаёт жить в регистрах. Оставлено как понятнее и как быстрее.
    auto rol = [](uint64_t x, int n) {
        return n ? ((x << n) | (x >> (64 - n))) : x;
    };
    for (int round = 0; round < 24; ++round) {
        uint64_t c[5], d[5];
        for (int x = 0; x < 5; ++x)
            c[x] = s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20];
        for (int x = 0; x < 5; ++x) d[x] = c[(x + 4) % 5] ^ rol(c[(x + 1) % 5], 1);
        for (int x = 0; x < 5; ++x)
            for (int y = 0; y < 5; ++y) s[x + 5 * y] ^= d[x];
        uint64_t b[25];
        for (int x = 0; x < 5; ++x)
            for (int y = 0; y < 5; ++y)
                b[y + 5 * ((2 * x + 3 * y) % 5)] = rol(s[x + 5 * y], R[x + 5 * y]);
        for (int x = 0; x < 5; ++x)
            for (int y = 0; y < 5; ++y)
                s[x + 5 * y] = b[x + 5 * y] ^ (~b[(x + 1) % 5 + 5 * y] &
                                               b[(x + 2) % 5 + 5 * y]);
        s[0] ^= RC[round];
    }
}

inline Bytes shake256(const Bytes &input, size_t out_len) {
    const size_t rate = 136;
    uint64_t s[25] = {0};
    Bytes padded = input;
    padded.push_back(0x1F);                       // доменное дополнение SHAKE
    while (padded.size() % rate != 0) padded.push_back(0x00);
    padded[padded.size() - 1] |= 0x80;
    // Впитывание и выдача по 8 байт за раз: побайтные сдвиги на мегабайтном
    // потоке заметно дороже, чем сборка лимба целиком.
    for (size_t at = 0; at < padded.size(); at += rate) {
        for (size_t limb = 0; limb < rate / 8; ++limb) {
            uint64_t value = 0;
            for (int b = 7; b >= 0; --b)
                value = (value << 8) | padded[at + limb * 8 + b];
            s[limb] ^= value;
        }
        keccak_f(s);
    }
    Bytes out(out_len);
    size_t done = 0;
    while (done < out_len) {
        size_t chunk = out_len - done < rate ? out_len - done : rate;
        for (size_t i = 0; i < chunk; ++i)
            out[done + i] = (uint8_t)(s[i / 8] >> (8 * (i % 8)));
        done += chunk;
        if (done < out_len) keccak_f(s);
    }
    return out;
}

// ---------------------------------------------------------------- HMAC-SHA512
inline Bytes hmac_sha512(Bytes key, const Bytes &message) {
    const size_t block = 128;                     // внутренний блок SHA-512
    if (key.size() > block) key = sha512(key);
    key.resize(block, 0x00);
    Bytes inner_key(block), outer_key(block);
    for (size_t i = 0; i < block; ++i) {
        inner_key[i] = key[i] ^ 0x36;
        outer_key[i] = key[i] ^ 0x5c;
    }
    Sha512 inner;
    inner.update(inner_key);
    inner.update(message);
    Bytes digest = inner.digest();
    Sha512 outer;
    outer.update(outer_key);
    outer.update(digest);
    return outer.digest();
}

// ---------------------------------------------------------------- утилиты
inline std::string to_hex(const Bytes &data) {
    static const char *digits = "0123456789abcdef";
    std::string out;
    out.reserve(data.size() * 2);
    for (uint8_t byte : data) {
        out.push_back(digits[byte >> 4]);
        out.push_back(digits[byte & 0x0f]);
    }
    return out;
}

// Разбор hex. Нечётная длина — ОШИБКА, а не «последний символ отбросим»:
// молчаливое усечение здесь дало бы нулевой ключ вместо заявленного, и код
// продолжил бы работать, «шифруя» на нём. Пустой результат при непустом входе
// означает отказ, и вызывающий обязан его проверить.
inline Bytes from_hex(const std::string &text) {
    Bytes out;
    if (text.size() % 2 != 0) return out;
    out.reserve(text.size() / 2);
    auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i + 1 < text.size(); i += 2) {
        int hi = nibble(text[i]), lo = nibble(text[i + 1]);
        if (hi < 0 || lo < 0) return Bytes();
        out.push_back((uint8_t)((hi << 4) | lo));
    }
    return out;
}

}  // namespace bhydra
#endif
