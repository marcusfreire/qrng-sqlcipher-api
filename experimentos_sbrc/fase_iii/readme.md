# Experimento RanA – Expansão de Entropia com QRNG + CSPRNG e Avaliação NIST SP 800-22

## 1. Objetivo

Este experimento avalia um pipeline de **expansão determinística de entropia** no qual sementes provenientes de um **QRNG** são processadas em blocos, combinadas por **XOR com um CSPRNG local**, e expandidas por meio do **RanA (baseado em Argon2id)**.
O objetivo é gerar um fluxo binário de grande volume (dezenas a centenas de milhões de bits) adequado para avaliação estatística com o **NIST SP 800-22**, analisando o impacto de:

* reseeding frequente (por blocos),
* combinação de fontes (QRNG ⊕ CSPRNG),
* fator de expansão controlado e aleatório por bloco.

---

## 2. Visão geral do pipeline

1. **Entrada**
   Arquivo ASCII contendo bits `0/1` gerados por um QRNG (`randomBits_50M_QRNG.txt`).

2. **Segmentação**
   O fluxo é dividido em blocos consecutivos de **19456 bits** (2432 bytes).

3. **Hedging de entropia (XOR)**
   Cada bloco do QRNG é combinado via XOR com um bloco de mesmo tamanho gerado por um **CSPRNG do sistema operacional** (`secrets.token_bytes`).

4. **Expansão RanA**
   O bloco combinado é expandido com RanA:

   * baseado em Argon2id,
   * fator de expansão **derivado do próprio bloco** (estilo RanA),
   * limitado por `--max-factor`.

5. **Saída**
   Todos os blocos expandidos são concatenados e escritos em um único arquivo ASCII (`rana_extend.txt`).

6. **Avaliação estatística**
   O arquivo final é avaliado com a suíte **NIST SP 800-22**.

---

## 3. Estrutura de arquivos esperada

```
.
├── rana_expand_bits.py
├── rana_extender/
│   ├── __init__.py
│   └── rana.py
└── data/
    ├── randomBits_50M_QRNG.txt
    └── rana_extend.txt
```

---

## 4. Dependências

* Python ≥ 3.9
* Biblioteca Argon2:

```bash
pip install argon2-cffi
```

Nenhuma dependência externa adicional é necessária.

---

## 5. Parâmetros principais do experimento

| Parâmetro          | Descrição                                      |
| ------------------ | ---------------------------------------------- |
| `--in-bits`        | Arquivo ASCII contendo bits `0/1` do QRNG      |
| `--in-nbits`       | Número total de bits a serem lidos do arquivo  |
| `--chunk-bits`     | Tamanho do bloco (em bits), múltiplo de 8      |
| `--max-factor`     | Limite superior do fator de expansão por bloco |
| `--argon2-time`    | Custo temporal do Argon2id                     |
| `--argon2-mem-kib` | Memória do Argon2id (KiB)                      |
| `--argon2-par`     | Paralelismo do Argon2id                        |
| `--out`            | Arquivo de saída ASCII com os bits expandidos  |

---

## 6. Execução do experimento

### Chamada no terminal

```bash
python3 rana_expand_bits.py \
  --in-bits data/randomBits_50M_QRNG.txt \
  --in-nbits 50000000 \
  --chunk-bits 19456 \
  --out data/rana_extend.txt \
  --max-factor 4 \
  --argon2-time 2 \
  --argon2-mem-kib 16384 \
  --argon2-par 1 \
  --progress-every 20
```

Durante a execução, o script imprime o progresso a cada 20 blocos, incluindo:

* número de blocos processados,
* bits de entrada consumidos,
* bits de saída gerados,
* fator médio de expansão observado.

---

## 7. Avaliação com NIST SP 800-22

Após a execução:

1. Copie `rana_extend.txt` para o diretório de entrada do NIST STS.
2. Execute a suíte conforme a documentação oficial do NIST.
3. Analise:

   * proporção de sequências aprovadas,
   * distribuição uniforme de *p-values*,
   * sensibilidade de testes como **NonOverlappingTemplate**.

> Observação: o NIST SP 800-22 avalia **propriedades estatísticas**, não prova aumento de entropia nem imprevisibilidade criptográfica.

---

## 8. Considerações metodológicas

* A expansão RanA **não cria entropia nova**; ela redistribui a entropia existente da semente.
* O uso de blocos de 19456 bits promove **reseeding frequente**, reduzindo dependência entre segmentos.
* O XOR com CSPRNG local atua como mecanismo de **hedging**, mitigando falhas de uma única fonte.
* O parâmetro `--max-factor` deve ser tratado como **parâmetro experimental**, não como garantia de segurança.

--

## 9. Resultado esperado

* Geração de um arquivo binário expandido de dezenas ou centenas de milhões de bits.
* Comparação direta entre:

  * QRNG puro (50M bits),
  * QRNG + RanA por blocos,
  * impacto estatístico da expansão e do reseeding frequente.

