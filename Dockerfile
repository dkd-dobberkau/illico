FROM python:3.12-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libxml2-dev libxslt-dev \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md LICENSE ./
COPY illico_*.py illico_index.html ./
RUN pip install --no-cache-dir --user .

FROM python:3.12-slim
# Kein curl im Runtime-Image: der Healthcheck nutzt Pythons urllib (Python ist
# ohnehin da). Spart curl + libcurl4t64 samt transitiver libssh2/krb5 und
# damit deren fixlose Base-Image-CVEs an der Wurzel — statt sie zu ignorieren.
RUN useradd -m -u 1000 illico
WORKDIR /app
COPY --from=builder /root/.local /home/illico/.local
RUN mkdir -p /app/illico-data && chown illico:illico /app/illico-data
USER illico
ENV PATH=/home/illico/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ILLICO_DATA=/app/illico-data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"]
# Illico Single: Login-freier Kern ohne Multi-Tenancy/Auth — für den
# Selfhosted-Einsatz gedacht. Wer Auth/Multi-Tenancy braucht, betreibt Illico
# hinter einem eigenen Reverse Proxy mit Zugriffsschutz.
CMD ["uvicorn", "illico_app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
