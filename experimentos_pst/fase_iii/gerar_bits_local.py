import secrets

NUM_BITS = 20_000_000
OUTPUT_FILE = "data/pst/bits_entropia_local.txt"

# Converter bits → bytes
num_bytes = (NUM_BITS + 7) // 8

# Gerar entropia local
random_bytes = secrets.token_bytes(num_bytes)

# Converter para sequência de bits (ASCII '0'/'1')
bit_string = ''.join(f'{b:08b}' for b in random_bytes)

# Garantir exatamente NUM_BITS
bit_string = bit_string[:NUM_BITS]

# Salvar em formato texto (requerido pelo NIST)
with open(OUTPUT_FILE, "w") as f:
    f.write(bit_string)

print(f"Arquivo salvo com {len(bit_string)} bits em {OUTPUT_FILE}")
