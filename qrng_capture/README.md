# Coleta de bits do QRNG RP2040 via Python (host)

### 1. Visão geral

Este repositório contém um script Python para PC que coleta bits já pós-processados de um QRNG baseado em **detecção balanceada de shot noise** em um optoacoplador, implementado sobre um **RP2040** (Raspberry Pi Pico) conforme:

* o artigo de referência do hardware e do modelo de entropia:
  **Singh et al. (2024)** – *A Compact Quantum Random Number Generator Based on Balanced Detection of Shot Noise* 
* o *QRNG Quick Start Guide (Windows)* fornecido pelo grupo de comunicações quânticas do TII (versão para Windows, aqui adaptada para Linux).

O script:

* sincroniza com o REPL do MicroPython na placa (`/dev/ttyACM0`);
* importa a biblioteca `DACADC` e o coletor de lixo `gc` no RP2040;
* chama repetidamente `DACADC.Toeplitz_Extractor_fast(BlockSize, extractionRatio)` na placa;
* converte cada amostra (inteiro `0–31`, ou seja, **5 bits**) em representação binária;
* grava um arquivo `bits_315k.txt` com todos os bits concatenados.

Os parâmetros padrão (`BLOCOS=30`, `BLOCKSIZE=2100`, `ER=5`) geram aproximadamente:

* [30 x 2100 x 5 = 315,000 bits] de saída pós-processada pelo extrator de Toeplitz.

---

### 2. Fundamentação física e modelo de entropia

#### 2.1. Shot noise e detecção balanceada

O QRNG é baseado nas flutuações de **shot noise** de um LED, medidas em configuração de **detecção balanceada** com dois fotodiodos dentro de um optoacoplador linear. A detecção balanceada cancela o ruído clássico correlacionado (ruído de fonte, térmico, eletrônica) e preserva o componente quântico não correlacionado, como descrito em Singh et al. (Seções II e III) .

* A corrente média dos dois fotodiodos é subtraída, cancelando o termo determinístico e o ruído clássico correlacionado.
* O ruído de shot, associado à estatística de Poisson dos fótons, permanece na saída e domina a variância quando o LED está polarizado corretamente.
* A relação entre variância de shot noise e corrente média é aproximadamente linear:
  $
  (\Delta i)^2 \propto \langle i \rangle.
  $

A placa do QRNG usa:

* **TIA + amplificador de tensão** em dois estágios;
* o ADC interno de 12 bits do RP2040 (ENOB ≈ 8 bits, SNR ≈ 53 dB);
* amostragem sub-Nyquist (≈ 100 kSa/s), para garantir independência entre amostras (IID) .
> O Transimpedance Amplifier (TIA) é o primeiro estágio analógico responsável por converter a corrente produzida pelos fotodiodos do optoacoplador em uma tensão proporcional e amplificada. Como fotodiodos geram sinais de corrente extremamente pequenos, o TIA é essencial para elevar o nível do sinal quântico (shot noise) acima do ruído eletrônico clássico. No QRNG, o TIA opera em detecção balanceada, recebendo as correntes de dois fotodiodos opostos; isso cancela ruído comum (ruído térmico, eletrônica, variações do LED) e preserva as flutuações quânticas independentes. O segundo estágio — um amplificador de tensão — aumenta ainda mais o sinal antes da digitalização pelo ADC do RP2040. Essa combinação garante ganho adequado, boa largura de banda e relação sinal-ruído suficiente para que o extrator de entropia opere de forma confiável.


#### 2.2. QCNR e extração de entropia

A qualidade da fonte é caracterizada por:

* **QCNR (Quantum-to-Classical Noise Ratio)**:
  $
  \mathrm{QCNR} = 20\log_{10}!\left(\frac{\sigma^2_{Q,C} - \sigma^2_C}{\sigma^2_C}\right),
  $
  onde $\sigma^2_C$ é a variância com LED desligado (ruído clássico) e $\sigma^2_{Q,C}$ é a variância com LED ligado .
* **Entropia mínima** do ADC:
  $
  H_\infty(X) = -\log_2\left(\max_x \Pr[X=x]\right).
  $

