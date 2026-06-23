#!/bin/bash
# Script para ejecutar deploy desde GitHub Actions
# Este script se ejecuta en el servidor cuando GitHub Actions hace SSH

set -e

DEPLOY_DIR="/opt/abcd-control"
LOG_DIR="$DEPLOY_DIR/logs"
LOG_FILE="$LOG_DIR/abcd-deploy.log"

mkdir -p "$LOG_DIR"

# Función para loguear
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Función para enviar notificación Slack (opcional)
notify_slack() {
    if [ -n "$SLACK_WEBHOOK" ]; then
        curl -X POST "$SLACK_WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{\"text\": \"$1\"}"
    fi
}

log "🚀 Iniciando deploy..."

# Verificar que el directorio existe
if [ ! -d "$DEPLOY_DIR" ]; then
    log "❌ Directorio $DEPLOY_DIR no existe"
    exit 1
fi

cd "$DEPLOY_DIR"
log "📁 Directorio: $(pwd)"

# Pull del repositorio
log "📥 Descargando cambios..."
if git pull origin main >> "$LOG_FILE" 2>&1; then
    log "✅ Git pull exitoso"
else
    log "❌ Error en git pull"
    notify_slack "❌ Deploy fallido: Error en git pull"
    exit 1
fi

# Verificar que .env existe
if [ ! -f .env ]; then
    log "❌ Archivo .env no encontrado"
    exit 1
fi

# Mostrar versión anterior
PREVIOUS_IMAGE=$(docker compose images -q abcd-app 2>/dev/null || echo "none")
log "📦 Imagen anterior: $PREVIOUS_IMAGE"

# Build de imagen
log "🔨 Construyendo imagen Docker..."
if docker compose build --no-cache >> "$LOG_FILE" 2>&1; then
    log "✅ Build completado"
else
    log "❌ Error en build Docker"
    notify_slack "❌ Deploy fallido: Error en build Docker"
    exit 1
fi

# Obtener nueva imagen
NEW_IMAGE=$(docker compose images -q abcd-app 2>/dev/null || echo "unknown")
log "📦 Nueva imagen: $NEW_IMAGE"

# Detener contenedor actual
log "⏹️  Deteniendo contenedor actual..."
docker compose down >> "$LOG_FILE" 2>&1

# Pequeña espera
sleep 2

# Iniciar nuevo contenedor
log "▶️  Iniciando nuevo contenedor..."
if docker compose up -d >> "$LOG_FILE" 2>&1; then
    log "✅ Contenedor iniciado"
else
    log "❌ Error al iniciar contenedor"
    notify_slack "❌ Deploy fallido: Error al iniciar contenedor"
    exit 1
fi

# Esperar a que esté listo
log "⏳ Esperando a que la aplicación esté lista..."
sleep 5

# Health check
max_attempts=10
attempt=0
while [ $attempt -lt $max_attempts ]; do
    if docker compose exec -T abcd-app curl -s http://localhost:8501/_stcore/health > /dev/null 2>&1; then
        log "✅ Health check exitoso"
        break
    fi
    attempt=$((attempt + 1))
    log "⏳ Health check intento $attempt/$max_attempts..."
    sleep 2
done

if [ $attempt -eq $max_attempts ]; then
    log "⚠️  Health check no respondió después de $max_attempts intentos"
    log "📋 Últimos logs:"
    docker compose logs --tail=20 abcd-app >> "$LOG_FILE" 2>&1
fi

# Mostrar últimos logs
log "📋 Últimos logs:"
docker compose logs --tail=10 abcd-app >> "$LOG_FILE" 2>&1

log "✨ Deploy completado exitosamente"
notify_slack "✅ Deploy exitoso - ABCD Control actualizado"

# Limpiar imágenes antiguas (opcional)
log "🧹 Limpiando imágenes antiguas..."
docker image prune -f >> "$LOG_FILE" 2>&1

exit 0
