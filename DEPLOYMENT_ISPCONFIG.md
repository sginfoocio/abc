# 🚀 Deployment a ISPConfig + Apache2

## Paso 1: Verificar Módulos Apache2 Necesarios

```bash
# Habilitar módulos requeridos
sudo a2enmod proxy
sudo a2enmod proxy_http
sudo a2enmod proxy_wstunnel
sudo a2enmod rewrite
sudo a2enmod headers
sudo a2enmod ssl
sudo a2enmod deflate

# Reiniciar Apache2
sudo systemctl restart apache2

# Verificar que están habilitados
apache2ctl -M | grep proxy
apache2ctl -M | grep rewrite
apache2ctl -M | grep ssl
```

---

## Paso 2: Crear Sitio en ISPConfig

### **Opción A: Usando ISPConfig GUI (Recomendado)**

1. **Acceder a ISPConfig**
   - URL: `https://tu-servidor:8080`
   - Usuario: admin
   - Contraseña: (la tuya)

2. **Crear nuevo sitio web**
   - Menú → **Sitios web** → **Sitios web**
   - Botón "Nuevo sitio web"

3. **Configurar dominio**
   ```
   Nombre de dominio: abcd.cloud.diagonaleyewear.net
   IP: Seleccionar IP del servidor
   Puerto: 443 (SSL) y 80 (redirigir a HTTPS)
   Usuario: usuario_abcd
   Contraseña: generar contraseña fuerte
   ```

4. **Opciones SSL/TLS**
   - ✅ Habilitar SSL
   - ✅ Let's Encrypt
   - Dominio: `abcd.cloud.diagonaleyewear.net`
   - Crear certificado

5. **Configuración Apache**
   - **Directivas personalizadas de Apache2:**
   
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
       RequestHeader set X-Forwarded-Port 443
   </Location>
   
   <Location /_stcore/stream>
       ProxyPass ws://127.0.0.1:8501/_stcore/stream
       ProxyPassReverse ws://127.0.0.1:8501/_stcore/stream
   </Location>
   ```

6. **Guardar**

### **Opción B: Editar manualmente (avanzado)**

```bash
# Acceder por SSH al servidor
ssh root@tu-servidor

# Editar archivo de configuración de ISPConfig
sudo nano /etc/apache2/sites-available/abcd.cloud.diagonaleyewear.net.conf

# Pegar contenido de apache2.conf (de este proyecto)
# Salvar: Ctrl+X → Y → Enter

# Habilitar sitio
sudo a2ensite abcd.cloud.diagonaleyewear.net.conf

# Verificar sintaxis
sudo apache2ctl configtest
# Debe retornar: "Syntax OK"

# Reiniciar Apache
sudo systemctl restart apache2
```

---

## Paso 3: Preparar Servidor (SSH)

```bash
# Conectar por SSH
ssh root@tu-servidor

# Instalar Docker y Docker Compose
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Crear directorio de aplicación
sudo mkdir -p /opt/abcd-control
cd /opt/abcd-control

# Asignar permisos
sudo chown -R $(whoami):$(whoami) /opt/abcd-control
```

---

## Paso 4: Deploy de Aplicación

```bash
# Descargar archivos (desde el servidor)
# Opción 1: Clonar desde Git
git clone <repo-url> .

# Opción 2: Copiar por SCP desde tu máquina
scp -r ~/OneDrive\ -\ Diagonal\ Eyewear/Proyectos/ABC/* root@tu-servidor:/opt/abcd-control/

# Entrar al directorio
cd /opt/abcd-control

# Configurar variables de entorno
cp .env.example .env
nano .env

# Verificar contenido:
cat .env
```

**Contenido esperado de .env:**
```
DB_HOST=10.3.0.13
DB_PORT=5432
DB_NAME=DiagonalDBProd
DB_USER=user_sg_informatica
DB_PASSWORD=xy8fPpxPerETSQ
```

---

## Paso 5: Iniciar Contenedor Docker

```bash
# Construir imagen
docker-compose build

# Verificar build exitoso
docker images | grep abcd-control

# Iniciar contenedor en background
docker-compose up -d

# Esperar 10 segundos y verificar
sleep 10
docker-compose ps

# Debe mostrar:
# NAME           STATUS      PORTS
# abcd-app       Up X secs   8501/tcp
```

---

## Paso 6: Verificar Conectividad

```bash
# Test local en el servidor
curl -I http://localhost:8501
# Debe retornar: HTTP/1.1 200 OK

# Test desde afuera (sustituir IP del servidor)
curl -I https://abcd.cloud.diagonaleyewear.net
# Debe retornar: HTTP/2 200

# Ver logs si hay error
docker-compose logs abcd-app
```

---

## Paso 7: Mantener Actualizado (Automático)

### **Crear script de auto-actualización**

```bash
cat > /opt/abcd-control/update.sh << 'EOF'
#!/bin/bash
cd /opt/abcd-control
echo "Actualizando ABCD Control..."
git pull origin main
docker-compose build --no-cache
docker-compose down
docker-compose up -d
echo "Actualización completada en $(date)" >> /var/log/abcd-update.log
EOF

chmod +x /opt/abcd-control/update.sh

# Programar para ejecutarse semanalmente (lunes a las 3:00 AM)
echo "0 3 * * 1 /opt/abcd-control/update.sh" | sudo crontab -e
```

---

## 🔍 Verificación Post-Deploy

### **Checklist**

```bash
# ✅ Contenedor corriendo
docker-compose ps | grep "Up"

# ✅ Puerto 8501 abierto internamente
docker-compose exec abcd-app curl -I http://localhost:8501

# ✅ BD accesible
docker-compose exec abcd-app psql -h 10.3.0.13 -U user_sg_informatica -d DiagonalDBProd -c "SELECT 1"

# ✅ Apache2 sin errores
sudo apache2ctl configtest
# Debe mostrar: "Syntax OK"

# ✅ Certificado SSL válido
echo | openssl s_client -servername abcd.cloud.diagonaleyewear.net -connect abcd.cloud.diagonaleyewear.net:443 2>/dev/null | openssl x509 -noout -dates

# ✅ DNS resuelve
nslookup abcd.cloud.diagonaleyewear.net
dig abcd.cloud.diagonaleyewear.net +short
```

---

## 📊 Monitoreo Continuo

### **Verificaciones diarias (cron)**

```bash
cat > /opt/abcd-control/monitor.sh << 'EOF'
#!/bin/bash

# Checkear si está corriendo
if ! docker-compose ps | grep -q "abcd-app.*Up"; then
    echo "ALERTA: ABCD Control no está corriendo!" 
    docker-compose up -d
fi

# Checkear health
if ! curl -s http://localhost:8501/_stcore/health > /dev/null; then
    echo "ALERTA: Health check falló!"
    docker-compose restart abcd-app
fi

# Log
echo "[$(date)] Status: OK" >> /var/log/abcd-monitor.log
EOF

chmod +x /opt/abcd-control/monitor.sh

# Ejecutar cada 5 minutos
*/5 * * * * /opt/abcd-control/monitor.sh
```

---

## 🛠️ Comandos ISPConfig + Docker

### **Desde ISPConfig GUI**

- **Para ver logs:** Panel → Sitios web → Abrir log
- **Para editar Apache:** Sitios web → Directivas personalizadas de Apache2
- **Para renovar SSL:** Let's Encrypt automático cada 60 días

### **Desde terminal**

```bash
# Entrar en contenedor para debugging
docker-compose exec abcd-app bash

