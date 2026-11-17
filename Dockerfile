# RentHome Ibiza – imagen para homelab (funciona en Mac Apple Silicon y en x86)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Europe/Madrid

WORKDIR /app
COPY requirements.txt .
# libatomic1: lo necesita el Node que incluye Playwright en la imagen slim (arm64)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libatomic1 \
 && pip install -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY renthome ./renthome
COPY config.yaml ./
COPY tests ./tests

# data/ y docs/ se montan como volúmenes para conservar el historial
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=4)"
CMD ["python", "-m", "renthome.serve"]
