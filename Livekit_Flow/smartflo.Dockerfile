FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONOPTIMIZE=2

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-build.txt ./

RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-build.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements-build.txt

COPY . .

# Compile bridge to .so
RUN python setup.py build_ext --inplace

RUN cp /app/*.so /app/smart-flo/ 2>/dev/null || true
RUN find /app/smart-flo -name "*.so" -exec strip {} \;

# Remove compiled .py source (IP protection)
RUN rm -f /app/smart-flo/smartflow_bridge.py

RUN find /app -name "*.c" -type f -delete

# -------- RUNTIME --------
FROM python:3.13-slim AS runtime

ENV PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY --from=builder /app/requirements.txt ./requirements.txt

RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt \
    && rm -rf /wheels requirements.txt

COPY --from=builder /app/smart-flo/ ./smart-flo/

EXPOSE 8000

ENTRYPOINT ["python", "-m", "uvicorn"]
CMD ["smart-flo.smartflow_bridge:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-level", "info"]
