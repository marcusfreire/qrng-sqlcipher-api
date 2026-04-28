#!/usr/bin/env python3
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path
from typing import Iterator, Optional

from rana_extender.rana import RanaConfig, rana_expand


_BYTE_TO_BITS = [format(i, "08b") for i in range(256)]


class BitReaderASCII:
    """
    Lê arquivo ASCII contendo bits '0' e '1'.
    Ignora quebras de linha, espaços e outros caracteres.
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
        out[i // 8] = int(bits[i:i + 8], 2)

    return bytes(out)


def bytes_to_bits(data: bytes) -> str:
    return "".join(_BYTE_TO_BITS[b] for b in data)


def xor_bytes(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        raise ValueError("xor length mismatch")
    return bytes(x ^ y for x, y in zip(a, b))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "RanA block-wise expansion with selectable KDF: "
            "argon2, pbkdf2, or hkdf."
        )
    )

    ap.add_argument("--in-bits", required=True, help="Input ASCII bit file.")
    ap.add_argument("--in-nbits", type=int, required=True, help="Number of input bits to read.")
    ap.add_argument("--chunk-bits", type=int, default=19456, help="Chunk size in bits. Must be multiple of 8.")
    ap.add_argument("--out", required=True, help="Output ASCII bit file.")

    ap.add_argument(
        "--kind-kdf",
        choices=["argon2", "pbkdf2", "hkdf"],
        default="argon2",
        help="KDF used by RanA.",
    )

    ap.add_argument("--max-factor", type=int, default=4, help="Maximum expansion factor per chunk.")

    # Argon2id
    ap.add_argument("--argon2-time", type=int, default=2)
    ap.add_argument("--argon2-mem-kib", type=int, default=16384)
    ap.add_argument("--argon2-par", type=int, default=1)

    # PBKDF2
    ap.add_argument("--pbkdf2-iterations", type=int, default=100_000)

    # HKDF
    ap.add_argument("--hkdf-hash", default="sha256", choices=["sha256", "sha384", "sha512"])

    # Output control
    ap.add_argument("--progress-every", type=int, default=50)
    ap.add_argument(
        "--limit-out-bits",
        type=int,
        default=0,
        help="If >0, truncate total output to this number of bits.",
    )

    args = ap.parse_args()

    if args.in_nbits <= 0:
        print("[ERROR] --in-nbits must be positive", file=sys.stderr)
        return 2

    if args.chunk_bits <= 0 or args.chunk_bits % 8 != 0:
        print("[ERROR] --chunk-bits must be positive and multiple of 8", file=sys.stderr)
        return 2

    if args.max_factor <= 0:
        print("[ERROR] --max-factor must be positive", file=sys.stderr)
        return 2

    if args.pbkdf2_iterations <= 0:
        print("[ERROR] --pbkdf2-iterations must be positive", file=sys.stderr)
        return 2

    in_path = Path(args.in_bits)
    out_path = Path(args.out)

    cfg = RanaConfig(
        kind_kdf=args.kind_kdf,
        max_factor=args.max_factor,
        argon2_time=args.argon2_time,
        argon2_mem_kib=args.argon2_mem_kib,
        argon2_par=args.argon2_par,
        pbkdf2_iterations=args.pbkdf2_iterations,
        hkdf_hash=args.hkdf_hash,
    )

    reader = BitReaderASCII(in_path, n_bits=args.in_nbits)

    chunk_buf: list[str] = []
    chunk_idx = 0

    total_in_bits = 0
    total_out_bits = 0

    limit_out = args.limit_out_bits if args.limit_out_bits and args.limit_out_bits > 0 else None

    print(
        f"[config] kind_kdf={args.kind_kdf} "
        f"chunk_bits={args.chunk_bits} "
        f"max_factor={args.max_factor} "
        f"argon2_time={args.argon2_time} "
        f"argon2_mem_kib={args.argon2_mem_kib} "
        f"argon2_par={args.argon2_par} "
        f"pbkdf2_iterations={args.pbkdf2_iterations} "
        f"hkdf_hash={args.hkdf_hash}"
    )

    with out_path.open("w", encoding="ascii") as fout:
        for bit in reader.iter_bits():
            chunk_buf.append(bit)
            total_in_bits += 1

            if len(chunk_buf) == args.chunk_bits:
                chunk_idx += 1

                bits_str = "".join(chunk_buf)
                chunk_buf.clear()

                # Bloco QRNG
                seed_qrng = bits_to_bytes(bits_str)

                # CSPRNG local do sistema operacional
                seed_local = secrets.token_bytes(len(seed_qrng))

                # Hedging: QRNG XOR CSPRNG
                seed_mixed = xor_bytes(seed_qrng, seed_local)

                # RanA com KDF selecionável
                out_bytes = rana_expand(seed_mixed, cfg=cfg)

                out_bits = bytes_to_bits(out_bytes)

                if limit_out is not None:
                    remaining = limit_out - total_out_bits

                    if remaining <= 0:
                        break

                    if len(out_bits) > remaining:
                        out_bits = out_bits[:remaining]

                fout.write(out_bits)
                total_out_bits += len(out_bits)

                if args.progress_every > 0 and chunk_idx % args.progress_every == 0:
                    avg_expansion = total_out_bits / max(1, total_in_bits)
                    print(
                        f"[progress] chunks={chunk_idx} "
                        f"in_bits={total_in_bits} "
                        f"out_bits={total_out_bits} "
                        f"avg_expansion={avg_expansion:.4f}x"
                    )

                if limit_out is not None and total_out_bits >= limit_out:
                    break

        if chunk_buf:
            print(
                f"[warn] trailing incomplete chunk ignored: {len(chunk_buf)} bits",
                file=sys.stderr,
            )

    print(
        f"[done] kind_kdf={args.kind_kdf} "
        f"chunks={chunk_idx} "
        f"in_bits={total_in_bits} "
        f"out_bits={total_out_bits} "
        f"out={out_path}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())