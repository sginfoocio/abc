#!/bin/bash
# Script para iniciar la aplicación con Docker en desarrollo local

set -e

echo "🚀 Iniciando ABCD Control Application..."
echo ""

# Colores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Verificar si Docker está instalado
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⚠️  Docker no está instalado. Instalando...${NC}"
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
fi

# Verificar si Docker Compose está instalado
if ! command -v docker-compose &> /dev/null; then
    echo -e "${YELLOW}⚠️  Docker Compose no está instalado. Instalando...${NC}"
    sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    sudo chmod +x /usr/local/bin/docker-compose
fi

# Crear .env si no existe
if [ ! -f .env ]; then
    echo -e "${BLUE}📝 Creando archivo .env...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✅ .env creado. Revísalo antes de continuar!${NC}"
    echo ""
    read -p "¿Deseas editar el archivo .env ahora? (s/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Ss]$ ]]; then
        nano .env
    fi
fi

echo ""
echo -e "${BLUE}🔨 Construyendo imagen Docker...${NC}"
docker-compose build

echo ""
echo -e "${BLUE}▶️  Iniciando contenedor...${NC}"
docker-compose up -d

# Esperar a que el servicio esté listo
echo ""
echo -e "${BLUE}⏳ Esperando a que la aplicación inicie...${NC}"
sleep 5

# Intentar conexión
max_attempts=10
attempt=1
while [ $attempt -le $max_attempts ]; do
    if curl -s http://localhost:8501 > /dev/null; then
        echo -e "${GREEN}✅ ¡Aplicación lista!${NC}"
        break
    fi
    echo "Intento $attempt/$max_attempts..."
    sleep 2
    attempt=$((attempt + 1))
done

echo ""
echo -e "${GREEN}✨ ABCD Control está ejecutándose!${NC}"
echo ""
echo -e "${BLUE}📍 Acceder a:${NC} http://localhost:8501"
echo -e "${BLUE}📊 Dashboard:${NC} http://localhost:8501/?page=Inicio"
echo ""
echo -e "${YELLOW}📋 Comandos útiles:${NC}"
echo "  Ver logs:         docker-compose logs -f abcd-app"
echo "  Detener:          docker-compose down"
echo "  Reiniciar:        docker-compose restart abcd-app"
echo "  Estado:           docker-compose ps"
echo ""
