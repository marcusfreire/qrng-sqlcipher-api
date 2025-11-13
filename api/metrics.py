import math
from typing import Tuple

# Caso precise de numpy em métricas mais elaboradas, importe aqui (opcional)
# import numpy as np

def bits_from_hex(hexstr: str, total_bits: int) -> str:
    """
    Converte hex -> bits "0101..." e recorta para 'total_bits'.
    """
    b = bytes.fromhex(hexstr)
    s = "".join(f"{byte:08b}" for byte in b)
    return s[:total_bits]

def bytes_from_bits(bitstr: str) -> bytes:
    """
    Converte "0101..." -> bytes, preenchendo zeros à direita se necessário.
    """
    pad = (-len(bitstr)) % 8
    if pad:
        bitstr += "0" * pad
    return bytes(int(bitstr[i:i+8], 2) for i in range(0, len(bitstr), 8))

def hmin_p1(bitstr: str) -> Tuple[float, float]:
    """
    H_min por bit e p1 do trecho (para resposta do /keys/pop).
    """
    n = len(bitstr)
    if n == 0:
        return 0.0, 0.0
    p1 = bitstr.count("1") / n
    pmax = max(p1, 1 - p1)
    hmin = -math.log2(pmax) if pmax > 0 else 0.0
    return round(hmin, 6), round(p1, 6)
