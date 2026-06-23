#!/bin/bash
# Script para preparar el servidor para recibir deploys automáticos
# Ejecutar en el servidor ANTES de configurar GitHub Actions

set -e

echo "🚀 Iniciando setup del servidor para ABCD Control Deploy..."

# 1. Verificar que tenemos permisos de sudo
if [[ $EUID -ne 0 ]]; then
   echo "❌ Este script debe ejecutarse con sudo"
   exit 1
fi

echo "✅ Ejecutando como root"

# 2. Instalar Docker si no está
echo "📦 Verificando Docker..."
if ! command -v docker &> /dev/null; then
    echo "📥 Instalando Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

# 3. Instalar Docker Compose si no está
echo "📦 Verificando Docker Compose..."
if ! command -v docker-compose &> /dev/null; then
    echo "📥 Instalando Docker Compose..."
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

echo "✅ Docker: $(docker --version)"
echo "✅ Docker Compose: $(docker-compose --version)"

# 4. Crear directorio de deploy
DEPLOY_DIR="/opt/abcd-control"
echo "📁 Creando directorio: $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR/logs"
mkdir -p "$DEPLOY_DIR/scripts"

# 5. Clonar o actualizar repositorio
echo "📥 Clonando/actualizando repositorio..."
if [ -d "$DEPLOY_DIR/.git" ]; then
    cd "$DEPLOY_DIR"
    git pull origin main
else
    cd /opt
    git clone https://github.com/diagonaleyewear/abc.git abcd-control
    cd abcd-control
fi

echo "✅ Repositorio actualizado"

# 6. Crear archivo .env si no existe
if [ ! -f .env ]; then
    echo "📝 Creando archivo .env..."
    echo "⚠️  IMPORTANTE: Editar .env con credenciales reales"
    echo ""
    
    cp .env.example .env
    echo ""
    echo "📋 Contenido de .env (rellenar valores reales):"
    cat .env
    echo ""
    echo "❌ DETENTE AQUÍ y edita .env con las credenciales reales:"
    echo "   nano .env"
    echo ""
    read -p "Presiona Enter cuando hayas configurado .env con valores reales..."
else
    echo "✅ Archivo .env ya existe"
fi

# 7. Verificar permisos del directorio
echo "🔐 Configurando permisos..."
chown -R rubensg:rubensg "$DEPLOY_DIR"
chmod 755 "$DEPLOY_DIR"
chmod +x "$DEPLOY_DIR/scripts/deploy.sh"

# 8. Crear directorio de logs y configurar rotación
echo "📋 Configurando logs..."
touch "$DEPLOY_DIR/logs/abcd-deploy.log"
chmod 666 "$DEPLOY_DIR/logs/abcd-deploy.log"

# 9. Verificar conectividad con BD (si .env está configurado)
echo "🔌 Verificando conectividad con base de datos..."
source "$DEPLOY_DIR/.env"

if command -v psql &> /dev/null; then
    if PGPASSWORD="$DB_PASSWORD" psql -h "$DB_HOST" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" > /dev/null 2>&1; then
        echo "✅ Conexión a BD exitosa"
    else
        echo "⚠️  No se pudo conectar a BD. Verificar .env"
    fi
fi

# 10. Prueba de build (opcional, puede tardar)
read -p "¿Deseas hacer una prueba de build Docker? (s/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Ss]$ ]]; then
    echo "🔨 Haciendo prueba de build..."
    cd "$DEPLOY_DIR"
    docker-compose build
    echo "✅ Build exitoso"
fi

echo ""
echo "🎉 Setup completado exitosamente!"
echo ""
echo "📝 Resumen:"
echo "   ✅ Docker instalado"
echo "   ✅ Repositorio clonado en: $DEPLOY_DIR"
echo "   ✅ Archivo .env configurado"
echo "   ✅ Permisos configurados"
echo ""
echo "🚀 Próximo paso:"
echo "   1. Generar SSH key: ssh-keygen -t ed25519 -f ~/.ssh/abcd_deploy -N \"\""
echo "   2. Agregar clave a GitHub Secrets"
echo "   3. Hacer push a GitHub y verá el deploy automático!"
echo ""