O *Quick Start Guide* implementa uma função `ExtractionRatio(...)` na biblioteca `DACADC` que estima a **entropia mínima por amostra de ADC** e a transforma em **taxa de extração** (número máximo de bits de entropia por amostra). A partir disso, o guia propõe **usar um extrator de Toeplitz** com uma margem de segurança, tipicamente **5 bits por amostra** para o ADC de 12 bits, como documentado tanto no guia quanto no artigo (Seção III, min-entropy e Toeplitz extractor) .

---

### 3. Arquitetura de software na placa (`DACADC.py` / `main.py`)

A biblioteca `DACADC` (rodando no RP2040 em MicroPython):

* controla o DAC (`mcp4725`) que ajusta a corrente do LED;
* usa `ADC_DMA` para capturar amostras do ADC interno;
* implementa:

  * `SelfTest(...)` — varredura de DAC para encontrar o ponto de máxima variância (antes da saturação do amplificador);
  * `QCNR(...)` — cálculo experimental do QCNR, comparando variações LED OFF/ON;
  * `ExtractionRatio(...)` — estima entropia mínima e taxa de extração;
  * `Toeplitz_Mat(...)` — construção da matriz de Toeplitz aleatória;
  * `Toeplitz_Extractor_fast(BlockSize, extractionRatio, DacValue)` — extrator rápido que:

    1. gera uma matriz de Toeplitz pseudoaleatória;
    2. captura `BlockSize` amostras do ADC;
    3. faz produtos escalares mod 2 entre o vetor de bits do ADC e cada linha da matriz;
    4. devolve um vetor `extr_bits` de inteiros, cada um representando `extractionRatio` bits (no nosso caso, `0–31` para `extractionRatio=5`) .

O `main.py` simplesmente:

```python
from DACADC import ji_qrng_btn, SelfTest, QCNR, max_dac, qrng_btn_fixed
from gc import collect
from machine import lightsleep

lightsleep(1)
collect()
lin_arr, _ = SelfTest(max_dac, 1000, 10)
print(*lin_arr)
_ = QCNR()
qrng_btn_fixed()
```

Ou seja:

1. roda o **SelfTest** e imprime as variâncias;
2. calcula o **QCNR**;
3. entra em um modo acionado por botão (`qrng_btn_fixed`), que usa o extrator internamente para aplicações de demonstração (LEDs, etc.).

---

### 4. Script de host – visão geral

O script `qrng_capture.py` roda no PC e fala com o REPL MicroPython do RP2040 via USB serial:

```python
#!/usr/bin/env python3
import sys, time, serial, ast

PORT="/dev/ttyACM0"
BAUD=115200
TIMEOUT=8.0
BLOCOS=30
BLOCKSIZE=2100
ER=5  # cada amostra tem 5 bits

CTRL_C=b'\x03'; NL=b"\r\n"
```

* `PORT`, `BAUD`, `TIMEOUT` — parâmetros da porta serial.

  * Em Linux, a placa geralmente aparece como `/dev/ttyACM0`. Ajuste se necessário.
* `BLOCOS` — número de chamadas repetidas ao extrator.
* `BLOCKSIZE` — tamanho do bloco em amostras de **extrator** (não ADC cru): cada chamada a `Toeplitz_Extractor_fast` retorna `BLOCKSIZE` números.
* `ER` — `extractionRatio` (número de bits por amostra, coerente com o modelo de entropia do guia e do artigo).

#### 4.1. Sincronização com o REPL

```python
def sync_repl(ser):
    ser.write(CTRL_C+CTRL_C); ser.flush(); time.sleep(0.1)
    ser.reset_input_buffer(); ser.reset_output_buffer()
    ser.write(NL); ser.flush()
    end=time.time()+3; buf=b""
    while time.time()<end:
        buf+=ser.read(512) or b""
        if b">>>" in buf: return True
    return False
```

* Envia `Ctrl+C` duas vezes para interromper qualquer código em execução na placa.
* Limpa buffers de entrada/saída.
* Envia uma linha em branco.
* Lê até encontrar o prompt `>>>`, indicando que o REPL está pronto.
* Se não encontrar em 3 s, retorna `False` (típico quando outra ferramenta está conectada, ex.: Thonny).

#### 4.2. Execução de comandos remotos

