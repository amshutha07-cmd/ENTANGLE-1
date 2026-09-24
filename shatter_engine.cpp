// shatter_engine.cpp — A.N.Sx Vault | Reed-Solomon shard engine (format v2)
//
// Pipeline:  file -> zlib -> AES-256-GCM -> RS(12,8) over a keyed GF(2^8) -> 12 shards
//
//  * CONFIDENTIALITY + INTEGRITY come from AES-256-GCM. The AES key is
//    HKDF-SHA256(salt, len-prefixed(C1) || len-prefixed(C2)). C1 is the primary
//    secret (the per-vault ephemeral key); C2 is an optional second factor
//    (empty string = unused). A wrong key or any tampering fails GCM verification.
//  * The Galois field polynomial (one of the 16 primitive polynomials of degree 8)
//    and the 12 evaluation points are also derived from the same HKDF output.
//    They make the codec differ per vault, but they are NOT a security boundary:
//    ciphertext is what is protected, not the erasure-code structure.
//  * Every shard is self-describing (52-byte header incl. CRC32), so any 8 intact
//    shards can be located, validated and decoded without a side-car manifest.
//    Corrupt shards are skipped, which is the point of the erasure code.
//
// Shard file layout (big endian):
//   0  magic "ANSX"      4
//   4  version (2)       1
//   5  shard index 0..11 1
//   6  K (8)             1
//   7  N (12)            1
//   8  salt              32
//   40 blob_len          8   length of AES-GCM blob before zero padding
//   48 crc32(data)       4
//   52 data              ceil(blob_len / K) bytes
//
// Portable C++17 (Linux, macOS, Windows). Build: python build_engine.py

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <zlib.h>

#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <openssl/rand.h>
#include <openssl/sha.h>

#if defined(_WIN32)
#  define ANSX_API extern "C" __declspec(dllexport)
#else
#  define ANSX_API extern "C" __attribute__((visibility("default")))
#endif

namespace fs = std::filesystem;
using Bytes = std::vector<unsigned char>;

static const int K_DATA   = 8;
static const int N_SHARDS = 12;
static const int ENGINE_VERSION = 2;
static const size_t HEADER_SIZE = 52;
static const size_t SALT_LEN = 32;
static const size_t GCM_NONCE = 12;
static const size_t GCM_TAG = 16;
static const uint64_t MAX_PAYLOAD = 1ULL << 31;   // 2 GiB hard cap (all data is held in RAM)

// All 16 primitive polynomials of degree 8 over GF(2) (verified: each generates a group of order 255).
static const uint16_t PRIMITIVE_POLYS[16] = {
    0x11D, 0x12B, 0x12D, 0x14D, 0x15F, 0x163, 0x165, 0x169,
    0x171, 0x187, 0x18D, 0x1A9, 0x1C3, 0x1CF, 0x1E7, 0x1F5};

// ─── Galois field GF(2^8) with a selectable primitive polynomial ─────────────
struct GF {
    unsigned char exp_[512];
    unsigned char log_[256];

    explicit GF(uint16_t poly) {
        int x = 1;
        std::memset(log_, 0, sizeof(log_));
        for (int i = 0; i < 255; i++) {
            exp_[i] = (unsigned char)x;
            log_[x] = (unsigned char)i;
            x <<= 1;
            if (x & 0x100) x ^= poly;
        }
        for (int i = 255; i < 512; i++) exp_[i] = exp_[i - 255];
    }
    unsigned char mul(unsigned char a, unsigned char b) const {
        if (a == 0 || b == 0) return 0;
        return exp_[log_[a] + log_[b]];
    }
    unsigned char inv(unsigned char a) const { return a == 0 ? 0 : exp_[255 - log_[a]]; }
};

static void invert_matrix(const GF& gf, std::vector<Bytes>& matrix, int K) {
    std::vector<Bytes> inv(K, Bytes(K, 0));
    for (int i = 0; i < K; i++) inv[i][i] = 1;

    for (int i = 0; i < K; i++) {
        if (matrix[i][i] == 0) {
            for (int r = i + 1; r < K; r++) {
                if (matrix[r][i] != 0) {
                    std::swap(matrix[i], matrix[r]);
                    std::swap(inv[i], inv[r]);
                    break;
                }
            }
        }
        unsigned char pivot = matrix[i][i];
        if (pivot == 0) throw std::runtime_error("singular matrix");
        unsigned char pivot_inv = gf.inv(pivot);
        for (int j = 0; j < K; j++) {
            matrix[i][j] = gf.mul(matrix[i][j], pivot_inv);
            inv[i][j] = gf.mul(inv[i][j], pivot_inv);
        }
        for (int r = 0; r < K; r++) {
            if (r == i) continue;
            unsigned char factor = matrix[r][i];
            if (!factor) continue;
            for (int j = 0; j < K; j++) {
                matrix[r][j] ^= gf.mul(factor, matrix[i][j]);
                inv[r][j] ^= gf.mul(factor, inv[i][j]);
            }
        }
    }
    matrix = inv;
}

