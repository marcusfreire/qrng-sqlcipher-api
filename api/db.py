import os
import sqlite3
from contextlib import contextmanager

# Usamos pysqlcipher3.dbapi2 como sqlite3 (já instalado via requirements)
try:
    from pysqlcipher3 import dbapi2 as sqlcipher
except ImportError as e:
    raise RuntimeError("pysqlcipher3 não instalado. Verifique Dockerfile/requirements.") from e

DB_PATH = os.getenv("DB_PATH", "/data/keys.db")
DB_KEYFILE = os.getenv("DB_KEYFILE", "/run/secrets/db_key")

def _read_db_key(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

def open_db():
    """
    Abre conexão SQLCipher e aplica PRAGMAs de segurança e desempenho.
    """
    key = _read_db_key(DB_KEYFILE)
    con = sqlcipher.connect(DB_PATH, check_same_thread=False)
    cur = con.cursor()
    # PRAGMAs SQLCipher (ordem importa)
    # cur.execute("PRAGMA key = ?;", (key,))
    cur.execute(f"PRAGMA key='{key}';")
    cur.execute("PRAGMA cipher_page_size = 4096;")
    cur.execute("PRAGMA kdf_iter = 64000;")
    cur.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512;")
    cur.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;")
    # SQLite PRAGMAs
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")
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


@contextmanager
def tx_immediate(con):
    """
    Transação IMEDIATA (bloqueia escrita concorrente) para operações atômicas.

    - Usa BEGIN IMMEDIATE;
    - Só chama COMMIT/ROLLBACK se ainda houver transação ativa (con.in_transaction).
    """
    con.execute("BEGIN IMMEDIATE;")
    try:
        yield
        # Só commita se ainda estivermos dentro de uma transação
        if getattr(con, "in_transaction", False):
            con.commit()
    except Exception:
        # Só faz rollback se ainda houver transação ativa
        if getattr(con, "in_transaction", False):
            try:
                con.rollback()
            except Exception:
                pass
        raise


# Inicializa schema ao subir a API
_con_boot = open_db()
init_schema(_con_boot)
_con_boot.close()