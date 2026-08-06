// bhydra_miner.cpp — нативный майнер B-hydra: перебор nonce на всех ядрах.
//
// ЗАЧЕМ. Perebor — единственное место в проекте, где Python считает САМ и много:
// миллионы SHA-512 подряд. Даже с midstate и сравнением байтов чистый Python
// даёт ~3 тыс. хешей/с, hashlib ~1 млн, и всё это в один поток из-за GIL.
// Здесь тот же алгоритм на C++ и на всех ядрах сразу.
//
// ЧТО ЗДЕСЬ НЕ МЕНЯЕТСЯ. Формат заголовка блока и хеш — ни на бит. Майнер
// получает готовый ПРЕФИКС заголовка (всё, кроме nonce) и подставляет к нему
// десятичный nonce ровно так же, как Python: `prefix + str(nonce)`. Иначе
// найденный блок не прошёл бы проверку у собственного узла.
//
// Приёмы те же, что и в Python-версии, — они не «хитрости», а следствие того,
// как устроен заголовок:
//   * midstate: неизменная часть сжимается один раз, дальше копируется
//     состояние (Sha512 копируемый);
//   * сравнение 64 байт вместо перевода хеша в число.
//
// Сборка:
//     g++ -O2 -std=c++17 -pthread -I cpp -o bhydra_miner cpp/bhydra_miner.cpp
//
// Команды (вывод — JSON для Python):
//     selftest                   контрольные векторы, без сети и без потоков
//     bench [сек] [потоков]      скорость перебора
//     mine <префикс-hex> <цель-hex> <старт-nonce> <потоков> <сек>
//
// Префикс передаётся в hex, чтобы не зависеть от кодировок и кавычек оболочки.
// Цель — 128 hex-символов (64 байта, старший первый).
//
// ⚠️ nonce здесь 64-битный. Python допускает сколь угодно большой, но 2^64
// попыток при миллионе хешей в секунду — это полмиллиона лет, так что предел
// недостижим. Если он всё же исчерпан, майнер честно сообщает об этом, а не
// заворачивается по кругу на уже проверенные значения.

#include "bhydra_hash.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <algorithm>
#include <cctype>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace {

using bhydra::Bytes;
using bhydra::Sha512;

constexpr uint64_t kNonceCeiling = UINT64_MAX;

double now_seconds() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double>(clock::now().time_since_epoch()).count();
}

