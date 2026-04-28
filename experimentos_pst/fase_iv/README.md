# Benchmark de concorrência para API EaaS

Este diretório contém um benchmark concorrente para avaliar uma API de **Entropy-as-a-Service (EaaS)** sob múltiplos clientes concorrentes. O objetivo é medir **unicidade**, **desempenho**, **fairness** e **estabilidade** durante o consumo atômico de blocos de entropia por meio do endpoint `POST /keys/pop?size_bits={N}`.

O script principal é:

- `eaas_concurrency_bench.py`

---

## 1. Objetivo experimental

O benchmark foi projetado para produzir evidências sobre três hipóteses operacionais:

1. **A operação atômica preserva unicidade sob concorrência**, isto é, não há reutilização de `key_id` entre clientes concorrentes.
2. **A arquitetura desacoplada da fonte física escala melhor que o acesso direto ao hardware**, porque amortiza o custo de aquisição de entropia.
3. **O tamanho da requisição afeta a eficiência do sistema**, em especial quando `size_bits=256`, pois um bloco físico de 2048 bits é consumido integralmente e o restante é descartado.

---

## 2. Requisitos

- Python 3.10 ou superior
- API EaaS acessível por HTTP, caso o teste seja executado em modo real
- Dependências Python listadas em `requirements.txt`

Instalação:

```bash
python -m pip install -r requirements.txt
```

Dependências esperadas:

- `httpx`
- `matplotlib`

---

## 3. Estrutura esperada da resposta da API

O benchmark assume que uma resposta HTTP bem-sucedida retorna um JSON contendo pelo menos:

- `key_id`: identificador único do bloco consumido

Campos adicionais são opcionais, mas úteis:

- `size_bits`
- `slice_hex`

Se `size_bits` não vier no JSON, o script usa o valor solicitado na requisição como tamanho retornado.

---

## 4. Cargas de trabalho implementadas

O script suporta três perfis de carga:

### Carga A
Solicita sempre um bloco completo:

- `size_bits = 2048`

### Carga B
Solicita sempre uma fração do bloco:

- `size_bits = 256`

### Carga C
Mistura probabilística:

- 80% das requisições com `size_bits = 256`
- 20% das requisições com `size_bits = 2048`

### Observação importante sobre desperdício
O experimento assume a semântica descrita para a API:

- cada requisição bem-sucedida consome **um bloco físico de 2048 bits**
- se `size_bits < 2048`, os bits remanescentes são **descartados**

Assim, as cargas B e C podem reduzir a eficiência do uso do pool mesmo quando a latência por requisição parecer aceitável.

---

## 5. Modos de execução

### 5.1. Teste rápido, sem infraestrutura externa

Este modo usa `--transport mock`, útil para validar a instalação, o fluxo do script e a geração de saídas.

```bash
python eaas_concurrency_bench.py \
  --transport mock \
  --concurrency 2 \
  --duration 10 \
  --repeats 2 \
  --output-dir results_quicktest
```

Esse teste não mede a sua API real. Ele apenas valida o pipeline experimental.

### 5.2. Execução contra a API real

Exemplo completo:

```bash
python eaas_concurrency_bench.py \
  --base-url http://localhost:8081 \
  --endpoint /keys/pop \
  --concurrency 1 2 4 8 16 32 64 \
  --duration 60 \
  --repeats 20 \
  --workloads A B C \
  --output-dir results_real
```

Esse comando executa:

- 3 cargas: A, B e C
- 7 níveis de concorrência: 1, 2, 4, 8, 16, 32, 64
- 20 repetições por configuração
- 60 s por repetição

Total de configurações:

\[
3 \times 7 = 21
\]

Total de repetições:

\[
21 \times 20 = 420
\]

---

## 6. Opções relevantes

### `--duration`
Tempo de execução de cada repetição, em segundos.

### `--repeats`
Número de repetições por configuração. Para análise estatística, recomenda-se manter `20` ou mais.

### `--concurrency`
Lista com o número de clientes concorrentes.

Exemplo:

```bash
--concurrency 1 2 4 8 16 32 64
```

### `--workloads`
Lista das cargas a executar.

Exemplo:

```bash
--workloads A B C
```

