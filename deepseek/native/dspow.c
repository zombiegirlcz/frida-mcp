/* dspow.c — DeepSeekHashV1 (PoW pro chat.deepseek.com) bez fridy.
 *
 * DeepSeekHashV1 je SHA3-256 s jedinou zmenou: prvni kolo permutace
 * Keccak-f[1600] (kolo 0) se PRESKOCI. Zbytek (rate 136, padding 0x06,
 * 24 RC konstant, 25x64bit stav) je standardni SHA3-256.
 *
 * Overeno proti realnemu vystupu nativni funkce z librscrypto.so:
 *   msg = "6fe4581ae0fcf306e50d_1789139155175_35873"
 *   -> 886a0939c788d0ef9b2ef84e5b98c48494fe2474ea3f62cc5abe036879415a3a
 *
 * Kompilace:
 *   gcc -O3 -shared -fPIC -o libdspow.so dspow.c
 */
#include <stdint.h>
#include <string.h>
#include <stdio.h>

#define RATE 136
#define DS_ROUNDS 24 /* kola 1..23 se skutecne provedou */

static const uint64_t RC[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL,
};

/* rotace pro lane (x + 5*y) */
static const int ROT[25] = {
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
};

#define ROTL(x, n) (((x) << (n)) | ((x) >> (64 - (n))))

/* Keccak-f[1600], ale bez kola 0 (to je cela "tajna" DeepSeekHashV1). */
static void keccak_skip_first(uint64_t s[25]) {
    uint64_t c[5], d[5], b[25];
    int r, x, y, i;

    for (r = 1; r < DS_ROUNDS; r++) {
        /* theta */
        for (x = 0; x < 5; x++)
            c[x] = s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20];
        for (x = 0; x < 5; x++)
            d[x] = c[(x + 4) % 5] ^ ROTL(c[(x + 1) % 5], 1);
        for (x = 0; x < 5; x++)
            for (y = 0; y < 5; y++)
                s[x + 5 * y] ^= d[x];

        /* rho + pi */
        for (x = 0; x < 5; x++)
            for (y = 0; y < 5; y++) {
                i = x + 5 * y;
                b[y + 5 * ((2 * x + 3 * y) % 5)] =
                    ROT[i] ? ROTL(s[i], ROT[i]) : s[i];
            }

        /* chi */
        for (x = 0; x < 5; x++)
            for (y = 0; y < 5; y++)
                s[x + 5 * y] =
                    b[x + 5 * y] ^ ((~b[((x + 1) % 5) + 5 * y]) &
                                    b[((x + 2) % 5) + 5 * y]);

        /* iota */
        s[0] ^= RC[r];
    }
}

/* DeepSeekHashV1(data) -> 32 bajtu (rate 136, SHA-3 padding 0x06). */
void deepseek_hash(const uint8_t *in, size_t len, uint8_t out[32]) {
    uint64_t s[25];
    uint8_t block[RATE];
    size_t i;

    memset(s, 0, sizeof s);

    while (len >= RATE) {
        for (i = 0; i < RATE / 8; i++) {
            uint64_t w;
            memcpy(&w, in + i * 8, 8); /* little-endian host */
            s[i] ^= w;
        }
        keccak_skip_first(s);
        in += RATE;
        len -= RATE;
    }

    memset(block, 0, RATE);
    if (len)
        memcpy(block, in, len);
    block[len] = 0x06;
    block[RATE - 1] |= 0x80;
    for (i = 0; i < RATE / 8; i++) {
        uint64_t w;
        memcpy(&w, block + i * 8, 8);
        s[i] ^= w;
    }
    keccak_skip_first(s);

    memcpy(out, s, 32);
}

/* Najde nonce v [0, difficulty) tak, ze hash(prefix+nonce) == target.
 * Vraci nonce nebo -1. Cela smycka je v C (rychle). */
long long deepseek_solve(const char *prefix, long long prefix_len,
                         const uint8_t target[32], long long difficulty,
                         long long start) {
    char buf[512];
    uint8_t h[32];
    long long n;

    if (prefix_len < 0 || prefix_len > 400)
        return -1;
    memcpy(buf, prefix, (size_t)prefix_len);

    for (n = start; n < difficulty; n++) {
        int l = snprintf(buf + prefix_len, sizeof(buf) - (size_t)prefix_len,
                         "%lld", n);
        deepseek_hash((const uint8_t *)buf, (size_t)prefix_len + (size_t)l, h);
        if (memcmp(h, target, 32) == 0)
            return n;
    }
    return -1;
}

/* Jen pro testy: hash jednoho retezce. */
void deepseek_hash_str(const char *s, long long len, uint8_t out[32]) {
    deepseek_hash((const uint8_t *)s, (size_t)len, out);
}