// ─── HKDF-SHA256 (RFC 5869) ──────────────────────────────────────────────────
static Bytes hmac_sha256(const Bytes& key, const unsigned char* data, size_t len) {
    Bytes out(32);
    unsigned int outlen = 0;
    if (!HMAC(EVP_sha256(), key.empty() ? (const void*)"" : key.data(), (int)key.size(), data, len, out.data(), &outlen))
        throw std::runtime_error("HMAC failed");
    return out;
}

static Bytes hkdf_extract(const Bytes& salt, const Bytes& ikm) {
    return hmac_sha256(salt, ikm.data(), ikm.size());
}

static Bytes hkdf_expand(const Bytes& prk, const std::string& info, size_t length) {
    Bytes okm, t;
    unsigned char counter = 1;
    while (okm.size() < length) {
        Bytes msg(t);
        msg.insert(msg.end(), info.begin(), info.end());
        msg.push_back(counter++);
        t = hmac_sha256(prk, msg.data(), msg.size());
        okm.insert(okm.end(), t.begin(), t.end());
    }
    okm.resize(length);
    return okm;
}

static void append_lp(Bytes& out, const std::string& s) {
    uint32_t n = (uint32_t)s.size();
    out.push_back(n >> 24); out.push_back(n >> 16); out.push_back(n >> 8); out.push_back(n);
    out.insert(out.end(), s.begin(), s.end());
}

struct Keys {
    Bytes aes_key;            // 32 bytes
    uint16_t poly;            // GF polynomial
    Bytes points;             // N distinct non-zero x-coordinates
    ~Keys() { OPENSSL_cleanse(aes_key.data(), aes_key.size()); }
};

static Keys derive_keys(const Bytes& salt, const std::string& c1, const std::string& c2) {
    Bytes ikm;
    append_lp(ikm, c1);
    append_lp(ikm, c2);
    Bytes prk = hkdf_extract(salt, ikm);
    OPENSSL_cleanse(ikm.data(), ikm.size());

    Keys k;
    k.aes_key = hkdf_expand(prk, "ANSX-v2 AES-256-GCM", 32);
    Bytes field = hkdf_expand(prk, "ANSX-v2 FIELD", 1);
    k.poly = PRIMITIVE_POLYS[field[0] & 0x0F];

    // Keyed partial Fisher-Yates over {1..255}: first N entries are the evaluation points.
    Bytes stream = hkdf_expand(prk, "ANSX-v2 GEOMETRY", 2 * N_SHARDS);
    Bytes pool(255);
    for (int i = 0; i < 255; i++) pool[i] = (unsigned char)(i + 1);
    for (int i = 0; i < N_SHARDS; i++) {
        unsigned r = ((unsigned)stream[2 * i] << 8) | stream[2 * i + 1];
        int j = i + (int)(r % (unsigned)(255 - i));
        std::swap(pool[i], pool[j]);
    }
    k.points.assign(pool.begin(), pool.begin() + N_SHARDS);
    OPENSSL_cleanse(prk.data(), prk.size());
    return k;
}

// ─── AES-256-GCM ─────────────────────────────────────────────────────────────
// blob = nonce(12) || ciphertext || tag(16); `aad` is authenticated but not encrypted.
static Bytes gcm_encrypt(const Bytes& plain, const Bytes& key, const Bytes& aad) {
    Bytes blob(GCM_NONCE + plain.size() + GCM_TAG);
    if (RAND_bytes(blob.data(), (int)GCM_NONCE) != 1) throw std::runtime_error("rng");
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    int len = 0;
    bool ok = ctx &&
        EVP_EncryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, (int)GCM_NONCE, nullptr) == 1 &&
        EVP_EncryptInit_ex(ctx, nullptr, nullptr, key.data(), blob.data()) == 1 &&
        EVP_EncryptUpdate(ctx, nullptr, &len, aad.data(), (int)aad.size()) == 1;
    if (ok && !plain.empty())
        ok = EVP_EncryptUpdate(ctx, blob.data() + GCM_NONCE, &len, plain.data(), (int)plain.size()) == 1;
    int flen = 0;
    ok = ok && EVP_EncryptFinal_ex(ctx, blob.data() + GCM_NONCE + plain.size(), &flen) == 1 &&
         EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_GET_TAG, (int)GCM_TAG, blob.data() + GCM_NONCE + plain.size()) == 1;
    if (ctx) EVP_CIPHER_CTX_free(ctx);
    if (!ok) throw std::runtime_error("gcm encrypt failed");
    return blob;
}

