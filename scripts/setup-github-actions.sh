#!/bin/bash
# Setup GitHub Actions - Automated Script
# Este script automatiza los pasos 1-2 de GitHub Actions setup

set -e

echo "🚀 GitHub Actions Setup Script"
echo "=============================="
echo ""

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Paso 1: Generar SSH Key
echo -e "${BLUE}STEP 1: Generando SSH Key...${NC}"

SSH_KEY_PATH="$HOME/.ssh/abcd_deploy"

if [ -f "$SSH_KEY_PATH" ]; then
    echo -e "${YELLOW}⚠️  SSH Key ya existe en $SSH_KEY_PATH${NC}"
    read -p "¿Sobrescribir? (s/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Ss]$ ]]; then
        echo "Usando clave existente"
    else
        rm "$SSH_KEY_PATH" "$SSH_KEY_PATH.pub"
    fi
fi

if [ ! -f "$SSH_KEY_PATH" ]; then
    ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N ""
    echo -e "${GREEN}✅ SSH Key generada${NC}"
fi

echo ""
echo -e "${BLUE}STEP 2: Copia estos SECRETS a GitHub${NC}"
echo "=====================================\n"

echo "📋 Secret #1 - SERVER_HOST"
read -p "Ingresa el dominio/IP del servidor: " SERVER_HOST
echo "Valor: $SERVER_HOST"
echo ""

echo "📋 Secret #2 - SERVER_USER"
read -p "Ingresa el usuario SSH (default: root): " SERVER_USER
SERVER_USER=${SERVER_USER:-root}
echo "Valor: $SERVER_USER"
echo ""

echo "📋 Secret #3 - SERVER_SSH_KEY"
echo "COPIA el siguiente contenido COMPLETO a GitHub:"
echo -e "${YELLOW}------- COPIAR DESDE AQUÍ -------${NC}"
cat "$SSH_KEY_PATH"
echo -e "${YELLOW}------- HASTA AQUÍ -------${NC}"
echo ""

# Paso 3: Instrucciones para agregar clave al servidor
echo -e "${BLUE}STEP 3: Agregar clave pública al servidor${NC}"
echo "==========================================="
echo ""
echo "COPIA el siguiente contenido:"
echo -e "${YELLOW}------- COPIAR DESDE AQUÍ -------${NC}"
cat "$SSH_KEY_PATH.pub"
echo -e "${YELLOW}------- HASTA AQUÍ -------${NC}"
echo ""
echo "Luego en el servidor ejecuta:"
echo ""
echo "mkdir -p ~/.ssh"
echo "chmod 700 ~/.ssh"
echo "cat >> ~/.ssh/authorized_keys << 'EOF'"
echo "# PEGAR AQUÍ"
echo "EOF"
echo "chmod 600 ~/.ssh/authorized_keys"
echo ""

# Verificar SSH connection
echo -e "${BLUE}STEP 4: Verificar conexión SSH${NC}"
echo "=============================="
read -p "¿Probar conexión SSH? (s/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Ss]$ ]]; then
    echo "Intentando SSH a $SERVER_USER@$SERVER_HOST..."
    if ssh -i "$SSH_KEY_PATH" "$SERVER_USER@$SERVER_HOST" "echo 'SSH OK'" &> /dev/null; then
        echo -e "${GREEN}✅ SSH funciona!${NC}"
    else
        echo -e "${YELLOW}⚠️  SSH no funciona aún${NC}"
        echo "Verifica que la clave pública está en ~/.ssh/authorized_keys del servidor"
    fi
fi

echo ""
echo -e "${GREEN}✨ Setup completado!${NC}"
echo ""
echo "Próximos pasos:"
echo "1. Ve a GitHub → Tu Repo → Settings → Secrets"
echo "2. Crea 3 secrets con los valores de arriba"
echo "3. git push origin main"
echo "4. Ver en GitHub → Actions"
echo ""
