# rana_extender/rana.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal
import hashlib
import hmac

from argon2.low_level import hash_secret_raw, Type as Argon2Type


KDFKind = Literal["argon2", "pbkdf2", "hkdf"]


@dataclass(frozen=True)
class RanaConfig:
    # Divisão interna do bloco de entrada
    seed_ratio: float = 0.75
    offset_ratio: float = 0.15
    numexp_ratio: float = 0.10

    # Seleção da KDF
    kind_kdf: KDFKind = "argon2"

    # Fator máximo de expansão.
    # O fator real é derivado deterministicamente do campo numexp:
    # factor = (hash(numexp) % max_factor) + 1
    max_factor: int = 4

    # Argon2id
    argon2_time: int = 2
    argon2_mem_kib: int = 16384
    argon2_par: int = 1

    # PBKDF2-HMAC-SHA256
    pbkdf2_iterations: int = 100_000

    # HKDF-SHA256
    hkdf_hash: str = "sha256"

    # Geração em partes para evitar chamadas gigantes
    out_chunk_bytes: int = 4096


def _check_positive_int(name: str, value: int) -> None:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int, got {value!r}")


def _split_parts(block: bytes, cfg: RanaConfig) -> tuple[bytes, bytes, bytes]:
    """
    Divide o bloco de entrada em:
    - seed: material principal para a KDF
    - offset: usado como domínio/salt
    - numexp: usado para derivar o fator de expansão
    """
    n = len(block)
    if n < 16:
        raise ValueError("seed input too small; need at least 16 bytes")

    s_end = int(n * cfg.seed_ratio)
    o_end = s_end + int(n * cfg.offset_ratio)

    # Garante pelo menos 1 byte em cada parte.
    s_end = max(1, min(s_end, n - 2))
    o_end = max(s_end + 1, min(o_end, n - 1))

    seed = block[:s_end]
    offset = block[s_end:o_end]
    numexp = block[o_end:]

    if len(numexp) == 0:
        numexp = block[-1:]

    return seed, offset, numexp


def _derive_factor(numexp: bytes, max_factor: int) -> int:
    """
    Deriva fator de expansão no intervalo [1, max_factor].

    Observação:
    - Isso deixa o fator variável por bloco.
    - Não aumenta entropia informacional.
    - Controla apenas o volume de saída derivado da semente.
    """
    _check_positive_int("max_factor", max_factor)
    digest = hashlib.blake2b(numexp, digest_size=8).digest()
    raw = int.from_bytes(digest, "big")
    return (raw % max_factor) + 1


def _derive_salt(offset: bytes, counter: int, label: bytes) -> bytes:
    """
    Deriva salt de 16 bytes para cada chamada interna.
    """
    payload = label + b"|" + offset + b"|" + counter.to_bytes(8, "big")
    return hashlib.blake2b(payload, digest_size=16).digest()


def _kdf_argon2id(secret: bytes, salt: bytes, out_len: int, cfg: RanaConfig) -> bytes:
    return hash_secret_raw(
        secret=secret,
        salt=salt,
        time_cost=cfg.argon2_time,
        memory_cost=cfg.argon2_mem_kib,
        parallelism=cfg.argon2_par,
        hash_len=out_len,
        type=Argon2Type.ID,
        version=19,
    )


def _kdf_pbkdf2(secret: bytes, salt: bytes, out_len: int, cfg: RanaConfig) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password=secret,
        salt=salt,
        iterations=cfg.pbkdf2_iterations,
        dklen=out_len,
    )


def _hkdf_extract(salt: bytes, ikm: bytes, hash_name: str = "sha256") -> bytes:
    hash_len = hashlib.new(hash_name).digest_size
    if not salt:
        salt = b"\x00" * hash_len
    return hmac.new(salt, ikm, hash_name).digest()


