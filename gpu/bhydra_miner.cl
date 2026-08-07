/*
 * bhydra_miner.cl — перебор nonce на видеокарте (OpenCL C).
 *
 * Третья реализация ОДНОГО И ТОГО ЖЕ SHA-512: первая на Python (`sha2.py`),
 * вторая на C++ (`cpp/bhydra_hash.hpp`), эта — для GPU. Хеш обязан совпадать
 * с ними побитово, иначе найденный блок сеть отвергнет.
 *
 * ПОЧЕМУ ЭТО ВООБЩЕ РАБОТАЕТ НА GPU. Перебор nonce — идеальная задача для
 * видеокарты: тысячи независимых попыток, между которыми нет никакого обмена
 * данными. Каждый work-item берёт свой nonce, считает хеш и молча выходит,
 * если не повезло. Общая память нужна ровно на одно слово — флаг «нашли».
 *
 * ⚠️ ЗАГОЛОВОК СОБИРАЕТСЯ КАК В PYTHON: `prefix + str(nonce)`, где nonce —
 * ДЕСЯТИЧНАЯ строка, а не 8 байт числа. Это не мелочь: возьми мы двоичный
 * nonce, хеш совпал бы сам с собой, но не с остальной сетью, и каждый
 * найденный блок отвергался бы всеми.
 *
 * ⚠️ MIDSTATE. Префикс заголовка при переборе не меняется, поэтому целые
 * 128-байтные блоки из него хост сжимает ОДИН раз и передаёт сюда готовое
 * состояние. Ядру остаётся хвост префикса плюс цифры nonce — обычно один
 * блок вместо двух-трёх.
 */

#define ROTR(x, n) (((x) >> (n)) | ((x) << (64 - (n))))
#define CH(x, y, z) (((x) & (y)) ^ (~(x) & (z)))
#define MAJ(x, y, z) (((x) & (y)) ^ ((x) & (z)) ^ ((y) & (z)))
#define BSIG0(x) (ROTR(x, 28) ^ ROTR(x, 34) ^ ROTR(x, 39))
#define BSIG1(x) (ROTR(x, 14) ^ ROTR(x, 18) ^ ROTR(x, 41))
#define SSIG0(x) (ROTR(x, 1) ^ ROTR(x, 8) ^ ((x) >> 7))
#define SSIG1(x) (ROTR(x, 19) ^ ROTR(x, 61) ^ ((x) >> 6))

/* Дробные части кубических корней первых 80 простых (FIPS 180-4). */
__constant ulong K[80] = {
    0x428a2f98d728ae22UL, 0x7137449123ef65cdUL, 0xb5c0fbcfec4d3b2fUL, 0xe9b5dba58189dbbcUL,
    0x3956c25bf348b538UL, 0x59f111f1b605d019UL, 0x923f82a4af194f9bUL, 0xab1c5ed5da6d8118UL,
    0xd807aa98a3030242UL, 0x12835b0145706fbeUL, 0x243185be4ee4b28cUL, 0x550c7dc3d5ffb4e2UL,
    0x72be5d74f27b896fUL, 0x80deb1fe3b1696b1UL, 0x9bdc06a725c71235UL, 0xc19bf174cf692694UL,
    0xe49b69c19ef14ad2UL, 0xefbe4786384f25e3UL, 0x0fc19dc68b8cd5b5UL, 0x240ca1cc77ac9c65UL,
    0x2de92c6f592b0275UL, 0x4a7484aa6ea6e483UL, 0x5cb0a9dcbd41fbd4UL, 0x76f988da831153b5UL,
    0x983e5152ee66dfabUL, 0xa831c66d2db43210UL, 0xb00327c898fb213fUL, 0xbf597fc7beef0ee4UL,
    0xc6e00bf33da88fc2UL, 0xd5a79147930aa725UL, 0x06ca6351e003826fUL, 0x142929670a0e6e70UL,
    0x27b70a8546d22ffcUL, 0x2e1b21385c26c926UL, 0x4d2c6dfc5ac42aedUL, 0x53380d139d95b3dfUL,
    0x650a73548baf63deUL, 0x766a0abb3c77b2a8UL, 0x81c2c92e47edaee6UL, 0x92722c851482353bUL,
    0xa2bfe8a14cf10364UL, 0xa81a664bbc423001UL, 0xc24b8b70d0f89791UL, 0xc76c51a30654be30UL,
    0xd192e819d6ef5218UL, 0xd69906245565a910UL, 0xf40e35855771202aUL, 0x106aa07032bbd1b8UL,
    0x19a4c116b8d2d0c8UL, 0x1e376c085141ab53UL, 0x2748774cdf8eeb99UL, 0x34b0bcb5e19b48a8UL,
    0x391c0cb3c5c95a63UL, 0x4ed8aa4ae3418acbUL, 0x5b9cca4f7763e373UL, 0x682e6ff3d6b2b8a3UL,
    0x748f82ee5defb2fcUL, 0x78a5636f43172f60UL, 0x84c87814a1f0ab72UL, 0x8cc702081a6439ecUL,
    0x90befffa23631e28UL, 0xa4506cebde82bde9UL, 0xbef9a3f7b2c67915UL, 0xc67178f2e372532bUL,
    0xca273eceea26619cUL, 0xd186b8c721c0c207UL, 0xeada7dd6cde0eb1eUL, 0xf57d4f7fee6ed178UL,
    0x06f067aa72176fbaUL, 0x0a637dc5a2c898a6UL, 0x113f9804bef90daeUL, 0x1b710b35131c471bUL,
    0x28db77f523047d84UL, 0x32caab7b40c72493UL, 0x3c9ebe0a15c9bebcUL, 0x431d67c49c100d4cUL,
    0x4cc5d4becb3e42b6UL, 0x597f299cfc657e2aUL, 0x5fcb6fab3ad6faecUL, 0x6c44198c4a475817UL,
};

