// bhydra_secure.hpp — «B-hydra TLS»: тот же шифрованный транспорт, что в
// b_hydra/secure.py, но на C++ и без зависимостей.
//
// Это ВТОРАЯ реализация одного протокола, а не новый протокол. Смысл именно в
// этом: пока реализация одна, «спецификация» — это то, что делает Python.
// Второй независимый код на другом языке заставляет протокол быть описанным, и
// расхождение сразу видно. Ровно так уже сделана браузерная подпись
// (bhydra-sign.js), и именно так там нашлась настоящая ошибка вывода нонса.
//
// Сверка идёт байт-в-байт в tests/test_cpp_secure.py: хеши, ECDH, подписи,
// вывод ключей, кадры — и живое рукопожатие C++ ↔ Python через сокет.
//
// Названия «TLS» в стандартном смысле здесь нет: это не RFC 8446, сертификатов
// и удостоверяющих центров нет. Доверие берётся из первого контакта (TOFU).
#ifndef BHYDRA_SECURE_HPP
#define BHYDRA_SECURE_HPP

#include <cerrno>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>

#include "bhydra_ec.hpp"

#if defined(_WIN32)
#include <winsock2.h>
#else
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace bhydra {

// ---------------------------------------------------------------- константы
// Значения обязаны совпадать с secure.py — это правила протокола, а не выбор
// реализации.
inline const char *kMagic() { return "BHYE1"; }
constexpr size_t kMagicLen = 5;
constexpr size_t kPublicKeyLen = 65;              // несжатая точка 0x04||X||Y
constexpr size_t kSignatureLen = 64;              // r||s
constexpr size_t kKeyLen = 32;
constexpr size_t kTagLen = 32;                    // усечённый HMAC-SHA512
constexpr size_t kSeqLen = 8;
constexpr size_t kMaxMessageSize = 32u * 1024u * 1024u;   // как в tcp.py

class HandshakeError : public std::runtime_error {
public:
    explicit HandshakeError(const std::string &what)
        : std::runtime_error(what) {}
};

class DecryptError : public std::runtime_error {
public:
    explicit DecryptError(const std::string &what) : std::runtime_error(what) {}
};

// ---------------------------------------------------------------- ключи сессии
struct SessionKeys {
    Bytes c2s, s2c, mac_c2s, mac_s2c;
};

// prk = HMAC(ключ = стенограмма, сообщение = общий секрет). Порядок аргументов
// именно такой — так же, как в secure.py: стенограмма играет роль соли.
inline SessionKeys derive_keys(const Bytes &shared, const Bytes &transcript) {
    Bytes prk = hmac_sha512(transcript, shared);
    auto label = [&](const char *text) {
        Bytes message(text, text + std::strlen(text));
        Bytes full = hmac_sha512(prk, message);
        return Bytes(full.begin(), full.begin() + kKeyLen);
    };
    SessionKeys keys;
    keys.c2s = label("BHY c2s cipher");
    keys.s2c = label("BHY s2c cipher");
    keys.mac_c2s = label("BHY c2s mac");
    keys.mac_s2c = label("BHY s2c mac");
    return keys;
}

inline Bytes keystream(const Bytes &key, uint64_t seq, size_t length) {
    Bytes seed = key;
    for (int i = kSeqLen - 1; i >= 0; --i)
        seed.push_back((uint8_t)(seq >> (8 * i)));
    return shake256(seed, length);
}

inline Bytes frame_mac(const Bytes &key, const Bytes &message) {
    Bytes full = hmac_sha512(key, message);
    return Bytes(full.begin(), full.begin() + kTagLen);
}

// Сравнение постоянного времени: побайтная проверка с ранним выходом
// подсказывает атакующему, сколько байтов тега он угадал.
inline bool equal_constant_time(const Bytes &a, const Bytes &b) {
    if (a.size() != b.size()) return false;
    uint8_t diff = 0;
    for (size_t i = 0; i < a.size(); ++i) diff |= (uint8_t)(a[i] ^ b[i]);
    return diff == 0;
}

// ---------------------------------------------------------------- сессия
class Session {
public:
    Session() = default;
    Session(const SessionKeys &keys, bool is_client,
            const std::string &peer_key = std::string())
        : peer_key_(peer_key) {
        out_ = is_client ? keys.c2s : keys.s2c;
        in_ = is_client ? keys.s2c : keys.c2s;
        out_mac_ = is_client ? keys.mac_c2s : keys.mac_s2c;
        in_mac_ = is_client ? keys.mac_s2c : keys.mac_c2s;
    }

    const std::string &peer_key() const { return peer_key_; }

    // Только для сверки с Python: позволяет начать с произвольного номера
    // кадра, чтобы проверить контрольные значения на больших seq. «Догонять»
    // номер настоящими кадрами нельзя — для seq = 2^32 это четыре миллиарда
    // шифрований.
    void set_sequence(uint64_t out_seq, uint64_t in_seq) {
        out_seq_ = out_seq;
        in_seq_ = in_seq;
    }

    Bytes encrypt(const Bytes &payload) {
        uint64_t seq = out_seq_++;
        Bytes stream = keystream(out_, seq, payload.size());
        Bytes frame;
        frame.reserve(kSeqLen + payload.size() + kTagLen);
        for (int i = kSeqLen - 1; i >= 0; --i)
            frame.push_back((uint8_t)(seq >> (8 * i)));
        for (size_t i = 0; i < payload.size(); ++i)
            frame.push_back((uint8_t)(payload[i] ^ stream[i]));
        Bytes tag = frame_mac(out_mac_, frame);
        frame.insert(frame.end(), tag.begin(), tag.end());
        return frame;
    }

    Bytes decrypt(const Bytes &frame) {
        if (frame.size() < kSeqLen + kTagLen)
            throw DecryptError("кадр короче заголовка");
        Bytes head_and_cipher(frame.begin(), frame.end() - kTagLen);
        Bytes tag(frame.end() - kTagLen, frame.end());
        // Тег — ДО расшифровки (Encrypt-then-MAC).
        if (!equal_constant_time(tag, frame_mac(in_mac_, head_and_cipher)))
            throw DecryptError("тег кадра не совпал");
        uint64_t seq = 0;
        for (size_t i = 0; i < kSeqLen; ++i) seq = (seq << 8) | frame[i];
        // Номер обязан идти строго подряд: «не меньше прошлого» разрешало бы
        // ВЫБРОСИТЬ кадр из потока, а это тоже подмена.
        if (seq != in_seq_) throw DecryptError("кадр вне очереди");
        ++in_seq_;
        size_t size = head_and_cipher.size() - kSeqLen;
        Bytes stream = keystream(in_, seq, size);
        Bytes payload(size);
        for (size_t i = 0; i < size; ++i)
            payload[i] = (uint8_t)(head_and_cipher[kSeqLen + i] ^ stream[i]);
        return payload;
    }

private:
    Bytes out_, in_, out_mac_, in_mac_;
    uint64_t out_seq_ = 0, in_seq_ = 0;
    std::string peer_key_;
};

// ---------------------------------------------------------------- кадрирование
// Сообщение с 4-байтовым префиксом длины — как tcp.py.
inline void write_all(int fd, const uint8_t *data, size_t size) {
    size_t at = 0;
    while (at < size) {
#if defined(_WIN32)
        int written = ::send(fd, (const char *)data + at, (int)(size - at), 0);
#else
        ssize_t written = ::send(fd, data + at, size - at, 0);
#endif
        if (written <= 0) {
            if (errno == EINTR) continue;
            throw HandshakeError("сокет закрыт при записи");
        }
        at += (size_t)written;
    }
}

inline void send_message(int fd, const Bytes &data) {
    uint8_t header[4];
    for (int i = 0; i < 4; ++i) header[i] = (uint8_t)(data.size() >> (8 * (3 - i)));
    write_all(fd, header, 4);
    if (!data.empty()) write_all(fd, data.data(), data.size());
}

inline bool read_exactly(int fd, uint8_t *data, size_t size) {
    size_t at = 0;
    while (at < size) {
#if defined(_WIN32)
        int got = ::recv(fd, (char *)data + at, (int)(size - at), 0);
#else
        ssize_t got = ::recv(fd, data + at, size - at, 0);
#endif
        if (got == 0) return false;                 // пир закрыл соединение
        if (got < 0) {
            if (errno == EINTR) continue;
            return false;
        }
        at += (size_t)got;
    }
    return true;
}

inline Bytes recv_message(int fd) {
    uint8_t header[4];
    if (!read_exactly(fd, header, 4)) return Bytes();
    size_t size = 0;
    for (int i = 0; i < 4; ++i) size = (size << 8) | header[i];
    // Слишком большое сообщение отвергаем НЕ читая тело: иначе пир одним
    // четырёхбайтовым заголовком заказывает у нас гигабайты памяти.
    if (size > kMaxMessageSize) return Bytes();
    Bytes data(size);
    if (size && !read_exactly(fd, data.data(), size)) return Bytes();
    return data;
}

// ---------------------------------------------------------------- ECDH
inline Bytes shared_secret(const U256 &priv, const Bytes &peer_public) {
    if (peer_public.size() != kPublicKeyLen || peer_public[0] != 0x04)
        throw HandshakeError("некорректный публичный ключ пира");
    Point point;
    point.x = U256::from_bytes(peer_public.data() + 1, 32);
    point.y = U256::from_bytes(peer_public.data() + 33, 32);
    // Точка ОБЯЗАНА лежать на кривой: иначе умножение на подсунутой точке
    // малого порядка выдаёт секрет по частям (invalid-curve атака).
    if (point.x.is_zero() || point.y.is_zero() || !on_curve(point))
        throw HandshakeError("публичный ключ пира не на кривой");
    Point result = scalar_mul(priv, point);
    if (result.infinity) throw HandshakeError("вырожденный общий секрет");
    return result.x.to_bytes();
}

// Источник случайности для одноразовых ключей. Без зависимостей: системный
// генератор напрямую. Слабая случайность здесь ломает прямую секретность,
// поэтому подмена на «псевдо» недопустима — при ошибке лучше отказ.
inline U256 random_scalar() {
#if defined(_WIN32)
    throw HandshakeError("нужен системный ГСЧ (BCryptGenRandom)");
#else
    U256 value;
    FILE *source = std::fopen("/dev/urandom", "rb");
    if (!source) throw HandshakeError("нет /dev/urandom");
    uint8_t raw[32];
    size_t got = std::fread(raw, 1, sizeof(raw), source);
    std::fclose(source);
    if (got != sizeof(raw)) throw HandshakeError("ГСЧ вернул мало байтов");
    value = U256::from_bytes(raw, sizeof(raw));
    if (value.is_zero() || U256::cmp(value, group_n()) >= 0) {
        U256 one(1);                                // вырожденный случай
        return one;
    }
    return value;
#endif
}

// ---------------------------------------------------------------- рукопожатие
// Клиент: посылает метку и свой эфемерный ключ, получает эфемерный ключ сервера,
// его долговременный ключ и подпись стенограммы.
inline Session client_handshake(int fd, const std::string &expect_key = "") {
    U256 eph_priv = random_scalar();
    Bytes eph_pub = public_key_bytes(eph_priv);
    Bytes hello(kMagic(), kMagic() + kMagicLen);
    hello.insert(hello.end(), eph_pub.begin(), eph_pub.end());
    send_message(fd, hello);

    Bytes reply = recv_message(fd);
    if (reply.size() != kPublicKeyLen * 2 + kSignatureLen)
        throw HandshakeError("пир не ответил на рукопожатие");
    Bytes peer_eph(reply.begin(), reply.begin() + kPublicKeyLen);
    Bytes peer_id(reply.begin() + kPublicKeyLen,
                  reply.begin() + kPublicKeyLen * 2);
    Bytes signature(reply.begin() + kPublicKeyLen * 2, reply.end());

    Bytes transcript(kMagic(), kMagic() + kMagicLen);
    transcript.insert(transcript.end(), eph_pub.begin(), eph_pub.end());
    transcript.insert(transcript.end(), peer_eph.begin(), peer_eph.end());
    transcript.insert(transcript.end(), peer_id.begin(), peer_id.end());

    if (!verify(peer_id, transcript, signature))
        throw HandshakeError("подпись рукопожатия не сошлась");
    std::string peer_key = to_hex(peer_id);
    // Ключ сверяем ПОСЛЕ подписи: иначе о совпадении судили бы по
    // неподтверждённому полю, которое кто угодно может скопировать.
    if (!expect_key.empty() && peer_key != expect_key)
        throw HandshakeError("ключ пира не совпал с запомненным");
    return Session(derive_keys(shared_secret(eph_priv, peer_eph), transcript),
                   true, peer_key);
}

// Сервер: первый кадр читает вызывающий (ему нужно решить, шифрованный клиент
// или открытый), поэтому он передаётся сюда готовым.
inline bool is_handshake(const Bytes &frame) {
    return frame.size() >= kMagicLen &&
           std::memcmp(frame.data(), kMagic(), kMagicLen) == 0;
}

inline Session server_handshake(int fd, const Bytes &first_frame,
                                const U256 &identity_priv) {
    if (!is_handshake(first_frame))
        throw HandshakeError("это не зашифрованное рукопожатие");
    if (first_frame.size() != kMagicLen + kPublicKeyLen)
        throw HandshakeError("некорректная длина рукопожатия");
    Bytes peer_eph(first_frame.begin() + kMagicLen, first_frame.end());
    U256 eph_priv = random_scalar();
    Bytes eph_pub = public_key_bytes(eph_priv);
    Bytes id_pub = public_key_bytes(identity_priv);

    Bytes transcript(kMagic(), kMagic() + kMagicLen);
    transcript.insert(transcript.end(), peer_eph.begin(), peer_eph.end());
    transcript.insert(transcript.end(), eph_pub.begin(), eph_pub.end());
    transcript.insert(transcript.end(), id_pub.begin(), id_pub.end());

    Bytes signature = sign(identity_priv, transcript);
    Bytes reply = eph_pub;
    reply.insert(reply.end(), id_pub.begin(), id_pub.end());
    reply.insert(reply.end(), signature.begin(), signature.end());
    send_message(fd, reply);
    return Session(derive_keys(shared_secret(eph_priv, peer_eph), transcript),
                   false);
}

}  // namespace bhydra
#endif
