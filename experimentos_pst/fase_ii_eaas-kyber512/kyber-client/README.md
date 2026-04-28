# PoC: EaaS (QRNG via API) + ML-KEM-512 (Kyber512) em Docker sidecar

## Pré-requisitos
- API já rodando no host em: http://localhost:8081
- Endpoint disponível:
  curl -sS -X POST "http://localhost:8081/keys/pop?size_bits=2048"

## Execução (Linux)
1) Suba sua API normalmente (fora deste compose).
2) Rode:
   docker compose up --build

Saída:
- ./out/results.csv
- ./out/errors.log

## Sobre a rede
Este compose usa `network_mode: "host"`, então o container acessa a API via:
http://127.0.0.1:8081

Em macOS/Windows, `network_mode: host` não se comporta igual.
Alternativa: trocar para host.docker.internal e remover network_mode.

## Modos do benchmark
- MODE=both (default): roda baseline e qrng
- MODE=baseline: roda somente RNG padrão do processo
- MODE=qrng: chama a API e usa os 256 bytes como seed de um DRBG (ChaCha20 stream)
  e substitui o RNG do liboqs via `OQS_randombytes_custom_algorithm`.

## O que é medido
Em results.csv:
- t_http_ms: tempo do curl (somente modo qrng)
- t_kem_ms: tempo de keygen+encaps+decaps do ML-KEM-512
- t_total_ms: soma (do início da repetição ao fim)
- entropy_bytes_len e entropy_sha256: auditoria (sem publicar entropia)

## Limitações (importante)
- A injeção de seed não usa uma API "seed" do algoritmo; ela substitui o RNG do liboqs,
  que é a forma suportada pelo próprio liboqs para RNG custom (via callback).
- O DRBG é um stream baseado em ChaCha20 para PoC/bench. Não é uma reivindicação FIPS.
- O objetivo é demonstrar o uso do EaaS como fonte de entropia e medir latência/estabilidade.