/*
 * Сжатие одного 128-байтного блока. Состояние на входе и выходе — 8 слов.
 *
 * ⚠️ РАСПИСАНИЕ ЖИВЁТ В ОКНЕ ИЗ 16 СЛОВ, а не в массиве на 80. Формула
 * W[i] = σ1(W[i−2]) + W[i−7] + σ0(W[i−15]) + W[i−16] заглядывает назад не
 * дальше чем на 16, поэтому старые слова не нужны и место под них не нужно
 * тоже. Разница не косметическая: 640 байт приватной памяти на work-item
 * против 128. На видеокарте приватная память — это регистры, и когда их не
 * хватает, компилятор выселяет расписание в глобальную память, после чего
 * каждый раунд упирается в неё. Замер на CPU-устройстве (там регистров вдоволь
 * и эффект слабее всего): при 65536 work-item 4,13 → 6,62 Мхеш/с,
 * и провал на больших запусках исчез совсем.
 */
static void compress(ulong *state, const ulong *block)
{
    ulong w[16];
    #pragma unroll
    for (int i = 0; i < 16; i++) {
        w[i] = block[i];
    }

    ulong a = state[0], b = state[1], c = state[2], d = state[3];
    ulong e = state[4], f = state[5], g = state[6], h = state[7];

    for (int i = 0; i < 80; i++) {
        const int j = i & 15;
        if (i >= 16) {
            /* W[i−16] лежит ровно там, куда пишем, — прибавляем на месте. */
            w[j] += SSIG0(w[(i + 1) & 15]) + w[(i + 9) & 15]
                  + SSIG1(w[(i + 14) & 15]);
        }
        ulong t1 = h + BSIG1(e) + CH(e, f, g) + K[i] + w[j];
        ulong t2 = BSIG0(a) + MAJ(a, b, c);
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }

    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

/*
 * Перебор.
 *
 * midstate   — состояние SHA-512 после целых блоков префикса (8 слов);
 * tail       — остаток префикса, меньше 128 байт;
 * tail_len   — его длина;
 * prefix_len — ПОЛНАЯ длина префикса (нужна для поля длины в набивке);
 * target     — порог, 8 слов, старшее первым (как байты хеша);
 * start      — первый nonce этого запуска;
 * per_item   — сколько nonce берёт на себя один work-item;
 * out        — [0] флаг «нашли», [1] и [2] младшая и старшая половины nonce;
 * winner     — 64 байта дайджеста победителя.
 *
 * ⚠️ nonce отдаётся ДВУМЯ 32-битными словами, а не одним 64-битным: 64-битные
 * атомарные операции — необязательное расширение OpenCL, а обычная запись в
 * ulong не гарантирует, что обе половины окажутся от одного и того же
 * победителя. Флаг ставится через atomic_cmpxchg, поэтому пишет ровно один
 * work-item, и половинки заведомо согласованы.
 *
 * ⚠️ Дайджест отдаётся НАРУЖУ намеренно: Python пересчитает хеш своим кодом и
 * сверит. Верни мы только nonce, сверять было бы не с чем — Python сравнил бы
 * свой хеш сам с собой, и ядро с ЧУЖИМ SHA-512 прошло бы проверку незаметно.
 */
__kernel void bhydra_mine(
    __global const ulong *midstate,
    __global const uchar *tail,
    const uint tail_len,
    const ulong prefix_len,
    __global const ulong *target,
    const ulong start,
    const uint per_item,
    __global volatile uint *out,
    __global uchar *winner)
{
    /* Хвост префикса и цель одни на всех — забираем в приватную память. */
    uchar head[128];
    for (uint i = 0; i < tail_len; i++) {
        head[i] = tail[i];
    }
    ulong limit[8];
    for (int i = 0; i < 8; i++) {
        limit[i] = target[i];
    }
    ulong base[8];
    for (int i = 0; i < 8; i++) {
        base[i] = midstate[i];
    }

    const ulong first = start + (ulong)get_global_id(0) * (ulong)per_item;

    for (uint step = 0; step < per_item; step++) {
        if (out[0] != 0) {
            return;                       /* кто-то уже нашёл — выходим */
        }
        const ulong nonce = first + step;

        /* Десятичные цифры nonce — ровно то, что даёт str(nonce) в Python. */
        uchar digits[20];
        uint ndigits = 0;
        ulong value = nonce;
        do {
            digits[ndigits++] = (uchar)('0' + (uint)(value % 10UL));
            value /= 10UL;
        } while (value != 0UL);

        uchar buffer[256];
        uint length = tail_len;
        for (uint i = 0; i < tail_len; i++) {
            buffer[i] = head[i];
        }
        for (uint i = 0; i < ndigits; i++) {
            buffer[length++] = digits[ndigits - 1 - i];   /* старшая цифра первой */
        }

        /* Набивка SHA-512: 0x80, нули, 128-битная длина в битах. */
        const ulong bits = (prefix_len + (ulong)ndigits) * 8UL;
        const uint blocks = (length + 1 + 16 <= 128) ? 1 : 2;
        const uint padded = blocks * 128;
        buffer[length] = 0x80;
        for (uint i = length + 1; i < padded; i++) {
            buffer[i] = 0;
        }
        /* Старшие 64 бита длины всегда нули: заголовок короче 2^61 байт. */
        for (int i = 0; i < 8; i++) {
            buffer[padded - 1 - i] = (uchar)(bits >> (8 * i));
        }

        ulong state[8];
        for (int i = 0; i < 8; i++) {
            state[i] = base[i];
        }
        for (uint b = 0; b < blocks; b++) {
            ulong block[16];
            for (int i = 0; i < 16; i++) {
                const uint at = b * 128 + i * 8;
                block[i] = ((ulong)buffer[at] << 56) | ((ulong)buffer[at + 1] << 48)
                         | ((ulong)buffer[at + 2] << 40) | ((ulong)buffer[at + 3] << 32)
                         | ((ulong)buffer[at + 4] << 24) | ((ulong)buffer[at + 5] << 16)
                         | ((ulong)buffer[at + 6] << 8) | (ulong)buffer[at + 7];
            }
            compress(state, block);
        }

        /* Сравнение с порогом: слово за словом, старшее первым — это ровно то
         * же, что побайтовое сравнение дайджеста с целью в Python. */
        int win = 0;
        for (int i = 0; i < 8; i++) {
            if (state[i] < limit[i]) { win = 1; break; }
            if (state[i] > limit[i]) { win = 0; break; }
            win = 1;                      /* равно — годится (хеш ≤ цели) */
        }
        if (win) {
            /* Пишет ровно один work-item — тот, кто first перевёл флаг из 0 в 1.
             * Иначе половинки nonce и байты дайджеста могли бы оказаться от
             * разных победителей. */
            if (atomic_cmpxchg(&out[0], 0u, 1u) == 0u) {
                for (int i = 0; i < 8; i++) {
                    for (int j = 0; j < 8; j++) {
                        winner[i * 8 + j] = (uchar)(state[i] >> (56 - 8 * j));
                    }
                }
                out[1] = (uint)(nonce & 0xFFFFFFFFUL);
                out[2] = (uint)(nonce >> 32);
            }
            return;
        }
    }
}
