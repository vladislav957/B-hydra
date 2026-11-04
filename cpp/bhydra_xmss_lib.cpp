// bhydra_xmss_lib.cpp — генерация листьев XMSS на всех ядрах, для Python.
//
// Сборка:
//     g++ -O2 -std=c++17 -pthread -shared -fPIC -I cpp
//         -o libbhydra_xmss.so cpp/bhydra_xmss_lib.cpp
//     x86_64-w64-mingw32-g++ -O2 -std=c++17 -shared -static -I cpp
//         -o bhydra_xmss.dll cpp/bhydra_xmss_lib.cpp
//
// ПОЧЕМУ БИБЛИОТЕКА, А НЕ КОМАНДА. У майнера мост — отдельный процесс, и это
// уместно: он молотит секундами, запуск теряется в фоне. Здесь Python ходит за
// листьями ПАЧКАМИ, чтобы между пачками показать прогресс и дать себя
// прервать, — при отдельном процессе за каждую пачку платился бы запуск.
//
// ⚠️ ПАЧКАМИ, А НЕ ВСЁ СРАЗУ, — по той же причине, по которой нативный майнер
// работает срезами по времени: генерация h=16 идёт минутами, и отдавать
// управление насовсем значит подвесить окно и лишить человека возможности
// отменить создание кошелька.
//
// ⚠️ Результату НЕ ВЕРЯТ на слово. Python перед включением бэкенда сверяет
// листья с чистой реализацией байт в байт (`native_xmss._selftest`), а
// библиотека дополнительно проверяет себя векторами NIST при первом вызове.
// Корень дерева — это публичный ключ и часть гибридного адреса: ядро с чужим
// SHA дало бы ДРУГОЙ адрес, то есть деньги ушли бы в никуда.

#include "bhydra_xmss.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace {

using bhydra::Bytes;

std::string hex(const uint8_t *data, size_t len) {
    static const char *digits = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        out.push_back(digits[data[i] >> 4]);
        out.push_back(digits[data[i] & 0x0F]);
    }
    return out;
}

// Векторы NIST для пустой строки и "abc" — обе длины, оба хеша.
bool hashes_are_ours() {
    uint8_t out[64];
    const uint8_t abc[3] = {'a', 'b', 'c'};

    bhydra::Sha256::one_shot(nullptr, 0, out);
    if (hex(out, 32) !=
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
        return false;
    bhydra::Sha256::one_shot(abc, 3, out);
    if (hex(out, 32) !=
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        return false;

    bhydra::Sha512::one_shot(nullptr, 0, out);
    if (hex(out, 64) !=
        "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
        "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e")
        return false;
    bhydra::Sha512::one_shot(abc, 3, out);
    if (hex(out, 64) !=
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f")
        return false;

    // ⚠️ Длина НА ГРАНИЦЕ БЛОКА — та проверка, ради которой стоило писать
    // self-test: короткие векторы идут одним блоком и ошибку в дополнении
    // просто не задевают. 55 байт для SHA-256 и 111 для SHA-512 — последняя
    // длина, при которой длина сообщения ещё влезает в тот же блок.
    Bytes edge(111, 'x');
    bhydra::Sha256::one_shot(edge.data(), 55, out);
    if (hex(out, 32) !=
        "d5e285683cd4efc02d021a5c62014694958901005d6f71e89e0989fac77e4072")
        return false;
    bhydra::Sha512::one_shot(edge.data(), 111, out);
    if (hex(out, 64) !=
        "9a2a120825c2319867758ec277924f6faa254968bf752046dacdd948d8ad299b"
        "10359fd04bfd7d3810b5fa1b16a294236138baff981cbb85248478053ac4d3dd")
        return false;
    return true;
}

int selftest_state = -1;   // -1 не проверяли, 1 наши байты, 0 чужие

bool ready() {
    if (selftest_state < 0) selftest_state = hashes_are_ours() ? 1 : 0;
    return selftest_state == 1;
}

}  // namespace

extern "C" {

/// Размер листа в байтах для режима (32 или 64). 0 — режим неизвестен.
int bhydra_xmss_leaf_size(int alg) {
    return (int)bhydra::xmss::leaf_size(alg);
}

/// Самопроверка хешей библиотеки. 1 — байты наши.
int bhydra_xmss_selftest() { return ready() ? 1 : 0; }

/// Листья [start, start + count) в `out` (count · leaf_size байт).
///
/// `threads` = 0 означает «реши сам». ⚠️ Python такого не присылает: там
/// действует то же правило, что у майнера, — все ядра КРОМЕ ОДНОГО, иначе
/// создание кошелька делает машину неотзывчивой на минуты.
///
/// Возвращает 1 при успехе. 0 — чужой режим, слишком длинный seed или
/// непройденная самопроверка хешей.
int bhydra_xmss_leaves(const uint8_t *seed, int seed_len, int alg,
                       uint32_t start, uint32_t count, int threads,
                       uint8_t *out) {
    if (!ready()) return 0;
    const size_t n = bhydra::xmss::leaf_size(alg);
    if (n == 0 || seed == nullptr || out == nullptr) return 0;
    if (seed_len < 0 || (size_t)seed_len > bhydra::xmss::kMaxSeed) return 0;
    if (count == 0) return 1;

    unsigned workers = threads > 0 ? (unsigned)threads
                                   : std::max(1u, std::thread::hardware_concurrency());
    workers = std::min<unsigned>(workers, count);

    if (workers <= 1) {
        return bhydra::xmss::leaves(seed, (size_t)seed_len, alg, start, count,
                                    out) ? 1 : 0;
    }

    std::vector<std::thread> pool;
    std::vector<char> ok(workers, 0);
    const uint32_t share = count / workers;
    const uint32_t extra = count % workers;
    uint32_t at = 0;
    for (unsigned w = 0; w < workers; ++w) {
        const uint32_t mine = share + (w < extra ? 1u : 0u);
        const uint32_t from = at;
        at += mine;
        pool.emplace_back([&, w, from, mine]() {
            ok[w] = bhydra::xmss::leaves(seed, (size_t)seed_len, alg,
                                         start + from, mine,
                                         out + (size_t)from * n) ? 1 : 0;
        });
    }
    for (auto &t : pool) t.join();
    for (char flag : ok)
        if (!flag) return 0;
    return 1;
}

}  // extern "C"
