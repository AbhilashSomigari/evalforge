FROM python:3.12-slim
WORKDIR /app
# Dependencies from the hash-pinned lock first (own layer, cached until the
# lock changes), then the project itself with --no-deps so nothing outside
# the lock can be pulled in.
COPY requirements/runtime.lock ./requirements/
RUN pip install --no-cache-dir --require-hashes -r requirements/runtime.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY examples ./examples
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/runs \
    && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1
CMD ["evalforge", "serve", "--host", "0.0.0.0", "--port", "8000"]
