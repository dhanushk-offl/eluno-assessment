FROM oven/bun:1 AS frontend-build
WORKDIR /app/ui
COPY ui/package.json ui/bun.lock ./
RUN bun install --frozen-lockfile
COPY ui/ ./
RUN bun run build

FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=frontend-build /usr/local/bin/bun /usr/local/bin/bun
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pos/ ./pos/
COPY starter-kit/ ./starter-kit/
COPY scripts/container_start.py ./scripts/container_start.py
COPY --from=frontend-build /app/ui/build ./ui/build
COPY --from=frontend-build /app/ui/node_modules ./ui/node_modules
COPY ui/package.json ./ui/package.json
ENV PYTHONUNBUFFERED=1 PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '3000') + '/api/health', timeout=2)" || exit 1
CMD ["python", "scripts/container_start.py"]
