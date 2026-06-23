# 🚀 QUICK START - Deployment ISPConfig

> **Tienes ISPConfig** → Esta es tu guía

## 5 Pasos para Producción

### ✅ Paso 1: Verificar módulos Apache2 (5 min)

```bash
# SSH al servidor
ssh root@tu-servidor

# Habilitar módulos
sudo a2enmod proxy proxy_http proxy_wstunnel rewrite headers ssl

# Verificar
sudo apache2ctl -M | grep proxy
sudo systemctl restart apache2
```

---

### ✅ Paso 2: Crear sitio en ISPConfig GUI (10 min)

1. **Acceder:** https://tu-servidor:8080
2. **Ir a:** Sitios web → Nuevo sitio web
3. **Configurar:**
   - Dominio: `abcd.cloud.diagonaleyewear.net`
   - SSL: Let's Encrypt ✅
   - Usuario: `usuario_abcd`

4. **Directivas Apache2 personalizadas:**

```apache
ProxyPreserveHost On
ProxyRequests Off

RewriteEngine On
RewriteCond %{HTTP:Upgrade} websocket [NC]
RewriteCond %{HTTP:Connection} upgrade [NC]
RewriteRule ^/(.*)$ "ws://127.0.0.1:8501/$1" [P,L]

<Location />
    ProxyPass http://127.0.0.1:8501/ timeout=3600
    ProxyPassReverse http://127.0.0.1:8501/
    ProxyAddHeaders On
    RequestHeader set X-Real-IP %{REMOTE_ADDR}s
    RequestHeader set X-Forwarded-For %{REMOTE_ADDR}s
    RequestHeader set X-Forwarded-Proto https
    RequestHeader set X-Forwarded-Host %{HTTP_HOST}s
</Location>

<Location /_stcore/stream>
    ProxyPass ws://127.0.0.1:8501/_stcore/stream
    ProxyPassReverse ws://127.0.0.1:8501/_stcore/stream
</Location>
```

5. **Guardar**

---

### ✅ Paso 3: Deploy Docker (10 min)

```bash
# SSH al servidor
ssh root@tu-servidor

# Crear directorio
mkdir -p /opt/abcd-control
cd /opt/abcd-control

# Descargar archivos (opción 1: Git)
git clone <repo-url> .

# O (opción 2: SCP desde tu máquina)
scp -r ~/OneDrive\ -\ Diagonal\ Eyewear/Proyectos/ABC/* root@tu-servidor:/opt/abcd-control/

# Ir al directorio
cd /opt/abcd-control

# Crear .env
cp .env.example .env

# Editar con credenciales reales
nano .env

# Verificar:
# DB_HOST=10.3.0.13
# DB_PORT=5432
# DB_NAME=DiagonalDBProd
# DB_USER=user_sg_informatica
# DB_PASSWORD=xy8fPpxPerETSQ
```

---

### ✅ Paso 4: Iniciar Contenedor (5 min)

```bash
# Construir y iniciar
docker-compose build
docker-compose up -d

# Verificar
docker-compose ps

# Ver logs si hay error
docker-compose logs abcd-app
```

---

### ✅ Paso 5: Verificar Acceso (5 min)

```bash
# Desde el servidor
curl -I http://localhost:8501
# Debe retornar: HTTP/1.1 200 OK

# Desde afuera (en tu navegador)
# https://abcd.cloud.diagonaleyewear.net
```

---

## 🎯 Resultado Final

```
✅ https://abcd.cloud.diagonaleyewear.net
✅ Certificado SSL válido (Let's Encrypt)
✅ Apache2 + Docker
✅ Actualizaciones automáticas
✅ Monitoreo
```

---

## 📋 Resumen Visual

```
Tu Servidor ISPConfig
│
├─ Apache2 (puerto 80/443)
│  └─ Directivas proxy → 127.0.0.1:8501
│
└─ Docker (puerto 8501)
   └─ Contenedor abcd-app
      ├─ Streamlit
      ├─ Python
      └─ Conexión PostgreSQL
```

---

## 🔧 Comandos Útiles (Después del deploy)

```bash
# Ver estado
docker-compose ps

# Ver logs
docker-compose logs -f abcd-app --tail=50

# Actualizar versión
cd /opt/abcd-control
git pull origin main
docker-compose build
docker-compose up -d

# Detener
docker-compose down

# Reiniciar
docker-compose restart abcd-app
```

---

## ⚠️ Troubleshooting Rápido

| Error | Solución |
|-------|----------|
| "Connection refused" | `docker-compose restart abcd-app` |
| "Database connection error" | Verificar `.env` - DB_HOST correcto |
| "Port already in use" | `docker-compose down && docker-compose up -d` |
| "SSL not working" | Esperar 5 min a que Let's Encrypt se propague, revisar ISPConfig |

---

## 📖 Para Más Detalles

- **Guía completa:** [DEPLOYMENT_ISPCONFIG.md](DEPLOYMENT_ISPCONFIG.md)
- **Docker info:** [DOCKER_README.md](DOCKER_README.md)
- **App info:** [GUIA_APP_ENHANCED.md](GUIA_APP_ENHANCED.md)

---

## ✨ ¡Listo! 

Accede a:
```
https://abcd.cloud.diagonaleyewear.net
```

Cualquier duda, revisa los logs:
```bash
docker-compose logs abcd-app
```