def _hkdf_expand(prk: bytes, info: bytes, out_len: int, hash_name: str = "sha256") -> bytes:
    """
    HKDF-Expand conforme RFC 5869.
    Limite por chamada: 255 * HashLen.
    Como o rana_expand_argon2 gera em chunks, esse limite não deve ser excedido.
    """
    hash_len = hashlib.new(hash_name).digest_size
    max_len = 255 * hash_len

    if out_len > max_len:
        raise ValueError(
            f"HKDF output too large for one expand call: {out_len} > {max_len}"
        )

    okm = bytearray()
    previous = b""
    counter = 1

    while len(okm) < out_len:
        previous = hmac.new(
            prk,
            previous + info + bytes([counter]),
            hash_name,
        ).digest()
        okm.extend(previous)
        counter += 1

    return bytes(okm[:out_len])


def _kdf_hkdf(secret: bytes, salt: bytes, out_len: int, cfg: RanaConfig, info: bytes) -> bytes:
    prk = _hkdf_extract(salt=salt, ikm=secret, hash_name=cfg.hkdf_hash)
    return _hkdf_expand(prk=prk, info=info, out_len=out_len, hash_name=cfg.hkdf_hash)


def _run_selected_kdf(
    secret: bytes,
    salt: bytes,
    out_len: int,
    cfg: RanaConfig,
    *,
    info: bytes,
) -> bytes:
    """
    Executa a KDF selecionada em cfg.kind_kdf.
    """
    if cfg.kind_kdf == "argon2":
        return _kdf_argon2id(secret, salt, out_len, cfg)

    if cfg.kind_kdf == "pbkdf2":
        return _kdf_pbkdf2(secret, salt, out_len, cfg)

    if cfg.kind_kdf == "hkdf":
        return _kdf_hkdf(secret, salt, out_len, cfg, info=info)

    raise ValueError(
        f"Unsupported kind_kdf={cfg.kind_kdf!r}. Use: argon2, pbkdf2, hkdf."
    )


def rana_expand(
    seed_input: bytes,
    cfg: RanaConfig,
    *,
    factor: Optional[int] = None,
) -> bytes:
    """
    Expande seed_input usando a KDF selecionada.

    Tamanho da saída:
        out_len_bytes = factor * len(seed_input)

    Se factor=None:
        factor é derivado do campo numexp:
        factor = (BLAKE2b(numexp) % cfg.max_factor) + 1

    Observação:
        A expansão é determinística. Ela não cria nova entropia
        informacional; apenas deriva uma sequência pseudorrandômica
        condicionada ao bloco de entrada.
    """
    seed, offset, numexp = _split_parts(seed_input, cfg)

    if factor is None:
        factor = _derive_factor(numexp, cfg.max_factor)

    _check_positive_int("factor", factor)

    total_out_len = factor * len(seed_input)

    if total_out_len <= 0:
        raise ValueError("computed total output length is invalid")

    out = bytearray()
    counter = 0

    while len(out) < total_out_len:
        remaining = total_out_len - len(out)
        take = min(cfg.out_chunk_bytes, remaining)

        salt = _derive_salt(
            offset=offset,
            counter=counter,
            label=f"rana-{cfg.kind_kdf}".encode("ascii"),
        )

        info = (
            b"rana-expand|"
            + cfg.kind_kdf.encode("ascii")
            + b"|"
            + counter.to_bytes(8, "big")
        )

        chunk = _run_selected_kdf(
            secret=seed,
            salt=salt,
            out_len=take,
            cfg=cfg,
            info=info,
        )

        out.extend(chunk)
        counter += 1

    return bytes(out)


# Alias para compatibilidade com scripts antigos.
def rana_expand_argon2(
    seed_input: bytes,
    cfg: RanaConfig,
    *,
    factor: Optional[int] = None,
) -> bytes:
    """
    Alias histórico. Agora usa a KDF definida em cfg.kind_kdf.
    Mantido para evitar quebrar imports antigos.
    """
    return rana_expand(seed_input, cfg=cfg, factor=factor)