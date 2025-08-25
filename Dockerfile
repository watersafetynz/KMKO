FROM python:3.11-slim-bullseye

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
    curl gnupg gnupg2 apt-transport-https \
    locales \
    unixodbc unixodbc-dev \
    libgssapi-krb5-2 \
    libunwind8 \
    libssl1.1 || true; \
    echo "en_US.UTF-8 UTF-8" > /etc/locale.gen && locale-gen; \
    mkdir -p /etc/apt/keyrings; \
    curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /etc/apt/keyrings/microsoft.gpg; \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/11/prod bullseye main" \
    > /etc/apt/sources.list.d/mssql-release.list; \
    apt-get update; \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18; \
    rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# (optionally add a non-root user like above)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=10000
CMD ["bash", "-lc", "gunicorn 'run:app' --worker-class gthread --workers ${GUNICORN_WORKERS:-2} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-90} --bind 0.0.0.0:${PORT}"]
