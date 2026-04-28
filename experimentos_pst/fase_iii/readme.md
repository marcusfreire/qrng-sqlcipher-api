# Experimento RanA – Expansão de Entropia com QRNG + CSPRNG e Avaliação NIST SP 800-22

## 1. Objetivo

Este experimento avalia um pipeline de **expansão determinística de entropia** no qual sementes provenientes de um **QRNG** são processadas em blocos, combinadas por **XOR com um CSPRNG local**, e expandidas por meio do **RanA**, com suporte a múltiplos KDFs (**Argon2id, PBKDF2-HMAC-SHA256 e HKDF-SHA256**).

O objetivo é analisar o impacto de:

* diferentes funções de derivação de chave (KDF),
* reseeding frequente por blocos,
* combinação de fontes (QRNG ⊕ CSPRNG),
* fator de expansão controlado e derivado por bloco,

sobre a qualidade estatística avaliada via **NIST SP 800-22**.

---

## 2. Visão geral do pipeline

1. **Entrada**
   Arquivos ASCII contendo bits `0/1` gerados por QRNG:

   * `pst_er3_20M.txt`
   * `pst_er5_20M.txt`

2. **Segmentação**
   O fluxo é dividido em blocos consecutivos de **19456 bits**.

3. **Hedging de entropia (XOR)**  
   Cada bloco do QRNG é combinado com um bloco de mesmo tamanho gerado por um CSPRNG local:

```

bloco_final = QRNG ⊕ CSPRNG

```

4. **Expansão RanA**
O bloco combinado é expandido usando um KDF selecionável:

* fator de expansão derivado do próprio bloco (RanA),
* limitado por `--max-factor`.

5. **Saída**
Os blocos expandidos são concatenados em um único arquivo ASCII.

6. **Avaliação**
O arquivo final é avaliado com o **NIST SP 800-22**.

---

## 3. Estrutura de arquivos

```
.
├── rana_expand_bits.py
├── rana_extender/
│   ├── __init__.py
│   └── rana.py
└── data/pst/
├── pst_er3_20M.txt
├── pst_er5_20M.txt
├── rana_er3_argon2.txt
├── rana_er3_pbkdf2.txt
├── rana_er3_hkdf.txt
├── rana_er5_argon2.txt
├── rana_er5_pbkdf2.txt
└── rana_er5_hkdf.txt
```

---

## 4. Dependências

* Python ≥ 3.9
* Argon2:

```bash
pip install argon2-cffi
````

---

## 5. KDFs avaliadas

| KDF      | Descrição                                                      |
| -------- | -------------------------------------------------------------- |
| Argon2id | KDF resistente à memória, adequada contra ataques com GPU/ASIC |
| PBKDF2   | KDF iterativa baseada em HMAC, amplamente utilizada            |
| HKDF     | KDF leve baseada em HMAC, eficiente para expansão de chave     |

---

## 6. Parâmetros principais

| Parâmetro      | Descrição                                |
| -------------- | ---------------------------------------- |
| `--in-bits`    | Arquivo de entrada (bits ASCII)          |
| `--in-nbits`   | Total de bits a serem processados        |
| `--chunk-bits` | Tamanho do bloco                         |
| `--max-factor` | Fator máximo de expansão por bloco       |
| `--kind-kdf`   | Tipo de KDF (`argon2`, `pbkdf2`, `hkdf`) |
| `--out`        | Arquivo de saída                         |

---

## 7. Execuções do experimento

### 7.1 ER = 3

#### Argon2id

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er3_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er3_argon2.txt \
  --kind-kdf argon2 \
  --max-factor 4 \
  --argon2-time 2 \
  --argon2-mem-kib 16384 \
  --argon2-par 1 \
  --progress-every 20
```

#### PBKDF2

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er3_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er3_pbkdf2.txt \
  --kind-kdf pbkdf2 \
  --max-factor 4 \
  --pbkdf2-iterations 100000 \
  --progress-every 20
```

#### HKDF

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er3_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er3_hkdf.txt \
  --kind-kdf hkdf \
  --max-factor 4 \
  --progress-every 20
```

---

### 7.2 ER = 5

#### Argon2id

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er5_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er5_argon2.txt \
  --kind-kdf argon2 \
  --max-factor 4 \
  --argon2-time 2 \
  --argon2-mem-kib 16384 \
  --argon2-par 1 \
  --progress-every 20
```

#### PBKDF2

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er5_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er5_pbkdf2.txt \
  --kind-kdf pbkdf2 \
  --max-factor 4 \
  --pbkdf2-iterations 100000 \
  --progress-every 20
```

#### HKDF

```bash
python3 rana_expand_bits.py \
  --in-bits data/pst/pst_er5_20M.txt \
  --in-nbits 20000000 \
  --chunk-bits 19456 \
  --out data/pst/rana_er5_hkdf.txt \
  --kind-kdf hkdf \
  --max-factor 4 \
  --progress-every 20
```

---

## 8. Avaliação com NIST SP 800-22

1. Inserir os arquivos gerados no diretório do NIST STS
2. Executar a suíte
3. Analisar:

   * proporção de aprovação
   * distribuição de p-values
   * testes sensíveis (ex: NonOverlappingTemplate, Serial)

---

## 9. Considerações metodológicas

* O RanA **não aumenta entropia**, apenas realiza expansão determinística.

* O uso de blocos promove **reseeding frequente**.

* O XOR com CSPRNG fornece **hedging de entropia**.

* Diferentes KDFs introduzem diferentes propriedades:

  * Argon2 → custo computacional elevado
  * PBKDF2 → determinismo iterativo
  * HKDF → expansão eficiente

* O parâmetro `--max-factor` deve ser tratado como variável experimental.