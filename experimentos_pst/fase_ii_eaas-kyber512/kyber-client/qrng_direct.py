#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import math
import sys
import time
from dataclasses import dataclass
from typing import Optional, List

import serial

CTRL_C = b"\x03"
NL = b"\r\n"

ALLOWED_ER = {1, 3, 5, 7}


def _sync_repl(ser: serial.Serial, timeout_s: float = 2.0) -> bool:
    ser.write(CTRL_C + CTRL_C)
    ser.flush()
    time.sleep(0.05)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(NL)
    ser.flush()

    end = time.time() + timeout_s
    buf = b""
    while time.time() < end:
        buf += ser.read(256) or b""
        if b">>>" in buf:
            return True
    return False


def _run_cmd(ser: serial.Serial, py: bytes, timeout_s: float = 3.0) -> str:
    ser.write(py + NL)
    ser.flush()

    end = time.time() + timeout_s
    buf = bytearray()

    # heurística: detecta quando uma lista [ ... ] está completa
    in_list = False
    bracket_balance = 0

    while time.time() < end:
        chunk = ser.read(16384) or b""  # lê mais por iteração
        if chunk:
            buf += chunk

            # Atualiza balanço de colchetes apenas em bytes ASCII
            for ch in chunk:
                if ch == ord('['):
                    in_list = True
                    bracket_balance += 1
                elif ch == ord(']') and in_list:
                    bracket_balance -= 1

            # Sai se voltou ao prompt ou se viu Traceback
            if b">>>" in buf or b"Traceback" in buf:
                break

            # Sai se a lista parece completa (balanceado e já vimos '[')
            if in_list and bracket_balance == 0 and b"]" in buf:
                break

    return bytes(buf).decode("utf-8", "ignore")

def _extract_list_line(txt: str) -> Optional[str]:
    for ln in txt.splitlines():
        t = ln.strip()
        if t.startswith("[") and t.endswith("]"):
            return t
    return None


def pack_er_samples_to_bytes(samples: List[int], out_bits: int, er: int) -> bytes:
    """
    Converte amostras 0..(2^er-1) (er bits por amostra) em bytes,
    MSB-first no stream de bits.

    out_bits deve ser múltiplo de 8.
    """
    if er not in ALLOWED_ER:
        raise ValueError(f"er must be one of {sorted(ALLOWED_ER)}, got {er}")
    if out_bits % 8 != 0:
        raise ValueError("out_bits must be multiple of 8")

    out = bytearray(out_bits // 8)
    bitpos = 0
    maxv = (1 << er) - 1
    for v in samples:
        if bitpos >= out_bits:
            break
        x = int(v)
        if x < 0 or x > maxv:
            raise ValueError(f"sample out of range for er={er}: {x} (expected 0..{maxv})")

        # escreve er bits do MSB ao LSB
        for b in range(er - 1, -1, -1):
            if bitpos >= out_bits:
                break
            bit = (x >> b) & 1
            byte_i = bitpos // 8
            bit_i = 7 - (bitpos % 8)  # MSB-first
            out[byte_i] |= (bit << bit_i)
            bitpos += 1

    if bitpos < out_bits:
        raise RuntimeError(f"insufficient bits produced: {bitpos} < {out_bits}")
    return bytes(out)

@dataclass
class QRNGDirectConfig:
    port: str = "/dev/ttyACM0"
    baud: int = 115200
    timeout: float = 2.0
    er: int = 5
    # margem pequena de bits a mais que o solicitado
    margin_samples: int = 8


class QRNGDirectSource:
    def __init__(self, cfg: Optional[QRNGDirectConfig] = None):
        self.cfg = cfg or QRNGDirectConfig()
        self._ser: Optional[serial.Serial] = None
        if self.cfg.er not in ALLOWED_ER:
            raise ValueError(f"ER inválido: {self.cfg.er}")
        self._inited: bool = False

    def __enter__(self) -> "QRNGDirectSource":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        if self._ser is not None:
            return
        # leitura com timeout curto para permitir várias iterações em _run_cmd
        self._ser = serial.Serial(self.cfg.port, self.cfg.baud, timeout=0.1)

        if not _sync_repl(self._ser):
            self.close()
            raise RuntimeError("REPL não sincronizou. Feche Thonny/IDE e tente novamente.")
        # importa 1x
        out = _run_cmd(self._ser, b"import DACADC, gc", timeout_s=2.0)
        if "Traceback" in out:
            self.close()
            raise RuntimeError(f"Falha import DACADC. Saída:\n{out}")
        self._inited = True

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            finally:
                self._ser = None

    def get_bytes(self, size_bits: int) -> bytes:
        if size_bits % 8 != 0:
            raise ValueError("size_bits deve ser múltiplo de 8")

        if self._ser is None:
            self.open()
        assert self._ser is not None

        # BLOCKSIZE mínimo + margem pequena
        samples = math.ceil(size_bits / self.cfg.er) + self.cfg.margin_samples
        cmd = f"gc.collect(); r=DACADC.Toeplitz_Extractor_fast({samples},{self.cfg.er}); print(list(r))".encode()

        # timeout cresce com o tamanho solicitado; ajuste se necessário
        cmd_timeout = max(7.0, 7.0 * (size_bits / 2048.0)) 
        out = _run_cmd(self._ser, cmd, timeout_s=cmd_timeout)

        s = _extract_list_line(out)
        if not s:
            raise RuntimeError(f"Sem lista retornada. Saída:\n{out}")

        arr = ast.literal_eval(s)
        if not isinstance(arr, list) or len(arr) == 0:
            raise RuntimeError(f"Lista inválida: {arr}")

        # Empacota o mínimo de bits
        return pack_er_samples_to_bytes(arr, size_bits,er=self.cfg.er)


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--size-bits", type=int, required=True,
                    help="Número de bits a coletar (positivo e múltiplo de 8)")
    ap.add_argument("--er", type=int, default=5, choices=sorted(ALLOWED_ER))
    ap.add_argument("--format", choices=["hex", "bin"], default="hex")
    ap.add_argument("--margin-samples", type=int, default=8)

    args = ap.parse_args()

    cfg = QRNGDirectConfig(
        port=args.port,
        baud=args.baud,
        timeout=args.timeout,
        er=args.er,
        margin_samples=args.margin_samples,
    )

    try:
        with QRNGDirectSource(cfg) as q:
            data = q.get_bytes(args.size_bits)
            if args.format == "hex":
                sys.stdout.write(data.hex())
            else:
                sys.stdout.write("".join(f"{b:08b}" for b in data))
            sys.stdout.flush()
        return 0
    except Exception as e:
        print(f"[qrng_direct] ERRO: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
