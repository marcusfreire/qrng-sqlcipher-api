FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git build-essential cmake ninja-build pkg-config \
    libssl-dev python3 python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir pyserial

WORKDIR /tmp

# fixar versão do liboqs para reprodutibilidade
ARG LIBOQS_TAG=0.15.0
RUN git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs.git

WORKDIR /tmp/liboqs
RUN cmake -S . -B build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_SHARED_LIBS=ON \
      -DOQS_USE_OPENSSL=ON \
 && cmake --build build \
 && cmake --install build

# App
WORKDIR /app
COPY kem_runner.c /app/kem_runner.c
COPY qrng_direct.py /app/qrng_direct.py

RUN gcc -O3 -std=c11 -Wall -Wextra -o /app/kem_runner /app/kem_runner.c \
    -loqs -lcrypto

COPY run_kem_bench.py /app/run_kem_bench.py
RUN python3 -m pip install --no-cache-dir pandas

ENV LD_LIBRARY_PATH=/usr/local/lib