# Multi-stage build para optimizar el tamaño de imagen
FROM python:3.11-slim as builder

WORKDIR /app

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias Python
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage final
FROM python:3.11-slim

WORKDIR /app

# Instalar solo lo necesario en runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copiar dependencias instaladas desde el builder
COPY --from=builder /root/.local /root/.local

# Copiar archivos de la aplicación
COPY app_enhanced.py .
COPY db_loader.py .
COPY engine.py .
COPY db_config.py .
COPY transform_luxottica_masterdata.py .
COPY validate_masterdata_odoo_dryrun.py .
COPY graph_mail_downloader.py .
COPY save_abcd_weekly_snapshot.py .
COPY watchlist_config.py .
COPY check_pedidos_vigilados.py .

# Crear directorio para configuración de Streamlit
RUN mkdir -p ~/.streamlit

# Configurar PATH
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1

# Configurar Streamlit
RUN echo "\
[server]\n\
port = 8501\n\
headless = true\n\
maxUploadSize = 200\n\
enableXsrfProtection = true\n\
\n\
[logger]\n\
level = info\n\
" > ~/.streamlit/config.toml

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=5)" || exit 1

# Comando para ejecutar la aplicación
CMD ["streamlit", "run", "app_enhanced.py", "--server.address", "0.0.0.0"]
