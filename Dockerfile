# Dockerfile - API FastAPI com SQLCipher (Alpine)
FROM python:3.12-alpine

# Atualiza e instala toolchain + sqlcipher para compilar pysqlcipher3
RUN apk add --no-cache \
      build-base \
      python3-dev \
      pkgconfig \
      sqlcipher \
      sqlcipher-dev \
      bash

# Diretório da aplicação
WORKDIR /app

# Dependências
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Código da API
COPY api /app/api
COPY tools /app/tools
COPY entrypoint.sh /app/entrypoint.sh

# Cria usuário/grupo não-root (uid/gid 1000) e pasta de dados
RUN addgroup -g 1000 app && adduser -D -G app -u 1000 app && \
    mkdir -p /data && chown -R app:app /data && chmod 750 /data && \
    chown app:app /app/entrypoint.sh && chmod 750 /app/entrypoint.sh

EXPOSE 8081

# Troca para usuário não-root
USER app:app

# ENTRYPOINT via /bin/sh para evitar problema com shebang/CRLF
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
