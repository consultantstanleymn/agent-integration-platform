# syntax=docker/dockerfile:1
# Stage 1: build wheels so the final image has no compiler or pip cache.
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# Stage 2: small runtime image, non-root user.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
COPY alembic.ini ./
COPY migrations ./migrations
COPY prompts ./prompts
COPY evals ./evals
COPY docs/engagement/AUTHORITY.md ./docs/engagement/AUTHORITY.md
COPY mock_crm ./mock_crm
COPY docker-entrypoint.sh ./
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["uvicorn", "--factory", "aip.api.app:app_factory", "--host", "0.0.0.0", "--port", "8000"]
