// bhydra_xmss.hpp — лист дерева XMSS (ключ WOTS) на C++, байт в байт как Python.
//
// ЗАЧЕМ. Генерация XMSS-ключа — это 2^h листьев, и каждый лист стоит
// len·(W−1) + 1 хешей: 67·15 + 67 + 1 = 1073 в режиме P256. Публичный ключ ЕСТЬ
// корень над всеми листьями, поэтому обойтись меньшим числом нельзя никаким
// алгоритмом обхода — BDS удешевил ПОДПИСЬ, а не генерацию.
//
// Замер на этой машине (`b_hydra/pqcrypto.py`, один лист):
//
//     наш чистый Python-SHA   282,19 мс   →   h=16 это 308 минут
//     hashlib (OpenSSL)         0,97 мс   →   h=16 это 1,1 минуты
//
// Из тех же замеров: узлы дерева — 0,24–0,34% общего времени, всё остальное
// листья. Поэтому сюда уезжают ТОЛЬКО листья, а обход BDS остаётся в Python.
//
// ⚠️ ЭТО НЕ ПЕРЕНОС ЛОГИКИ, А ПЕРЕНОС АРИФМЕТИКИ. Состояние обхода, путь
// включения и расписание treehash — код с тонкой семантикой, и второй его копии
// в проекте быть не должно: ровно поэтому `sign_hash` в своё время ВЫНЕСЛИ из
// `sign`, а не скопировали, и ровно поэтому две реализации кривой свели в
// единый `ec.py`. Лист же — чистая функция от (seed, index), у неё нет
// состояния и расходиться ей негде.
//
// ⚠️ ВЕСЬ СМЫСЛ В ПАРАЛЛЕЛИЗМЕ. В один поток C++ примерно вровень с hashlib —
// то же, что уже выяснилось на майнере: `hashlib` это OpenSSL, и наш SHA с ним
// идёт ноздря в ноздрю. Выигрыш даёт то, чего у Python нет из-за GIL, —
// 2^h независимых листьев на всех ядрах сразу.
//
// ⚠️ Индекс кладётся 4 БАЙТАМИ BIG-ENDIAN, как `i.to_bytes(4, "big")` в Python.
// Возьми мы другой порядок — листья сходились бы сами с собой, но не с
// кошельком, и корень (он же публичный ключ и часть гибридного адреса) вышел
// бы другим.
#ifndef BHYDRA_XMSS_HPP
#define BHYDRA_XMSS_HPP

#include "bhydra_hash.hpp"

#include <cstdint>
#include <cstring>
#include <vector>

namespace bhydra {
namespace xmss {

// Основание Винтерница: цифра — 4 бита, значит в цепочке W−1 = 15 шагов.
constexpr int kW = 16;
constexpr int kChainSteps = kW - 1;

// Длина seed ограничена, чтобы деривация секрета укладывалась в стек.
constexpr size_t kMaxSeed = 256;

/// Набор параметров схемы. Совпадает с P256/P512 из `pqcrypto.py`.
struct Params {
    size_t n;      ///< байт в элементе подписи
    size_t len;    ///< число цепочек = len1 + len2
};

constexpr Params kP256{32, 64 + 3};    ///< элементы SHA-256, 67 цепочек
constexpr Params kP512{64, 128 + 3};   ///< элементы SHA-512, 131 цепочка

/// Рабочие буферы одного потока: выделяются раз, а не на каждый из 2^h листьев.
struct Scratch {
    uint8_t seed_buf[kMaxSeed + 12];   ///< seed || index || "wots" || j
    Bytes pk;                          ///< склейка публичного ключа WOTS
};

/// Лист №index: хеш публичного ключа WOTS, выведенного из seed.
///
/// Повторяет `MerkleSigner._leaf` ровно:
///     sk[j] = H(seed || index_be4 || "wots" || j_be4)
///     pk[j] = H^15(sk[j])
///     leaf  = H(pk[0] || … || pk[len-1])
template <class H, const Params &P>
inline void leaf_of(const uint8_t *seed, size_t seed_len, uint32_t index,
                    Scratch &scratch, uint8_t *out) {
    uint8_t *buf = scratch.seed_buf;
    std::memcpy(buf, seed, seed_len);
    size_t at = seed_len;
    buf[at++] = (uint8_t)(index >> 24);
    buf[at++] = (uint8_t)(index >> 16);
    buf[at++] = (uint8_t)(index >> 8);
    buf[at++] = (uint8_t)index;
    std::memcpy(buf + at, "wots", 4);
    at += 4;
    const size_t j_at = at;            // сюда ляжет номер цепочки
    at += 4;

    if (scratch.pk.size() != P.len * P.n) scratch.pk.assign(P.len * P.n, 0);
    uint8_t *pk = scratch.pk.data();

    for (size_t j = 0; j < P.len; ++j) {
        buf[j_at + 0] = (uint8_t)(j >> 24);
        buf[j_at + 1] = (uint8_t)(j >> 16);
        buf[j_at + 2] = (uint8_t)(j >> 8);
        buf[j_at + 3] = (uint8_t)j;
        uint8_t *slot = pk + j * P.n;
        H::one_shot(buf, at, slot);                  // sk[j]
        for (int step = 0; step < kChainSteps; ++step)
            H::one_shot(slot, P.n, slot);            // цепочка Винтерница
    }
    H::one_shot(pk, P.len * P.n, out);
}

/// Листья [start, start + count) подряд в `out` (count · n байт).
inline bool leaves(const uint8_t *seed, size_t seed_len, int alg, uint32_t start,
                   uint32_t count, uint8_t *out) {
    if (seed == nullptr || out == nullptr || seed_len > kMaxSeed) return false;
    Scratch scratch;
    if (alg == 256) {
        for (uint32_t k = 0; k < count; ++k)
            leaf_of<Sha256, kP256>(seed, seed_len, start + k, scratch,
                                   out + (size_t)k * kP256.n);
        return true;
    }
    if (alg == 512) {
        for (uint32_t k = 0; k < count; ++k)
            leaf_of<Sha512, kP512>(seed, seed_len, start + k, scratch,
                                   out + (size_t)k * kP512.n);
        return true;
    }
    return false;   // чужой режим — молча считать «как-нибудь» нельзя
}

/// Размер элемента (и листа) для режима: 32 или 64 байта. 0 — режим неизвестен.
inline size_t leaf_size(int alg) {
    return alg == 256 ? kP256.n : (alg == 512 ? kP512.n : 0);
}

}  // namespace xmss
}  // namespace bhydra

#endif  // BHYDRA_XMSS_HPP
