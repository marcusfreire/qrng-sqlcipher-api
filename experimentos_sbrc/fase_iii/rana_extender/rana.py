# rana_extender/rana.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import hashlib

from argon2.low_level import hash_secret_raw, Type as Argon2Type


@dataclass(frozen=True)
class RanaConfig:
    # Split do bloco
    seed_ratio: float = 0.75
    offset_ratio: float = 0.15
    numexp_ratio: float = 0.10

    # Argon2id parâmetros
    argon2_time: int = 2
    argon2_mem_kib: int = 16384
    argon2_par: int = 1

    # Geração incremental
    out_chunk_bytes: int = 4096  # tamanho de cada “pedaço” gerado por iteração

    # Fator de expansão (aleatório derivado do bloco) limitado por max_factor
    max_factor: int = 4  # para o experimento


def _clamp_positive_int(name: str, v: int) -> None:
    if not isinstance(v, int) or v <= 0:
        raise ValueError(f"{name} must be positive int, got {v!r}")


def _split_parts(block: bytes, cfg: RanaConfig) -> tuple[bytes, bytes, bytes]:
    n = len(block)
    if n < 16:
        raise ValueError("seed input too small; need at least 16 bytes")

    s_end = int(n * cfg.seed_ratio)
    o_end = s_end + int(n * cfg.offset_ratio)

    # garante ao menos 1 byte em cada parte
    s_end = max(1, min(s_end, n - 2))
    o_end = max(s_end + 1, min(o_end, n - 1))

    seed = block[:s_end]
    offset = block[s_end:o_end]
    numexp = block[o_end:]
    if len(numexp) < 1:
        numexp = block[-1:]
    return seed, offset, numexp


def _derive_factor(numexp: bytes, max_factor: int) -> int:
    """
    Fator “aleatório” derivado do bloco (determinístico dado o bloco).
    max_factor define o teto do fator para o experimento.
    """
    _clamp_positive_int("max_factor", max_factor)
    x = int.from_bytes(hashlib.blake2b(numexp, digest_size=8).digest(), "big")
    return (x % max_factor) + 1


def rana_expand_argon2(
    seed_input: bytes,
    cfg: RanaConfig,
    *,
    factor: Optional[int] = None,
) -> bytes:
    """
    Expande seed_input produzindo out_len_bytes = factor * len(seed_input),
    onde factor é derivado de numexp (aleatório estilo RanA) se não for fornecido.
    """
    seed, offset, numexp = _split_parts(seed_input, cfg)

    if factor is None:
        factor = _derive_factor(numexp, cfg.max_factor)
    _clamp_positive_int("factor", factor)

    out_len = factor * len(seed_input)
    if out_len <= 0:
        raise ValueError("computed out_len invalid")

    out = bytearray()
    ctr = 0

    # Gera saída em pedaços; cada iteração usa um salt distinto derivado de offset||ctr
    while len(out) < out_len:
        need = out_len - len(out)
        take = cfg.out_chunk_bytes if need > cfg.out_chunk_bytes else need

        salt = hashlib.blake2b(offset + ctr.to_bytes(8, "big"), digest_size=16).digest()
        chunk = hash_secret_raw(
            secret=seed,
            salt=salt,
            time_cost=cfg.argon2_time,
            memory_cost=cfg.argon2_mem_kib,
            parallelism=cfg.argon2_par,
            hash_len=take,
            type=Argon2Type.ID,
        )
        out.extend(chunk)
        ctr += 1

    return bytes(out)