### `--stop-on-exhaustion`
Encerra a repetição após o primeiro evento de exaustão do pool, tipicamente detectado por HTTP `4xx`.

Exemplo:

```bash
python eaas_concurrency_bench.py \
  --base-url http://localhost:8081 \
  --endpoint /keys/pop \
  --concurrency 8 16 32 \
  --duration 60 \
  --repeats 20 \
  --workloads A B C \
  --stop-on-exhaustion \
  --output-dir results_exhaustion
```

Esse modo é útil quando o objetivo principal é caracterizar o comportamento do sistema próximo da exaustão do pool.

---

## 7. Saídas geradas

O diretório de saída conterá arquivos tabulares, análise textual e figuras.

### 7.1. Dados brutos

#### `raw_requests.csv`
Um registro por requisição. Campos típicos:

- `run_id`
- `workload`
- `concurrency`
- `client_id`
- `request_index`
- `t_start`
- `t_end`
- `latency_ms`
- `status_code`
- `timeout`
- `key_id`
- `requested_size_bits`
- `returned_size_bits`
- `success`

Uso principal:

- auditoria detalhada
- reanálise externa em pandas/R
- verificação de duplicidade
- reconstrução de séries temporais

#### `raw_backlog.csv`
Amostras temporais do número de requisições pendentes ao longo da repetição.

Uso principal:

- detectar saturação progressiva
- visualizar formação de fila
- correlacionar backlog com aumento de latência

### 7.2. Resumos estatísticos

#### `run_summary.csv`
Resumo por repetição e por configuração.

Inclui métricas como:

- total de requisições
- sucessos e falhas
- `p50`, `p95`, `p99`
- `req/s`
- `bits/s`
- `keys/s`
- taxa de `4xx`, `5xx` e `timeouts`
- duplicatas de `key_id`
- índice de Jain

#### `aggregate_summary.csv`
Resumo agregado por configuração, com estatísticas sobre as repetições.

Inclui, entre outras, medidas como:

- mediana
- intervalos de confiança via bootstrap
- percentis agregados

Esse arquivo é o ponto de partida para tabelas de artigo.

### 7.3. Análise textual

#### `analysis.txt`
Texto analítico gerado automaticamente. O objetivo é produzir um resumo técnico das evidências observadas em cada experimento.

### 7.4. Figuras

O script gera gráficos em PNG, sem configuração explícita de cores.

Arquivos típicos:

- `latency_vs_concurrency.png`
- `throughput_vs_concurrency.png`
- `fairness_vs_concurrency.png`
- `bits_per_s_vs_concurrency.png`
- `latency_timeseries_<workload>_N<k>.png`
- `backlog_<workload>_N<k>.png`

---

## 8. Como interpretar os resultados

### 8.1. Unicidade

Critério principal:

- verificar se o número de duplicatas de `key_id` é igual a zero

Interpretação:

- **Esperado:** zero duplicatas
- **Se houver duplicatas:** há evidência de violação da atomicidade, falha de isolamento entre clientes ou erro no mecanismo de marcação/remoção do bloco consumido

### 8.2. Latência

Métricas usadas:

- `p50`: comportamento típico
- `p95`: cauda operacional
- `p99`: eventos extremos

Interpretação:

- crescimento moderado com a concorrência é esperado
- crescimento acentuado ou não linear sugere saturação de algum recurso compartilhado, como banco, threadpool, conexão HTTP ou fila interna

### 8.3. Throughput por requisição

Métrica:

\[
\text{req/s} = \frac{\text{requisições totais}}{\text{tempo total}}
\]

Interpretação:

- se `req/s` cresce com a concorrência e depois entra em platô, o sistema atingiu um limite de capacidade
- se `req/s` cai com concorrência adicional, o sistema entrou em região de contenção

### 8.4. Throughput efetivo de entropia

Métrica:

\[
\text{bits/s} = \frac{\sum \text{returned\_size\_bits}}{\text{tempo total}}
\]

Interpretação:

- é a métrica mais adequada para comparar utilidade criptográfica efetiva entre as cargas A, B e C
- uma carga pode ter `req/s` alto e `bits/s` baixo, o que significa bom ritmo de chamadas, mas baixa entrega útil de bits

### 8.5. Taxa de exaustão do pool

Métrica:

