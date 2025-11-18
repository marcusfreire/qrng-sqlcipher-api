#!/usr/bin/env python3
import sys, time, serial, ast

PORT="/dev/ttyACM0"
BAUD=115200
TIMEOUT=5.0
BLOCOS=100
BLOCKSIZE=2100
ER=5  # cada amostra tem 5 bits

CTRL_C=b'\x03'; NL=b"\r\n"

def sync_repl(ser):
    ser.write(CTRL_C+CTRL_C); ser.flush(); time.sleep(0.1)
    ser.reset_input_buffer(); ser.reset_output_buffer()
    ser.write(NL); ser.flush()
    end=time.time()+3; buf=b""
    while time.time()<end:
        buf+=ser.read(512) or b""
        if b">>>" in buf: return True
    return False

def run_cmd(ser, py, timeout=12.0):
    ser.write(py+NL); ser.flush()
    end=time.time()+timeout; buf=b""
    while time.time()<end:
        buf+=ser.read(2048) or b""
        if b">>>" in buf or b"Traceback" in buf: break
    return buf.decode("utf-8","ignore")

def extract_list(txt):
    s = None
    for ln in txt.splitlines():
        t = ln.strip()
        if t.startswith("[") and t.endswith("]"):
            s = t; break
    return s

def main():
    ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    bits = []
    with ser:
        if not sync_repl(ser):
            sys.exit("REPL não sincronizou (feche IDE/Thonny).")
        run_cmd(ser, b"import DACADC, gc")
        cmd = f"gc.collect(); r=DACADC.Toeplitz_Extractor_fast({BLOCKSIZE},{ER}); print(list(r))".encode()

        for i in range(BLOCOS):
            out = run_cmd(ser, cmd, timeout=5.0)
            s = extract_list(out)
            if not s:
                if not sync_repl(ser): raise RuntimeError("sync falhou")
                out = run_cmd(ser, cmd, timeout=5.0)
                s = extract_list(out)
                if not s: raise RuntimeError(f"sem lista no bloco {i+1}")
            arr = ast.literal_eval(s)
            # converte cada número 0–31 em 5 bits
            for val in arr:
                bits.extend('{:05b}'.format(int(val)))
        with open("randomBits.txt","w") as f:
            f.write("".join(bits))
    print(f"OK: randomBits.txt ({len(bits)} bits, esperado ≈ {BLOCOS*BLOCKSIZE*ER})")

if __name__=="__main__":
    main()
