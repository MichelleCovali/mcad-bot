FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
ENV VITE_API_BASE_URL=/api
RUN npm run build

FROM python:3.12-slim-bookworm AS application
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
RUN groupadd --system mcad && useradd --system --gid mcad --home /app mcad
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py commands.py news.py filters.py archive.py ./
COPY mcad/ ./mcad/
COPY --from=frontend-build /build/frontend/dist/client ./frontend/dist/client/
USER mcad
EXPOSE 8080
CMD ["sh", "-c", "python -m mcad migrate && exec uvicorn mcad.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-server-header"]
