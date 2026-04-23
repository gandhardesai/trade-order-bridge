FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY trade_order_bridge ./trade_order_bridge

RUN pip install --no-cache-dir -U pip setuptools wheel && pip install --no-cache-dir ".[ibkr]"

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "trade_order_bridge.main:app", "--host", "0.0.0.0", "--port", "8000"]
