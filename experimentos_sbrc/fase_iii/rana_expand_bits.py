#!/usr/bin/env python3
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Iterator, Optional

from rana_extender.rana import RanaConfig, rana_expand_argon2


# Lookup table: byte -> "01010101"
_BYTE_TO_BITS = [format(i, "08b") for i in range(256)]


class BitReaderASCII:
    """
    Lê arquivo ASCII contendo '0' e '1' (pode ter \n e espaços).
    Entrega bits como caracteres '0'/'1' até atingir n_bits (se fornecido).
    """
    def __init__(self, path: Path, n_bits: Optional[int] = None, buf_bytes: int = 1 << 20):
        self.path = path
        self.n_bits = n_bits
        self.buf_bytes = buf_bytes

    def iter_bits(self) -> Iterator[str]:
        emitted = 0
        with self.path.open("r", encoding="ascii", errors="ignore") as f:
            while True:
                chunk = f.read(self.buf_bytes)
                if not chunk:
                    break
                for ch in chunk:
                    if ch == "0" or ch == "1":
                        yield ch
                        emitted += 1
                        if self.n_bits is not None and emitted >= self.n_bits:
                            return


def bits_to_bytes(bits: str) -> bytes:
    if len(bits) % 8 != 0:
        raise ValueError(f"bits length must be multiple of 8, got {len(bits)}")
    out = bytearray(len(bits) // 8)
    for i in range(0, len(bits), 8):
        out[i // 8] = int(bits[i:i+8], 2)
    return bytes(out)


def bytes_to_bits(b: bytes) -> str:
    # rápido via lookup table
    return "".join(_BYTE_TO_BITS[x] for x in b)


def xor_bytes(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        raise ValueError("xor length mismatch")
    return bytes(x ^ y for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Block-wise RanA expansion: read ASCII bits, chunk, XOR with CSPRNG, expand, write ASCII bits."
    )
    ap.add_argument("--in-bits", required=True, help="Input ASCII bits file (0/1).")
    ap.add_argument("--in-nbits", type=int, required=True, help="How many bits to read from input.")
    ap.add_argument("--chunk-bits", type=int, default=19456, help="Chunk size in bits (default: 19456). Must be multiple of 8.")
    ap.add_argument("--out", required=True, help="Output ASCII bits file.")
    ap.add_argument("--max-factor", type=int, default=4, help="Upper bound for factor derived per chunk.")
    ap.add_argument("--argon2-time", type=int, default=2)
    ap.add_argument("--argon2-mem-kib", type=int, default=16384)
    ap.add_argument("--argon2-par", type=int, default=1)
    ap.add_argument("--progress-every", type=int, default=50, help="Print progress every N chunks.")
    ap.add_argument("--limit-out-bits", type=int, default=0, help="If >0, truncate total output bits to this size.")
    args = ap.parse_args()

    if args.chunk_bits <= 0 or args.chunk_bits % 8 != 0:
        print("[ERROR] --chunk-bits must be positive and multiple of 8", file=sys.stderr)
        return 2

    in_path = Path(args.in_bits)
    out_path = Path(args.out)

    cfg = RanaConfig(
        argon2_time=args.argon2_time,
        argon2_mem_kib=args.argon2_mem_kib,
        argon2_par=args.argon2_par,
        max_factor=args.max_factor,
    )

    reader = BitReaderASCII(in_path, n_bits=args.in_nbits)

    chunk_bits = args.chunk_bits
    chunk_buf = []
    chunk_idx = 0

    total_in_bits = 0
    total_out_bits = 0
    limit_out = args.limit_out_bits if args.limit_out_bits and args.limit_out_bits > 0 else None

    # escrita em modo texto ASCII
    with out_path.open("w", encoding="ascii") as fout:
        for bit in reader.iter_bits():
            chunk_buf.append(bit)
            total_in_bits += 1

            if len(chunk_buf) == chunk_bits:
                chunk_idx += 1
                bits_str = "".join(chunk_buf)
                chunk_buf.clear()

                # bits -> bytes
                seed_qrng = bits_to_bytes(bits_str)

                # CSPRNG local (mesmo tamanho)
                seed_local = secrets.token_bytes(len(seed_qrng))

                # XOR hedging
                seed_mixed = xor_bytes(seed_qrng, seed_local)

                # RanA expand: out_len = factor * len(seed_input)
                out_bytes = rana_expand_argon2(seed_mixed, cfg)

                out_bits = bytes_to_bits(out_bytes)

                # truncagem opcional de saída total
                if limit_out is not None:
                    remain = limit_out - total_out_bits
                    if remain <= 0:
                        break
                    if len(out_bits) > remain:
                        out_bits = out_bits[:remain]

                fout.write(out_bits)
                total_out_bits += len(out_bits)

                if args.progress_every > 0 and (chunk_idx % args.progress_every == 0):
                    print(
                        f"[progress] chunks={chunk_idx} in_bits={total_in_bits} out_bits={total_out_bits} "
                        f"(avg expansion ~{(total_out_bits / max(1,total_in_bits)):.3f}x)"
                    )

                # se já leu todos os bits solicitados, sai
                if total_in_bits >= args.in_nbits:
                    break

        # Se sobrar um bloco incompleto, ignoramos (mantém alinhamento e evita padding)
        if chunk_buf:
            print(f"[warn] trailing incomplete chunk ignored: {len(chunk_buf)} bits", file=sys.stderr)

    print(f"[done] chunks={chunk_idx} in_bits={total_in_bits} out_bits={total_out_bits} out={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
