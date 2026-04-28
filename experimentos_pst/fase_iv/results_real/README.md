# EaaS concurrency benchmark

## Requisitos

```bash
python -m pip install -r requirements.txt
```

## Teste rápido sem infraestrutura externa

```bash
python eaas_concurrency_bench.py   --transport mock   --concurrency 2   --duration 10   --repeats 2   --output-dir results_quicktest
```

## Execução contra a API real

```bash
python eaas_concurrency_bench.py   --base-url http://localhost:8081   --endpoint /keys/pop   --concurrency 1 2 4 8 16 32 64   --duration 60   --repeats 20   --workloads A B C   --output-dir results_real
```

## Observações

1. A carga A usa `size_bits=2048`.
2. A carga B usa `size_bits=256`.
3. A carga C usa mistura 80% de 256 bits e 20% de 2048 bits.
4. Se `size_bits < 2048`, o script contabiliza desperdício assumindo descarte do restante do bloco.
5. O modo `--stop-on-exhaustion` encerra uma repetição após o primeiro HTTP 4xx.

## Saídas

- `raw_requests.csv`: dados brutos por requisição.
- `raw_backlog.csv`: amostras temporais de requisições pendentes.
- `run_summary.csv`: resumo por repetição.
- `aggregate_summary.csv`: agregação por configuração.
- `analysis.txt`: análise textual automática.
- gráficos `.png`.