```python
def run_cmd(ser, py, timeout=12.0):
    ser.write(py+NL); ser.flush()
    end=time.time()+timeout; buf=b""
    while time.time()<end:
        buf+=ser.read(2048) or b""
        if b">>>" in buf or b"Traceback" in buf: break
    return buf.decode("utf-8","ignore")
```

* Envia `py` (string de comando Python) para o REPL.
* Lê a saída até:

  * aparecer o prompt `>>>` (comando terminou), ou
  * surgir `Traceback` (erro em MicroPython).
* Retorna a saída decodificada como `str`, preservando o texto necessário para extrair a lista.

#### 4.3. Extração da lista de inteiros

```python
def extract_list(txt):
    s = None
    for ln in txt.splitlines():
        t = ln.strip()
        if t.startswith("[") and t.endswith("]"):
            s = t; break
    return s
```

* Varre as linhas da saída do REPL.
* Procura a primeira linha que **pareça uma lista Python literal** (`[...]`).
* Devolve essa linha, que será avaliada com segurança usando `ast.literal_eval`.

#### 4.4. Função principal

```python
def main():
    ser = serial.Serial(PORT, BAUD, timeout=TIMEOUT)
    bits = []
    with ser:
        if not sync_repl(ser):
            sys.exit("REPL não sincronizou (feche IDE/Thonny).")
        run_cmd(ser, b"import DACADC, gc")
        cmd = f"gc.collect(); r=DACADC.Toeplitz_Extractor_fast({BLOCKSIZE},{ER}); print(list(r))".encode()

        for i in range(BLOCOS):
            out = run_cmd(ser, cmd, timeout=15.0)
            s = extract_list(out)
            if not s:
                if not sync_repl(ser): raise RuntimeError("sync falhou")
                out = run_cmd(ser, cmd, timeout=15.0)
                s = extract_list(out)
                if not s: raise RuntimeError(f"sem lista no bloco {i+1}")
            arr = ast.literal_eval(s)
            # converte cada número 0–31 em 5 bits
            for val in arr:
                bits.extend('{:05b}'.format(int(val)))
        with open("bits_315k.txt","w") as f:
            f.write("".join(bits))
    print(f"OK: bits2.txt ({len(bits)} bits, esperado ≈ {BLOCOS*BLOCKSIZE*ER})")
```

Passo a passo:

1. **Abre a porta serial** com `pyserial`.

2. **Sincroniza** com o REPL (`sync_repl`).

3. Executa:

   ```python
   import DACADC, gc
   ```

   na placa, garantindo que:

   * a biblioteca do QRNG (`DACADC`) esteja carregada;
   * o módulo `gc` (coletor de lixo do MicroPython) esteja disponível.

4. Constrói o comando `cmd` para ser executado na placa:

   ```python
   gc.collect(); 
   r = DACADC.Toeplitz_Extractor_fast(BLOCKSIZE, ER); 
   print(list(r))
   ```

   * `gc.collect()` **libera memória** antes de cada chamada ao extrator, importante dada a RAM limitada do RP2040 e o tamanho das estruturas usadas (matriz Toeplitz + buffer de amostras).
   * `Toeplitz_Extractor_fast(...)` retorna uma lista de inteiros em `[0, 2^ER-1]`, aqui `[0, 31]`.
   * `print(list(r))` gera uma única linha com a lista literal, que o host filtra e avalia.

5. Loop principal:

   * Executa `cmd` **BLOCOS** vezes:

     * se não encontrar nenhuma lista na saída:

       * tenta re-sincronizar com o REPL;
       * reexecuta o comando;
       * se ainda falhar, aborta com erro indicando o número do bloco.
   * Converte a linha textual da lista em objeto Python com `ast.literal_eval`, que é seguro para literais.
   * Para cada valor `val` em `arr`:

     * converte `val` para uma string de 5 bits (`'{:05b}'.format(int(val))`);
     * adiciona os caracteres `'0'`/`'1'` à lista global `bits`.

6. Ao final, escreve um arquivo texto `bits_315k.txt` com todos os bits concatenados.

7. Imprime confirmação com o total de bits e o valor esperado teórico `BLOCOS*BLOCKSIZE*ER`.

---

### 5. Relação com o artigo e o guia do QRNG