# Ver logs
docker-compose logs --tail=50 abcd-app

# Reiniciar solo la app (sin rebuilda)
docker-compose restart abcd-app

# Ver consumo de recursos
docker stats abcd-app

# Actualizar a latest (pull + rebuild)
docker-compose pull
docker-compose build
docker-compose up -d
```

---

## 🚨 Troubleshooting ISPConfig + Apache2

### **Error: "ProxyPass no funciona"**
```bash
# Verificar módulos habilitados
sudo a2enmod proxy_http
sudo a2enmod proxy_wstunnel
sudo systemctl restart apache2
```

### **Error: "Port 8501 ya en uso"**
```bash
# Ver qué está usando el puerto
sudo lsof -i :8501

# Liberar puerto
docker-compose down
docker-compose up -d
```

### **Error: "SSL certificate not found"**
```bash
# ISPConfig debe tener la opción "Let's Encrypt" habilitada
# Si falla, crear manualmente:
sudo certbot certonly --standalone -d abcd.cloud.diagonaleyewear.net

# Luego actualizar en ISPConfig → Directivas Apache2 con la ruta correcta
```

### **Error: "WebSocket connection refused"**
```bash
# Necesita WebSocket tunneling en Apache2
sudo a2enmod proxy_wstunnel

# Y en directivas Apache2:
RewriteEngine On
RewriteCond %{HTTP:Upgrade} websocket [NC]
RewriteCond %{HTTP:Connection} upgrade [NC]
RewriteRule ^/(.*)$ "ws://127.0.0.1:8501/$1" [P,L]

# Reiniciar Apache
sudo systemctl restart apache2
```

### **Error: "ISPConfig regenera configuración y pierde cambios"**

ISPConfig puede sobrescribir cambios. Para evitar:

1. Usar **Directivas personalizadas de Apache2** en ISPConfig GUI (recomendado)
2. O editar `/etc/apache2/sites-available/abcd.cloud.diagonaleyewear.net.conf` y proteger:
   ```bash
   # Marcar como no editable por ISPConfig
   sudo chattr +i /etc/apache2/sites-available/abcd.cloud.diagonaleyewear.net.conf
   ```

---

## 🔐 Seguridad

### **Firewall (si está configurado)**
```bash
# Permitir HTTP/HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Bloquear acceso directo a puerto 8501
sudo ufw deny 8501/tcp

# O más restrictivo: solo localhost
sudo ufw allow from 127.0.0.1 to 127.0.0.1 port 8501
```

### **Credenciales .env**
```bash
# Proteger archivo .env
sudo chmod 600 /opt/abcd-control/.env
sudo chown root:root /opt/abcd-control/.env

# No incluir en backups públicos
```

---

## 📈 Performance Tuning

### **Limitar recursos del contenedor**
```yaml
# En docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
```

### **Cache en Apache2**
```apache
<FilesMatch "\.(jpg|css|png|js)$">
    Header set Cache-Control "max-age=604800, public"
</FilesMatch>
```

### **Compresión**
```apache
# Ya incluida en apache2.conf
# Gzip automático para CSS, JS, HTML
```

---

## 📝 Resumen Final

| Paso | Descripción | Tiempo |
|------|-------------|--------|
| 1 | Habilitar módulos Apache2 | 2 min |
| 2 | Crear sitio en ISPConfig | 5 min |
| 3 | Preparar servidor (Docker) | 10 min |
| 4 | Deploy aplicación | 5 min |
| 5 | Verificar conectividad | 2 min |
| **TOTAL** | | **~24 min** |

---

## ✅ Acceso Final

Una vez completado:

```
HTTPS: https://abcd.cloud.diagonaleyewear.net
Inicio: https://abcd.cloud.diagonaleyewear.net
Buscar: https://abcd.cloud.diagonaleyewear.net?page=Buscar%20Producto
Reportes: https://abcd.cloud.diagonaleyewear.net?page=Reportes%20ABCD
```

✨ **¡Listo para usar en producción!**