\[
\text{keys/s} = \frac{\text{blocos consumidos}}{\text{tempo total}}
\]

Como cada sucesso consome um bloco de 2048 bits, `keys/s` mede a taxa de drenagem do pool.

Interpretação:

- a carga B tende a esgotar o pool mais rapidamente por bit útil entregue
- comparar `keys/s` com `bits/s` ajuda a quantificar a ineficiência causada pelo descarte

### 8.6. Taxa de falha

Observar:

- `% 4xx`: normalmente associada à exaustão ou erro de uso esperado
- `% 5xx`: erro interno do serviço
- `% timeouts`: indício de saturação severa ou indisponibilidade transitória

Interpretação:

- aumento de `4xx` próximo do fim do teste é compatível com esgotamento do pool
- aumento de `5xx` ou timeout com backlog crescente sugere instabilidade do serviço sob carga

### 8.7. Fairness

O índice de Jain é calculado como:

\[
J(x) = \frac{\left(\sum_i x_i\right)^2}{n \sum_i x_i^2}
\]

onde `x_i` é o número de requisições bem-sucedidas do cliente `i`.

Interpretação:

- `J \approx 1`: distribuição equilibrada entre clientes
- `J` baixo: alguns clientes avançam mais que outros, indicando assimetria de atendimento ou competição desigual por recursos

### 8.8. Backlog

O backlog representa o número de requisições pendentes ao longo do tempo.

Interpretação:

- backlog estável e baixo: sistema acompanha a taxa de chegada
- backlog crescente: taxa de chegada maior que a taxa de serviço
- backlog crescente acompanhado de aumento de `p95/p99`: evidência forte de saturação operacional

---

## 9. Como identificar os principais fenômenos experimentais

### 9.1. Ponto em que a latência cresce de forma não linear

Use os gráficos:

- `latency_vs_concurrency.png`
- `latency_timeseries_<workload>_N<k>.png`

Critério prático:

- procure o primeiro nível de concorrência em que `p95` ou `p99` cresce muito mais rápido do que no nível anterior
- confirme se, no mesmo ponto, o throughput entra em platô e o backlog passa a crescer

Esse ponto marca o início da região de saturação.

### 9.2. Impacto da carga de 256 bits

Compare A versus B e C em:

- `bits_per_s_vs_concurrency.png`
- `throughput_vs_concurrency.png`
- `run_summary.csv`

Critério prático:

- se B apresentar `req/s` parecido ou maior que A, mas `bits/s` muito menor, isso evidencia desperdício estrutural do bloco
- compare também `keys/s` e calcule a eficiência útil:

\[
\text{eficiência} = \frac{\text{bits/s}}{2048 \times \text{keys/s}}
\]

Interpretação:

- carga A tende a eficiência próxima de 1
- carga B tende a eficiência próxima de \(256/2048 = 0.125\)
- carga C tende a valor intermediário, aproximadamente ponderado pela mistura

### 9.3. Evidência de contenção no banco SQLCipher

O benchmark **não mede diretamente** locks internos do SQLite/SQLCipher. Portanto, a inferência é indireta.

Indícios combinados:

- aumento forte de `p95/p99`
- backlog crescente
- throughput em platô ou queda
- fairness degradada
- ausência de aumento proporcional de `5xx`, o que indica que o sistema continua funcional, porém lento

Esse padrão é compatível com contenção em recurso serializado, incluindo escrita transacional, lock do banco ou seção crítica do consumo atômico.

### 9.4. Comportamento próximo da exaustão

Observar:

- crescimento de `%4xx`
- queda de `bits/s`
- backlog anômalo ao final da repetição
- mudança abrupta no padrão da série temporal de latência

Interpretação:

- quando o pool se aproxima do fim, a API pode alternar entre sucessos e falhas
- o instante da transição deve aparecer no `analysis.txt` e pode ser confirmado nos CSVs

---

## 10. Roteiro mínimo de análise para artigo

Uma sequência objetiva para análise acadêmica é:

1. Validar que não houve duplicatas de `key_id`.
2. Identificar o ponto de saturação via `p95/p99`, throughput e backlog.
3. Comparar A, B e C em `bits/s` e `keys/s`.
4. Quantificar o custo do descarte de blocos nas cargas B e C.
5. Verificar fairness para avaliar se a concorrência afeta clientes de forma homogênea.
6. Avaliar o comportamento próximo da exaustão com base em `%4xx` e séries temporais.