* **Modelo de entropia / extração**: o uso de `ER=5` corresponde ao modelo de entropia mínima detalhado no *Quick Start Guide*, que calcula a taxa de extração a partir de:
  $
  H_\infty(X) = -\log_2\left(\max_x P(X=x)\right),
  $
  e escolhe um número de bits por amostra **estritamente abaixo** do limite teórico para manter margem de segurança.
* **Extrator de Toeplitz**: a função `Toeplitz_Extractor_fast` implementa um extrator de Toeplitz otimizado no microcontrolador, conforme descrito no guia (seção que detalha `Toeplitz_Extractor_fast(BlockSize, extractionRatio, DacValue)`) e é consistente com o uso de extratores universais discutido na literatura de QRNG, como em Singh et al. (Seção III, extração com Toeplitz) .
* **QCNR e qualidade quântica**: antes de usar o QRNG em produção, recomenda-se:

  1. Rodar `SelfTest(...)` e `QCNR(...)` na própria placa para confirmar:

     * ponto de operação `max_dac` antes da saturação;
     * QCNR ≥ 30 dB, como relatado para o dispositivo de referência .
  2. Coletar grandes amostras de bits com este script e validar com baterias estatísticas (por exemplo, *dieharder*, conforme sugerido no artigo) .

---

### 6. Uso típico

1. **Preparar a placa QRNG**

   * Gravar o firmware MicroPython com `DACADC.py` e `main.py` na placa.
   * Conectar a placa via USB ao host (Linux).

2. **Verificar REPL e calibração (opcional, mas recomendado)**

   * Abrir um terminal serial (screen, minicom ou Thonny) no RP2040.
   * Importar `DACADC` e rodar:

     ```python
     import DACADC
     lin_arr, max_dac = DACADC.SelfTest()
     DACADC.QCNR()
     ```
   * Confirmar que o QCNR está em linha com os valores esperados (típicamente > 10 dB, idealmente > 30 dB, dependendo do lote).

3. **Executar o coletor de bits no PC**

   * Instalar dependências no host:

     ```bash
     pip install pyserial
     ```
   * Salvar o script como `qrng_capture.py`.
   * Rodar:

     ```bash
     python3 qrng_capture.py
     ```
   * Ao término, um arquivo `bits_315k.txt` será criado no diretório atual.

4. **Pós-processamento / uso criptográfico**

   * Opcionalmente converter os bits em hexadecimal, blocos de 128/256/2048 bits etc.
   * Alimentar um KDF (ex.: HKDF, Argon2, etc.) para derivar chaves criptográficas, respeitando a entropia efetiva por bit.

---

### 7. Parâmetros ajustáveis

* `PORT`: ajuste se o dispositivo aparecer como `/dev/ttyACM1`, `/dev/ttyUSB0` etc.
* `BLOCOS`: aumenta ou reduz a quantidade total de bits. Exemplo: 100 blocos → 100 × 2100 × 5 = 1 050 000 bits.
* `BLOCKSIZE`: deve caber na RAM do RP2040. Valores muito grandes podem causar erros de alocação, mesmo com `gc.collect()`.
* `ER`: **não aumente** sem recalibrar a entropia mínima com `ExtractionRatio(...)` e repetir a análise de entropia conforme descrito no artigo/guia.

### 8. Referências

1. SINGH, Jaideep; PIERA, Rodrigo; KUROCHKIN, Yury; GRIEVE, James A. **A Compact Quantum Random Number Generator Based on Balanced Detection of Shot Noise**. arXiv:2409.20515 [quant-ph], 2024. Disponível em: <[https://arxiv.org/abs/2409.20515>](https://arxiv.org/abs/2409.20515>). Acesso em: 17 nov. 2025.

2. QUANTUM COMMUNICATIONS GROUP (QRC, TII). **QRNG Quick Start Guide (Windows)**. Abu Dhabi: Technology Innovation Institute, 2024. Documento técnico interno fornecido com o dispositivo QRNG.

<p align="center" style="background-color:#0A1A2F; padding:20px; border-radius:12px;">
  <img src="../docs/QuIIN.png" width="240" alt="QUIIN Logo"/>
</p>

<p align="center">
  <strong>Projeto QUIIN – Quantum Industrial Innovation</strong><br>
  https://quiin.senaicimatec.com.br/
</p>