static Bytes gcm_decrypt(const Bytes& blob, const Bytes& key, const Bytes& aad) {
    if (blob.size() < GCM_NONCE + GCM_TAG) throw std::runtime_error("blob too small");
    size_t clen = blob.size() - GCM_NONCE - GCM_TAG;
    Bytes plain(clen);
    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    int len = 0;
    bool ok = ctx &&
        EVP_DecryptInit_ex(ctx, EVP_aes_256_gcm(), nullptr, nullptr, nullptr) == 1 &&
        EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_IVLEN, (int)GCM_NONCE, nullptr) == 1 &&
        EVP_DecryptInit_ex(ctx, nullptr, nullptr, key.data(), blob.data()) == 1 &&
        EVP_DecryptUpdate(ctx, nullptr, &len, aad.data(), (int)aad.size()) == 1;
    if (ok && clen)
        ok = EVP_DecryptUpdate(ctx, plain.data(), &len, blob.data() + GCM_NONCE, (int)clen) == 1;
    Bytes tag(blob.end() - GCM_TAG, blob.end());
    ok = ok && EVP_CIPHER_CTX_ctrl(ctx, EVP_CTRL_GCM_SET_TAG, (int)GCM_TAG, tag.data()) == 1;
    int flen = 0;
    ok = ok && EVP_DecryptFinal_ex(ctx, plain.data() + clen, &flen) == 1;   // verifies the tag
    if (ctx) EVP_CIPHER_CTX_free(ctx);
    if (!ok) {
        OPENSSL_cleanse(plain.data(), plain.size());
        throw std::runtime_error("authentication failed");
    }
    return plain;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
static void put_be(Bytes& b, uint64_t v, int nbytes) {
    for (int i = nbytes - 1; i >= 0; i--) b.push_back((unsigned char)(v >> (8 * i)));
}
static uint64_t get_be(const unsigned char* p, int nbytes) {
    uint64_t v = 0;
    for (int i = 0; i < nbytes; i++) v = (v << 8) | p[i];
    return v;
}

// Paths arrive as UTF-8 from Python; u8path keeps non-ASCII names working on Windows.
static fs::path P(const std::string& utf8) { return fs::u8path(utf8); }

static void restrict_perms(const fs::path& p, bool dir) {
    std::error_code ec;   // best effort: no-op semantics on filesystems without POSIX modes
    fs::permissions(p, dir ? fs::perms::owner_all : (fs::perms::owner_read | fs::perms::owner_write),
                    fs::perm_options::replace, ec);
}

static Bytes read_file(const std::string& path) {
    std::ifstream f(P(path), std::ios::binary | std::ios::ate);
    if (!f.is_open()) throw std::runtime_error("open failed");
    std::streamsize size = f.tellg();
    f.seekg(0, std::ios::beg);
    Bytes buf((size_t)size);
    if (size > 0 && !f.read((char*)buf.data(), size)) throw std::runtime_error("read failed");
    return buf;
}

// AAD binds the header fields that decide how the blob is interpreted.
static Bytes make_aad(const Bytes& salt, uint64_t blob_len) {
    Bytes aad = {'A', 'N', 'S', 'X', (unsigned char)ENGINE_VERSION, (unsigned char)K_DATA, (unsigned char)N_SHARDS};
    aad.insert(aad.end(), salt.begin(), salt.end());
    put_be(aad, blob_len, 8);
    return aad;
}

