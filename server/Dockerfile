# One image for the API, the worker and the release step (see Procfile).
# Python 3.12 everywhere: .python-version pins the same minor for Vercel, and
# 3.12 is the syntax floor, so the VM and Vercel run exactly what CI checks.
# tests/foundation/test_w0b_ops.py fails if the two pins ever drift apart.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 clefs && chown -R clefs /app
USER clefs

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --proxy-headers --timeout-graceful-shutdown 10"]