bool from_hex(const std::string& text, Bytes* out) {
    if (text.size() % 2 != 0) {
        return false;               // нечётная длина — это не байты
    }
    out->clear();
    out->reserve(text.size() / 2);
    for (size_t i = 0; i < text.size(); i += 2) {
        int hi = std::isxdigit(static_cast<unsigned char>(text[i]))
                     ? std::stoi(text.substr(i, 1), nullptr, 16) : -1;
        int lo = std::isxdigit(static_cast<unsigned char>(text[i + 1]))
                     ? std::stoi(text.substr(i + 1, 1), nullptr, 16) : -1;
        if (hi < 0 || lo < 0) {
            return false;
        }
        out->push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return true;
}

std::string to_hex(const Bytes& data) {
    static const char* digits = "0123456789abcdef";
    std::string out;
    out.reserve(data.size() * 2);
    for (uint8_t byte : data) {
        out.push_back(digits[byte >> 4]);
        out.push_back(digits[byte & 0x0F]);
    }
    return out;
}

int fail(const std::string& reason) {
    std::printf("{\"error\": \"%s\"}\n", reason.c_str());
    return 1;
}

// Результат перебора, общий для всех потоков.
struct Search {
    std::atomic<bool> found{false};
    std::atomic<bool> stop{false};
    std::atomic<uint64_t> attempts{0};
    std::atomic<uint64_t> winner{0};
    std::string hash;
    std::mutex hash_lock;
};

// Один поток перебирает свою подпоследовательность: start + i, +stride, …
// Так потоки не пересекаются и не нужен общий счётчик на каждой попытке —
// атомарный инкремент на каждый хеш съел бы весь выигрыш от многопоточности.
void grind(const Sha512& base, const Bytes& target, uint64_t start,
           uint64_t stride, double deadline, Search* search) {
    uint64_t local = 0;
    const uint64_t kReportEvery = 4096;
    for (uint64_t nonce = start; !search->found.load(std::memory_order_relaxed)
                                 && !search->stop.load(std::memory_order_relaxed);
         nonce += stride) {
        const std::string tail = std::to_string(nonce);
        Sha512 attempt = base;                       // midstate: копия состояния
        attempt.update(reinterpret_cast<const uint8_t*>(tail.data()), tail.size());
        const Bytes digest = attempt.digest();
        ++local;

        if (!std::lexicographical_compare(target.begin(), target.end(),
                                          digest.begin(), digest.end())) {
            // digest <= target — блок найден.
            bool expected = false;
            if (search->found.compare_exchange_strong(expected, true)) {
                std::lock_guard<std::mutex> guard(search->hash_lock);
                search->winner.store(nonce);
                search->hash = to_hex(digest);
            }
            break;
        }
        if (local % kReportEvery == 0) {
            search->attempts.fetch_add(kReportEvery, std::memory_order_relaxed);
            local = 0;
            if (now_seconds() >= deadline) {
                search->stop.store(true, std::memory_order_relaxed);
                break;
            }
        }
        // Переполнение 64 бит: дальше идти некуда, иначе пойдём по кругу.
        if (nonce > kNonceCeiling - stride) {
            search->stop.store(true, std::memory_order_relaxed);
            break;
        }
    }
    search->attempts.fetch_add(local, std::memory_order_relaxed);
}

int cmd_mine(const std::string& prefix_hex, const std::string& target_hex,
             uint64_t start, unsigned threads, double seconds) {
    Bytes prefix;
    Bytes target;
    if (!from_hex(prefix_hex, &prefix)) {
        return fail("префикс заголовка не hex");
    }
    if (!from_hex(target_hex, &target) || target.size() != 64) {
        return fail("цель должна быть 128 hex-символов (64 байта)");
    }
    if (threads == 0) {
        threads = std::thread::hardware_concurrency();
        if (threads == 0) {
            threads = 1;
        }
    }

    Sha512 base;
    base.update(prefix.data(), prefix.size());

    Search search;
    const double started = now_seconds();
    const double deadline = started + (seconds > 0 ? seconds : 1e9);
    std::vector<std::thread> workers;
    workers.reserve(threads);
    for (unsigned i = 0; i < threads; ++i) {
        workers.emplace_back(grind, std::cref(base), std::cref(target),
                             start + i, static_cast<uint64_t>(threads), deadline,
                             &search);
    }
    for (std::thread& worker : workers) {
        worker.join();
    }
    const double elapsed = now_seconds() - started;
    const uint64_t attempts = search.attempts.load();

    if (search.found.load()) {
        std::printf("{\"found\": true, \"nonce\": %llu, \"hash\": \"%s\", "
                    "\"attempts\": %llu, \"seconds\": %.6f, \"threads\": %u}\n",
                    static_cast<unsigned long long>(search.winner.load()),
                    search.hash.c_str(),
                    static_cast<unsigned long long>(attempts), elapsed, threads);
        return 0;
    }
    // Не нашли за отведённое время — это НЕ ошибка. Возвращаем, докуда дошли,
    // чтобы вызывающий продолжил с того же места, а не начинал заново.
    std::printf("{\"found\": false, \"attempts\": %llu, \"seconds\": %.6f, "
                "\"next_nonce\": %llu, \"threads\": %u}\n",
                static_cast<unsigned long long>(attempts), elapsed,
                static_cast<unsigned long long>(start + attempts), threads);
    return 0;
}

int cmd_bench(double seconds, unsigned threads) {
    // Цель из одних нулей недостижима — значит меряется чистый перебор.
    const Bytes target(64, 0x00);
    std::string prefix_hex;
    for (int i = 0; i < 200; ++i) {
        prefix_hex += "ab";
    }
    return cmd_mine(prefix_hex, to_hex(target), 0, threads, seconds);
}

// Проверяет то, что можно проверить без Python: хеш совпадает с известным
// вектором, midstate даёт тот же результат, что и разовый хеш, а сравнение
// байтов ведёт себя как сравнение чисел.
int cmd_selftest() {
    const std::string message = "abc";
    const Bytes digest = bhydra::sha512(
        Bytes(message.begin(), message.end()));
    const std::string expected =
        "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
        "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f";
    if (to_hex(digest) != expected) {
        return fail("SHA-512(\\\"abc\\\") не совпал с вектором");
    }

    // midstate == разовый хеш на любом хвосте.
    const std::string prefix = "заголовок-блока-";
    Sha512 base;
    base.update(reinterpret_cast<const uint8_t*>(prefix.data()), prefix.size());
    for (uint64_t nonce : {0ULL, 1ULL, 12345ULL, 999999999999ULL}) {
        const std::string tail = std::to_string(nonce);
        Sha512 attempt = base;
        attempt.update(reinterpret_cast<const uint8_t*>(tail.data()), tail.size());
        const std::string whole = prefix + tail;
        const Bytes once = bhydra::sha512(Bytes(whole.begin(), whole.end()));
        if (to_hex(attempt.digest()) != to_hex(once)) {
            return fail("midstate разошёлся с разовым хешем");
        }
    }

    // hex туда-обратно, включая отказ от мусора.
    Bytes parsed;
    if (!from_hex("00ff10", &parsed) || to_hex(parsed) != "00ff10") {
        return fail("hex туда-обратно испорчен");
    }
    for (const char* junk : {"0", "zz", "0g"}) {
        if (from_hex(junk, &parsed)) {
            return fail(std::string("принят мусорный hex: ") + junk);
        }
    }
    std::printf("{\"ok\": true, \"threads\": %u}\n",
                std::thread::hardware_concurrency());
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "использование: %s selftest | bench [сек] [потоков] | "
                     "mine <префикс-hex> <цель-hex> <старт> <потоков> <сек>\n",
                     argv[0]);
        return 2;
    }
    const std::string command = argv[1];
    if (command == "selftest") {
        return cmd_selftest();
    }
    if (command == "bench") {
        const double seconds = argc > 2 ? std::atof(argv[2]) : 2.0;
        const unsigned threads = argc > 3
                                     ? static_cast<unsigned>(std::atoi(argv[3]))
                                     : 0;
        return cmd_bench(seconds, threads);
    }
    if (command == "mine") {
        if (argc < 7) {
            std::fprintf(stderr, "mine требует 5 аргументов\n");
            return 2;
        }
        return cmd_mine(argv[2], argv[3], std::strtoull(argv[4], nullptr, 10),
                        static_cast<unsigned>(std::atoi(argv[5])),
                        std::atof(argv[6]));
    }
    std::fprintf(stderr, "неизвестная команда: %s\n", command.c_str());
    return 2;
}