static int do_shatter(const std::string& input_path, const std::string& shard_dir,
                      const std::string& c1, const std::string& c2) {
    Bytes raw = read_file(input_path);
    if (raw.size() >= MAX_PAYLOAD) return -5;

    // plaintext = raw_len(8) || zlib(raw)
    uLongf comp_size = compressBound((uLong)raw.size());
    Bytes comp(comp_size);
    if (compress(comp.data(), &comp_size, raw.data(), (uLong)raw.size()) != Z_OK) return -2;
    comp.resize(comp_size);
    Bytes plain;
    put_be(plain, raw.size(), 8);
    plain.insert(plain.end(), comp.begin(), comp.end());
    OPENSSL_cleanse(raw.data(), raw.size());

    Bytes salt(SALT_LEN);
    if (RAND_bytes(salt.data(), (int)SALT_LEN) != 1) return -3;
    Keys keys = derive_keys(salt, c1, c2);

    // blob length is fully determined before encryption, so it can go in the AAD.
    uint64_t blob_len = GCM_NONCE + plain.size() + GCM_TAG;
    Bytes blob;
    try { blob = gcm_encrypt(plain, keys.aes_key, make_aad(salt, blob_len)); }
    catch (...) { return -3; }
    OPENSSL_cleanse(plain.data(), plain.size());

    Bytes padded(blob);
    while (padded.size() % K_DATA != 0) padded.push_back(0);
    size_t chunks = padded.size() / K_DATA;

    GF gf(keys.poly);
    // Vandermonde rows: V[n][k] = x_n^k
    unsigned char V[N_SHARDS][K_DATA];
    for (int n = 0; n < N_SHARDS; n++) {
        unsigned char p = 1;
        for (int k = 0; k < K_DATA; k++) { V[n][k] = p; p = gf.mul(p, keys.points[n]); }
    }
    std::vector<Bytes> shards(N_SHARDS, Bytes(chunks));
    for (size_t c = 0; c < chunks; c++) {
        const unsigned char* coeffs = &padded[c * K_DATA];
        for (int n = 0; n < N_SHARDS; n++) {
            unsigned char acc = 0;
            for (int k = 0; k < K_DATA; k++) acc ^= gf.mul(coeffs[k], V[n][k]);
            shards[n][c] = acc;
        }
    }

    std::error_code ec;
    fs::create_directories(P(shard_dir), ec);
    restrict_perms(P(shard_dir), true);
    // Never leave stale shards from an earlier vault next to the new ones.
    for (auto& e : fs::directory_iterator(P(shard_dir), ec)) {
        std::string name = e.path().filename().string();
        if (name.rfind("fragment_", 0) == 0 && name.size() > 5 && name.substr(name.size() - 5) == ".ansx")
            fs::remove(e.path(), ec);
    }

    for (int i = 0; i < N_SHARDS; i++) {
        Bytes out = {'A', 'N', 'S', 'X', (unsigned char)ENGINE_VERSION, (unsigned char)i,
                     (unsigned char)K_DATA, (unsigned char)N_SHARDS};
        out.insert(out.end(), salt.begin(), salt.end());
        put_be(out, blob_len, 8);
        put_be(out, crc32(0L, shards[i].data(), (uInt)shards[i].size()), 4);
        out.insert(out.end(), shards[i].begin(), shards[i].end());

        std::string path = shard_dir + "/fragment_" + std::to_string(i + 1) + ".ansx";
        std::ofstream f(P(path), std::ios::binary | std::ios::trunc);
        if (!f.is_open()) return -4;
        f.write((const char*)out.data(), (std::streamsize)out.size());
        f.close();
        if (!f) return -4;
        restrict_perms(P(path), false);
    }
    return 0;
}

struct ShardFile { int index; Bytes salt; uint64_t blob_len; Bytes data; };

static bool parse_shard(const std::string& path, ShardFile& out) {
    Bytes b;
    try { b = read_file(path); } catch (...) { return false; }
    if (b.size() < HEADER_SIZE) return false;
    if (std::memcmp(b.data(), "ANSX", 4) != 0 || b[4] != ENGINE_VERSION || b[6] != K_DATA || b[7] != N_SHARDS) return false;
    if (b[5] >= N_SHARDS) return false;
    out.index = b[5];
    out.salt.assign(b.begin() + 8, b.begin() + 8 + SALT_LEN);
    out.blob_len = get_be(&b[40], 8);
    uint32_t crc = (uint32_t)get_be(&b[48], 4);
    out.data.assign(b.begin() + HEADER_SIZE, b.end());
    if (out.blob_len == 0 || out.blob_len > MAX_PAYLOAD * 2) return false;
    if (out.data.size() != (out.blob_len + K_DATA - 1) / K_DATA) return false;
    return crc32(0L, out.data.data(), (uInt)out.data.size()) == crc;
}

