#include <oqs/oqs.h>
#include <oqs/rand.h>
#include <openssl/evp.h>
#include <openssl/sha.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
  ChaCha20-DRBG simples (stream cipher) para "demonstrar injeção de seed"
  - key = SHA256(entropy256)
  - nonce = primeiros 16 bytes de SHA256(entropy256 || 0x01)
  - contador interno incrementa a cada chamada (via "nonce2" derivado)
  Observação: DRBG aqui é para PoC/bench, não é uma reivindicação FIPS.
*/

static uint8_t g_key[32];
static uint8_t g_nonce[16];
static uint64_t g_counter = 0;
static int g_rng_inited = 0;

static void derive_seed_material(const uint8_t *seed256, size_t seedlen) {
    if (seedlen != 256) {
        fprintf(stderr, "seedlen esperado 256, recebeu %zu\n", seedlen);
        exit(2);
    }
    // key = SHA256(seed256)
    SHA256(seed256, seedlen, g_key);

    // nonce = SHA256(seed256 || 0x01)[:16]
    uint8_t tmp[257];
    memcpy(tmp, seed256, 256);
    tmp[256] = 0x01;
    uint8_t h2[32];
    SHA256(tmp, sizeof(tmp), h2);
    memcpy(g_nonce, h2, 16);

    g_counter = 0;
    g_rng_inited = 1;
}

// RNG callback para liboqs: assinatura exigida
static void oqs_rng(uint8_t *out, size_t out_len) {
    if (!g_rng_inited) {
        fprintf(stderr, "RNG custom não inicializado\n");
        exit(2);
    }

    // Para gerar out_len bytes, usamos OpenSSL EVP_chacha20
    // Criamos um nonce efetivo derivado de g_nonce + counter (XOR nos 8 últimos bytes)
    uint8_t nonce_eff[16];
    memcpy(nonce_eff, g_nonce, 16);
    for (int i = 0; i < 8; i++) {
        nonce_eff[15 - i] ^= (uint8_t)((g_counter >> (8 * i)) & 0xFF);
    }

    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) exit(2);

    if (EVP_EncryptInit_ex(ctx, EVP_chacha20(), NULL, g_key, nonce_eff) != 1) {
        EVP_CIPHER_CTX_free(ctx);
        exit(2);
    }

    // ChaCha20 gera keystream ao "criptografar" zeros.
    uint8_t *zeros = (uint8_t *)calloc(out_len, 1);
    if (!zeros) {
        EVP_CIPHER_CTX_free(ctx);
        exit(2);
    }

    int outl = 0;
    if (EVP_EncryptUpdate(ctx, out, &outl, zeros, (int)out_len) != 1) {
        free(zeros);
        EVP_CIPHER_CTX_free(ctx);
        exit(2);
    }

    free(zeros);
    EVP_CIPHER_CTX_free(ctx);
    g_counter++;
}

static int hex2bytes(const char *hex, uint8_t *out, size_t outlen) {
    size_t n = strlen(hex);
    if (n != outlen * 2) return 0;
    for (size_t i = 0; i < outlen; i++) {
        unsigned int v = 0;
        if (sscanf(hex + 2*i, "%2x", &v) != 1) return 0;
        out[i] = (uint8_t)v;
    }
    return 1;
}

int main(int argc, char **argv) {
    const char *mode = getenv("MODE");
    if (!mode) mode = "baseline";

    const char *seed_hex = getenv("SEED_HEX"); // opcional no baseline, obrigatório no qrng
    uint8_t seed[256];

    if (strcmp(mode, "qrng") == 0) {
        if (!seed_hex) {
            fprintf(stderr, "MODE=qrng exige SEED_HEX (512 chars hex para 256 bytes)\n");
            return 2;
        }
        if (!hex2bytes(seed_hex, seed, sizeof(seed))) {
            fprintf(stderr, "SEED_HEX inválido (esperado 512 hex chars)\n");
            return 2;
        }
        derive_seed_material(seed, sizeof(seed));
        // troca RNG do liboqs para usar callback
        OQS_randombytes_custom_algorithm(oqs_rng);
    }

    const char *alg = "ML-KEM-512";
    OQS_KEM *kem = OQS_KEM_new(alg);
    if (!kem) {
        fprintf(stderr, "OQS_KEM_new falhou para %s\n", alg);
        return 2;
    }

    uint8_t *pk = malloc(kem->length_public_key);
    uint8_t *sk = malloc(kem->length_secret_key);
    uint8_t *ct = malloc(kem->length_ciphertext);
    uint8_t *ss1 = malloc(kem->length_shared_secret);
    uint8_t *ss2 = malloc(kem->length_shared_secret);

    if (!pk || !sk || !ct || !ss1 || !ss2) {
        fprintf(stderr, "malloc falhou\n");
        OQS_KEM_free(kem);
        return 2;
    }

    if (OQS_KEM_keypair(kem, pk, sk) != OQS_SUCCESS) {
        fprintf(stderr, "keypair falhou\n");
        return 2;
    }
    if (OQS_KEM_encaps(kem, ct, ss1, pk) != OQS_SUCCESS) {
        fprintf(stderr, "encaps falhou\n");
        return 2;
    }
    if (OQS_KEM_decaps(kem, ss2, ct, sk) != OQS_SUCCESS) {
        fprintf(stderr, "decaps falhou\n");
        return 2;
    }

    if (memcmp(ss1, ss2, kem->length_shared_secret) != 0) {
        fprintf(stderr, "shared secret mismatch\n");
        return 3;
    }

    // imprime apenas 1 byte para sinalizar sucesso sem vazar material
    printf("OK\n");

    free(pk); free(sk); free(ct); free(ss1); free(ss2);
    OQS_KEM_free(kem);
    return 0;
}