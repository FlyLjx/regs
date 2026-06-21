FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    CHATGPT2API_REG_APP_ROOT=/app/reg

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    curl-cffi>=0.15.0 \
    fastapi>=0.136.0 \
    python-multipart>=0.0.20 \
    uvicorn>=0.35.0

COPY reg ./reg
COPY services ./services
COPY utils ./utils
COPY web ./web
COPY README.md ./README.md
COPY pyproject.toml ./pyproject.toml

EXPOSE 8080

CMD ["uvicorn", "reg.web_main:app", "--host", "0.0.0.0", "--port", "8080", "--access-log"]