static int do_unshatter(const std::string& shard_dir, const std::string& out_file,
                        const std::string& c1, const std::string& c2) {
    // Collect valid shards; group by (salt, blob_len) and use the largest consistent group.
    std::vector<ShardFile> found;
    for (int i = 1; i <= N_SHARDS; i++) {
        std::string p = shard_dir + "/fragment_" + std::to_string(i) + ".ansx";
        if (!fs::exists(P(p))) continue;
        ShardFile s;
        if (parse_shard(p, s)) found.push_back(std::move(s));
    }
    if (found.empty()) return -10;

    std::vector<ShardFile> group;
    for (auto& cand : found) {
        std::vector<ShardFile> g;
        std::vector<bool> seen(N_SHARDS, false);
        for (auto& s : found) {
            if (s.salt == cand.salt && s.blob_len == cand.blob_len && !seen[s.index]) {
                seen[s.index] = true;
                g.push_back(s);
            }
        }
        if (g.size() > group.size()) group = g;
    }
    if ((int)group.size() < K_DATA) return -10;   // not enough intact shards
    group.resize(K_DATA);

    const Bytes& salt = group[0].salt;
    uint64_t blob_len = group[0].blob_len;
    Keys keys = derive_keys(salt, c1, c2);
    GF gf(keys.poly);

    std::vector<Bytes> matrix(K_DATA, Bytes(K_DATA));
    for (int i = 0; i < K_DATA; i++) {
        unsigned char x = keys.points[group[i].index], p = 1;
        for (int j = 0; j < K_DATA; j++) { matrix[i][j] = p; p = gf.mul(p, x); }
    }
    try { invert_matrix(gf, matrix, K_DATA); } catch (...) { return -14; }

    size_t chunks = group[0].data.size();
    Bytes padded(chunks * K_DATA);
    for (size_t c = 0; c < chunks; c++) {
        unsigned char y[K_DATA];
        for (int i = 0; i < K_DATA; i++) y[i] = group[i].data[c];
        for (int r = 0; r < K_DATA; r++) {
            unsigned char v = 0;
            for (int col = 0; col < K_DATA; col++) v ^= gf.mul(matrix[r][col], y[col]);
            padded[c * K_DATA + r] = v;
        }
    }
    padded.resize(blob_len);

    Bytes plain;
    try { plain = gcm_decrypt(padded, keys.aes_key, make_aad(salt, blob_len)); }
    catch (...) { return -11; }   // wrong key OR tampered/corrupt data

    if (plain.size() < 8) return -12;
    uint64_t raw_len = get_be(plain.data(), 8);
    if (raw_len >= MAX_PAYLOAD) return -12;
    Bytes raw((size_t)raw_len);
    uLongf dest = (uLongf)raw_len;
    if (uncompress(raw.data(), &dest, plain.data() + 8, (uLong)(plain.size() - 8)) != Z_OK || dest != raw_len) return -12;

    std::ofstream out(P(out_file), std::ios::binary | std::ios::trunc);
    if (!out.is_open()) return -13;
    out.write((const char*)raw.data(), (std::streamsize)raw.size());
    out.close();
    if (!out) return -13;
    restrict_perms(P(out_file), false);
    return 0;
}

// ─── C API ───────────────────────────────────────────────────────────────────
// Return codes: 0 ok; -1 unreadable input; -2 compress; -3 crypto; -4 shard write;
// -5 input too large; -10 fewer than K intact shards; -11 authentication failed
// (wrong key or tampered data); -12 corrupt payload; -13 output write; -14 singular; -99 other.
ANSX_API int ansx_engine_version() { return ENGINE_VERSION; }

ANSX_API int run_shatter_engine_to(const char* input_path, const char* out_dir, const char* key, const char* second_factor) {
    try {
        return do_shatter(input_path, out_dir, key ? key : "", second_factor ? second_factor : "");
    } catch (const std::exception& e) {
        std::cerr << "[shatter] " << e.what() << std::endl;
        return -1;
    } catch (...) { return -99; }
}

ANSX_API int unshatter_engine(const char* shard_dir, const char* out_file, const char* key, const char* second_factor) {
    try {
        return do_unshatter(shard_dir, out_file, key ? key : "", second_factor ? second_factor : "");
    } catch (...) { return -99; }
}
