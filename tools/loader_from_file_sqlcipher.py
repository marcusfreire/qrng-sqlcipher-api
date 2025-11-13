#!/usr/bin/env python3
import os, math, argparse

try:
    from pysqlcipher3 import dbapi2 as sqlcipher
except ImportError as e:
    raise SystemExit("Instale pysqlcipher3/sqlcipher no ambiente alvo.") from e


def read_bits_file(path: str) -> str:
    with open(path, "r") as f:
        data = f.read()
    return "".join(ch for ch in data if ch in "01")


def bits_to_hex(bitstr: str) -> str:
    pad = (-len(bitstr)) % 8
    if pad:
        bitstr += "0" * pad
    out = bytes(int(bitstr[i : i + 8], 2) for i in range(0, len(bitstr), 8))
    return out.hex()


def batch_metrics(bitstr: str):
    N = len(bitstr)
    if N == 0:
        return {"N": 0, "p1": 0.0, "H_min": 0.0, "H_shannon": 0.0}
    ones = bitstr.count("1")
    p1 = ones / N
    pmax = max(p1, 1 - p1)
    Hmin = -math.log2(pmax) if pmax > 0 else 0.0
    Hsh = 0.0 if p1 in (0.0, 1.0) else -(p1 * math.log2(p1) + (1 - p1) * math.log2(1 - p1))
    return {
        "N": N,
        "p1": round(p1, 6),
        "H_min": round(Hmin, 6),
        "H_shannon": round(Hsh, 6),
    }


def slice_into_keys(bitstr: str, key_bits: int):
    n = len(bitstr) // key_bits
    return [bitstr[i * key_bits : (i + 1) * key_bits] for i in range(n)]


def open_db(db_path: str, keyfile: str):
    folder = os.path.dirname(db_path) or "."
    os.makedirs(folder, exist_ok=True)

    with open(keyfile, "r", encoding="utf-8") as f:
        key = f.read().strip()

    con = sqlcipher.connect(db_path)
    cur = con.cursor()

    cur.execute(f"PRAGMA key='{key}';")
    cur.execute("PRAGMA cipher_page_size = 4096;")
    cur.execute("PRAGMA kdf_iter = 64000;")
    cur.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
    cur.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.close()
    return con


def init_schema(con):

    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS keys_pool (
          id          INTEGER PRIMARY KEY AUTOINCREMENT,
          key_hex     TEXT    NOT NULL,
          h_min       REAL    NOT NULL,
          h_shannon   REAL    NOT NULL,
          created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
          consumed    INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_keys_pool_consumed ON keys_pool (consumed, id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_keys_pool_created  ON keys_pool (created_at DESC);"
    )
    con.commit()
    cur.close()


def main():
    ap = argparse.ArgumentParser(
        description="Carrega chaves em SQLCipher a partir de bits.txt (one-shot keys)."
    )
    ap.add_argument("--file", required=True, help="arquivo com bits '0101...' (ex.: /input/bits.txt)")
    ap.add_argument(
        "--db",
        default=None,
        help="caminho do arquivo SQLCipher (default = DB_PATH ou ./data/keys.db)",
    )
    ap.add_argument(
        "--keyfile",
        default=None,
        help="arquivo com a senha do banco (default = DB_KEYFILE ou ./db_key.secret)",
    )
    ap.add_argument(
        "--key-bits", type=int, default=2048, help="tamanho da chave em bits (múltiplo de 8)"
    )
    args = ap.parse_args()

    if args.key_bits <= 0 or args.key_bits % 8 != 0:
        raise SystemExit("ERRO: --key-bits deve ser múltiplo de 8 e > 0.")
    if not os.path.exists(args.file):
        raise SystemExit(f"ERRO: arquivo não encontrado: {args.file}")

    db_path = args.db or os.getenv("DB_PATH", "./data/keys.db")
    keyfile = args.keyfile or os.getenv("DB_KEYFILE", "./db_key.secret")

    if not os.path.exists(keyfile):
        raise SystemExit(f"ERRO: segredo não encontrado: {keyfile}")

    bitstr = read_bits_file(args.file)
    if len(bitstr) < args.key_bits:
        raise SystemExit(
            f"ERRO: arquivo tem {len(bitstr)} bits; menor que key_bits={args.key_bits}."
        )

    stats = batch_metrics(bitstr)
    keys = slice_into_keys(bitstr, args.key_bits)

    con = open_db(db_path, keyfile)
    init_schema(con)
    cur = con.cursor()
    inserted = 0
    for kbits in keys:
        hexstr = bits_to_hex(kbits)
        cur.execute(
            """
          INSERT INTO keys_pool (key_hex, h_min, h_shannon, consumed)
          VALUES (?, ?, ?, 0);
        """,
            (hexstr, stats["H_min"], stats["H_shannon"]),
        )
        inserted += 1
    con.commit()
    cur.close()
    con.close()

    print(
        f"OK: chaves inseridas={inserted} | H_min(batch)={stats['H_min']} | H_shannon(batch)={stats['H_shannon']}"
    )


if __name__ == "__main__":
    main()
