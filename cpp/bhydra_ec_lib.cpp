// bhydra_ec_lib.cpp — проверка ECDSA-подписи из НАШЕЙ реализации, для Python.
//
// ЗАЧЕМ. Приём одной транзакции стоит ~23,6 мс, и 94% из них — проверка
// подписи на чистом Python. Хеш там всего 5%. Значит ускорять надо кривую, а
// не SHA.
//
// ПОЧЕМУ НЕ БИБЛИОТЕКА. Сторонняя libsecp256k1 (coincurve) в проекте уже
// поддержана как необязательный ускоритель, но здесь другой смысл: та же
// работа делается СВОИМ кодом из `bhydra_ec.hpp` — тем самым, что уже считает
// рукопожатие транспорта. Никакой чужой криптографии не добавляется, просто
// наш алгоритм компилируется.
//
// ПОЧЕМУ БИБЛИОТЕКА, А НЕ КОМАНДА. У майнера мост — отдельный процесс, и это
// нормально: он молотит секундами, запуск процесса теряется в фоне. Здесь
// наоборот — работы на полмиллисекунды, а запуск процесса стоит дороже самой
// проверки. Поэтому .so/.dll и ctypes: накладные расходы вызова — микросекунды.
//
// Сборка:
//     g++ -O2 -std=c++17 -shared -fPIC -I cpp
//         -o libbhydra_ec.so cpp/bhydra_ec_lib.cpp
//     x86_64-w64-mingw32-g++ -O2 -std=c++17 -shared -static -I cpp
//         -o bhydra_ec.dll cpp/bhydra_ec_lib.cpp
//
// ⚠️ Множество принимаемых подписей обязано СОВПАДАТЬ с чистым Python. Иначе
// узлы с собранной библиотекой и без неё разошлись бы в том, какая транзакция
// валидна, — а это раскол сети. Поэтому здесь повторены все те же проверки
// (точка на кривой, диапазон r и s), а Python перед включением прогоняет
// self-test на живых подписях.

#include "bhydra_ec.hpp"

#include <cstdint>

namespace {

using bhydra::Bytes;
using bhydra::U256;

// Числа приходят 32-байтовыми в порядке «старший первый» — тот же порядок, в
// котором их пишет Python (`int.to_bytes(32, "big")`).
U256 load(const uint8_t* value) {
    return U256::from_bytes(value, 32);
}

}  // namespace

extern "C" {

/// Проверка уравнения ECDSA. 1 — подпись верна, 0 — нет.
///
/// Принимает РОВНО то же, что `wallet._VERIFY_CORE`: координаты публичного
/// ключа, число z (хеш сообщения) и пару (r, s). Хеширование остаётся в
/// Python, потому что там оно уже сделано — переносить его сюда значило бы
/// считать SHA-512 дважды.
int bhydra_ec_verify(const uint8_t* x, const uint8_t* y, const uint8_t* z,
                     const uint8_t* r_raw, const uint8_t* s_raw) {
    if (x == nullptr || y == nullptr || z == nullptr ||
        r_raw == nullptr || s_raw == nullptr) {
        return 0;
    }
    bhydra::Point pub;
    pub.x = load(x);
    pub.y = load(y);
    pub.infinity = false;
    // Те же проверки, что и в чистом Python: точка обязана лежать на кривой
    // (защита от invalid-curve), r и s — быть в диапазоне.
    if (!bhydra::on_curve(pub)) {
        return 0;
    }
    const U256 r = load(r_raw);
    const U256 s = load(s_raw);
    if (r.is_zero() || s.is_zero()) {
        return 0;
    }
    const U256 n = bhydra::group_n();
    if (U256::cmp(r, n) >= 0 || U256::cmp(s, n) >= 0) {
        return 0;
    }

    const U256 zvalue = load(z);
    uint64_t zwide[8] = {zvalue.v[0], zvalue.v[1], zvalue.v[2], zvalue.v[3],
                         0, 0, 0, 0};
    const U256 zmod = bhydra::reduce_mod(zwide, n);
    const U256 w = bhydra::inverse_n(s);
    const bhydra::Point p1 = bhydra::scalar_mul(bhydra::mul_n(zmod, w),
                                                bhydra::generator());
    const bhydra::Point p2 = bhydra::scalar_mul(bhydra::mul_n(r, w), pub);
    const bhydra::Jacobian sum = bhydra::jacobian_add(bhydra::to_jacobian(p1),
                                                      bhydra::to_jacobian(p2));
    const bhydra::Point point = bhydra::to_affine(sum);
    if (point.infinity) {
        return 0;
    }
    uint64_t xwide[8] = {point.x.v[0], point.x.v[1], point.x.v[2],
                         point.x.v[3], 0, 0, 0, 0};
    return bhydra::reduce_mod(xwide, n) == r ? 1 : 0;
}

/// Проверка самой библиотеки без Python: подписываем и проверяем свою же
/// подпись, затем убеждаемся, что испорченное сообщение отвергается.
/// 0 — всё в порядке, отрицательное — что именно сломалось.
int bhydra_ec_selftest(void) {
    const Bytes secret(32, 0x11);
    const U256 priv = U256::from_bytes(secret.data(), 32);
    const Bytes message = {'b', '-', 'h', 'y', 'd', 'r', 'a'};
    const Bytes signature = bhydra::sign(priv, message);
    if (signature.size() != 64) {
        return -1;
    }
    const Bytes public_key = bhydra::public_key_bytes(priv);
    if (!bhydra::verify(public_key, message, signature)) {
        return -2;                       // свою же подпись не принял
    }
    const Bytes tampered = {'b', '-', 'h', 'y', 'd', 'r', 'A'};
    if (bhydra::verify(public_key, tampered, signature)) {
        return -3;                       // чужое сообщение принял
    }
    return 0;
}

}  // extern "C"
