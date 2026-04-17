FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PYTHONOPTIMIZE=2

WORKDIR /app

# -------- BUILDER (Cython compile + build wheels) --------
FROM base AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY requirements-build.txt .

RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements-build.txt
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements-build.txt

COPY . .

# Compile agent modules to .so
RUN python setup.py build_ext --inplace

# Move compiled .so files into the agent package
RUN cp /app/*.so /app/agent/ 2>/dev/null || true

# Strip debug symbols
RUN find /app/agent -name "*.so" -exec strip {} \;

RUN touch /app/agent/__init__.py

# Create startup wrapper
RUN printf '#!/usr/bin/env python\nimport sys\nimport os\n\nos.chdir("/app")\nsys.path.insert(0, "/app")\n\nif __name__ == "__main__":\n    from livekit import agents\n    from agent.web_rtc_server import server\n    print("🚀 Starting agent...")\n    agents.cli.run_app(server)\n' > /app/start_agent.py && \
    chmod +x /app/start_agent.py

# Remove compiled .py sources (IP protection)
RUN rm -f /app/agent/metrics.py \
    /app/agent/survey_agent.py \
    /app/agent/web_rtc_server.py

# Clean up build artifacts
RUN find /app -name "*.c" -type f -delete

# -------- RUNTIME --------
FROM python:3.13-slim AS runtime

ENV PYTHONOPTIMIZE=2 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY --from=builder /app/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --no-index --find-links /wheels -r /app/requirements.txt \
    && rm -rf /wheels /app/requirements.txt

COPY --from=builder /app/agent/ /app/agent/
COPY --from=builder /app/start_agent.py /app/start_agent.py

# Pre-download LiveKit model files
RUN python3 start_agent.py download-files

CMD ["python", "/app/start_agent.py", "start"]
