# Serves the published feature marts (data/marts/) with the FastAPI map explorer.
# The pipeline itself runs elsewhere; refresh by rebuilding marts and redeploying.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CITY_FABRIC_ROOT=/app

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY data/marts ./data/marts

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8000
# Railway injects PORT; proxy headers let uvicorn see the client's scheme behind its edge.
CMD ["sh", "-c", "exec uvicorn city_fabric.viz.server:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