Uma formulação técnica possível para discussão é:

> Os resultados indicam que a unicidade foi preservada sob concorrência, sem duplicação de `key_id`, o que é consistente com a propriedade de consumo atômico do serviço. Observa-se, contudo, um ponto de saturação a partir de determinado nível de concorrência, caracterizado por crescimento acentuado de latência de cauda, formação de backlog e estabilização do throughput. Além disso, as cargas com requisições de 256 bits apresentam menor eficiência de uso do pool, pois cada sucesso consome integralmente um bloco de 2048 bits, reduzindo o throughput efetivo em bits úteis por segundo.

---

## 11. Exemplo de interpretação rápida dos arquivos

### Situação 1. `duplicates_key_id = 0` em todas as repetições
Conclusão:

- não há evidência experimental de violação da unicidade

### Situação 2. `req/s` sobe de N=1 até N=8, estabiliza em N=16 e cai em N=32
Conclusão:

- o sistema escala até certo ponto e depois entra em região de contenção

### Situação 3. Carga B tem `req/s` maior que A, mas `bits/s` muito menor
Conclusão:

- o sistema atende mais chamadas, mas entrega menos entropia útil por unidade de tempo
- há desperdício estrutural associado à granularidade física de 2048 bits

### Situação 4. `%4xx` cresce apenas nos níveis altos de concorrência e no final da execução
Conclusão:

- o serviço provavelmente se aproxima da exaustão do pool sob maior pressão de consumo

### Situação 5. `Jain < 0.8` em concorrência alta
Conclusão:

- a distribuição de sucesso entre clientes deixa de ser homogênea
- há indício de competição desigual por recurso compartilhado

---

## 12. Boas práticas de execução

- Execute o benchmark em ambiente isolado, sem outras cargas relevantes no host.
- Registre versão do Python, sistema operacional, CPU, memória e configuração da API.
- Mantenha o mesmo pool inicial entre repetições comparáveis, quando possível.
- Se o objetivo for comparar A, B e C, evite misturar mudanças de infraestrutura entre rodadas.
- Se houver proxy reverso, load balancer ou TLS, documente isso explicitamente.

---

## 13. Limitações

1. A inferência de contenção no SQLCipher é indireta. O benchmark observa sintomas no nível da aplicação, não locks internos do banco.
2. O comportamento do cliente HTTP também influencia a medição, embora o uso do mesmo cliente em todas as configurações preserve comparabilidade relativa.
3. Se a API retornar respostas heterogêneas ou campos inconsistentes, a interpretação do tamanho efetivo retornado pode exigir ajuste no parser.
4. Resultados em ambiente `mock` não devem ser usados para conclusões sobre escalabilidade da API real.

---

## 14. Arquivos do diretório

- `eaas_concurrency_bench.py`: benchmark principal
- `requirements.txt`: dependências Python
- `README.md`: instruções de execução e análise
- `smoke_results/`: exemplo de saída de teste rápido

---

## 15. Execução mínima recomendada

Para validar a infraestrutura:

```bash
python eaas_concurrency_bench.py \
  --transport mock \
  --concurrency 2 \
  --duration 10 \
  --repeats 2 \
  --output-dir smoke_results_local
```

Para produzir resultados experimentais utilizáveis:

```bash
python eaas_concurrency_bench.py \
  --base-url http://localhost:8081 \
  --endpoint /keys/pop \
  --concurrency 1 2 4 8 16 32 64 \
  --duration 60 \
  --repeats 20 \
  --workloads A B C \
  --output-dir results_real
```

---

## 16. Sugestão de organização da pasta de teste

Uma estrutura simples para adicionar ao repositório é:

```text
tests/
└── eaas_benchmark/
    ├── README.md
    ├── eaas_concurrency_bench.py
    ├── requirements.txt
    └── results/
```

Se desejar separar execução de resultados:

```text
tests/
└── eaas_benchmark/
    ├── README.md
    ├── src/
    │   └── eaas_concurrency_bench.py
    ├── requirements.txt
    └── runs/
        ├── smoke/
        └── campaign_2026-04-05/
```

