FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY examples ./examples
COPY runs ./runs
EXPOSE 8000
CMD ["evalforge", "serve", "--host", "0.0.0.0", "--port", "8000"]
