from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Any, Dict
import base64

from .db import open_db, tx_immediate
from .metrics import bits_from_hex, bytes_from_bits

app = FastAPI(
    title="QRNG Key Pop API (SQLite+SQLCipher, one-shot keys)",
    version="3.1",
)


class KeySliceResponse(BaseModel):
    key_id: int
    slice_hex: str
    size_bits: int
    h_min: float
    h_shannon: float
    slice_b64: str


@app.get("/keys/count")
def count_keys() -> Dict[str, Any]:
    """
    Retorna a quantidade de chaves disponíveis no pool (consumed = 0).
    """
    con = open_db()
    cur = con.cursor()
    free = cur.execute(
        "SELECT COUNT(*) FROM keys_pool WHERE consumed = 0;"
    ).fetchone()[0]
    cur.close()
    con.close()
    return {"available": free}


@app.post("/keys/pop", response_model=KeySliceResponse)
def pop_key(size_bits: int = Query(..., ge=8, le=2048)) -> KeySliceResponse:
    """
    Entrega um slice de size_bits a partir de uma chave de 2048 bits.
    - size_bits deve ser múltiplo de 8.
    - Sempre consome (deleta) a chave do banco, mesmo se usar só parte dela.
    """
    if size_bits % 8 != 0:
        raise HTTPException(
            status_code=400,
            detail="size_bits deve ser múltiplo de 8 (ex.: 8, 256, 512, 1024, 2048).",
        )

    con = open_db()
    try:
        with tx_immediate(con):
            cur = con.cursor()
            row = cur.execute(
                """
                SELECT id, key_hex, h_min, h_shannon
                  FROM keys_pool
                 WHERE consumed = 0
                 ORDER BY id
                 LIMIT 1;
                """
            ).fetchone()

            if not row:
                raise HTTPException(404, "Sem chaves disponíveis no pool para o tamanho solicitado.")

            key_id, key_hex, h_min, h_sh = row

            bit_slice = bits_from_hex(key_hex, size_bits)
            slice_bytes = bytes_from_bits(bit_slice)
            slice_hex = slice_bytes.hex()
            slice_b64 = base64.b64encode(slice_bytes).decode()

            # Consome a chave de vez
            cur.execute("DELETE FROM keys_pool WHERE id = ?;", (key_id,))

            return KeySliceResponse(
                key_id=key_id,
                slice_hex=slice_hex,
                size_bits=size_bits,
                h_min=float(h_min),
                h_shannon=float(h_sh),
                slice_b64=slice_b64,
            )
    finally:
        con.close()
