# 🚀 GitHub Actions - Auto Deploy (3 Pasos)

## ✨ Qué Logras

```
git push → GitHub Actions → SSH al servidor → Docker rebuild → ✅ Live
```

Cada vez que hagas push a `main`, tu app se actualiza automáticamente en producción.

---

## PASO 1️⃣: Generar SSH Key en el Servidor (5 min)

**En el servidor SSH:**

```bash
# Conectar al servidor
ssh -p 2224 rubensg@188.227.145.193

# Generar clave EN el servidor
ssh-keygen -t ed25519 -f ~/.ssh/abcd_deploy -N ""

# Ver clave PRIVADA (COPIAR para GitHub Secrets)
cat ~/.ssh/abcd_deploy

# Ver clave PÚBLICA (quedará en authorized_keys)
cat ~/.ssh/abcd_deploy.pub
```

**Copiar el contenido de la clave PRIVADA** (todo desde `-----BEGIN OPENSSH PRIVATE KEY-----` hasta `-----END OPENSSH PRIVATE KEY-----`) para usarlo en GitHub Secrets.

---

## PASO 2️⃣: Configurar GitHub Secrets (5 min)

1. **Ve a GitHub → Tu Repo → Settings → Secrets and variables → Actions**

2. **Crea 4 secrets:**

### Secret 1️⃣
```
Name: SERVER_HOST
Value: cloud.diagonaleyewear.net
(o tu IP del servidor)
```

### Secret 2️⃣
```
Name: SERVER_USER
Value: rubensg
(o tu usuario SSH)
```

### Secret 3️⃣
```
Name: SERVER_PORT
Value: 2224
```

### Secret 4️⃣ (LA MÁS IMPORTANTE)
```
Name: SERVER_SSH_KEY
Value: [COPIAR AQUÍ contenido completo de ~/.ssh/abcd_deploy]
```

**Ejemplo de cómo se ve:**
```
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUtbm9uZS1ub25lAAAAAAAAADIAAAAjZWNkc2E...
... [muchas líneas]
-----END OPENSSH PRIVATE KEY-----
```

---

## PASO 3️⃣: Autorizar Clave en Servidor (2 min)

**En el servidor (ya debes estar conectado vía SSH):**

```bash
# Agregar clave pública a authorized_keys
mkdir -p ~/.ssh
chmod 700 ~/.ssh

cat ~/.ssh/abcd_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Verificar que se agregó
cat ~/.ssh/authorized_keys
```

**Verificar que funciona desde tu máquina local:**

```powershell
# En PowerShell
ssh -p 2224 -i C:\Users\Ruben\.ssh\abcd_deploy rubensg@188.227.145.193

# Si se conecta sin pedir password, ¡está bien!
```

---

## 🎯 Listo! Ahora Usa

### Primero: Push a GitHub

```bash
cd "c:\Users\Ruben\OneDrive - Diagonal Eyewear\Proyectos\ABC"

git add .
git commit -m "Initial ABCD Control"
git push -u origin main
```

### Luego: Ver Deploy Automático

1. **Ve a GitHub → Actions**
2. **Verás "Deploy to Production" corriendo**
3. **Espera 2-3 minutos**
4. **✅ Si es verde = Deploy exitoso**

---

## 🔄 Flujo Normal (Después)

### Cada cambio que hagas:

```bash
# Hacer cambio
nano app_enhanced.py

# Commitear
git add .
git commit -m "Fix: bug en búsqueda"

# Push (¡AUTO-DEPLOY!)
git push
```

### Resultado:
- GitHub Actions inicia automáticamente
- Testa el código
- Builds Docker image
- SSH al servidor
- Ejecuta `docker-compose build && docker-compose up -d`
- Health check
- ✅ App actualizada

**Tiempo total:** 2-3 minutos

---

## 📊 Ver Status del Deploy

### En GitHub:
```
Repo → Actions → Deploy to Production
├─ build-and-push: ✅ Completado (2 min)
└─ Step "Deploy to Server": ✅ Completado (1 min)
```

### En el Servidor:
```bash
# Ver logs del deploy
tail -f /var/log/abcd-deploy.log

# Ver si Docker corriendo
docker-compose ps

# Ver logs de la app
docker-compose logs -f abcd-app --tail=20
```

---

## ✅ Checklist

- [ ] SSH al servidor: `ssh -p 2224 rubensg@188.227.145.193`
- [ ] Clave SSH generada EN el servidor (`~/.ssh/abcd_deploy`)
- [ ] Clave pública agregada a `~/.ssh/authorized_keys` en servidor
- [ ] Clave privada copiada a GitHub Secret: `SERVER_SSH_KEY`
- [ ] 3 secrets más configurados (SERVER_HOST, SERVER_USER, SERVER_PORT)
- [ ] SSH test funciona: `ssh -p 2224 -i ~/.ssh/abcd_deploy rubensg@servidor`
- [ ] Primera vez push a GitHub
- [ ] GitHub Actions corrió exitosamente
- [ ] App actualizada en `https://abcd.cloud.diagonaleyewear.net`

---

## 🆘 Si Algo Falla

### "Deploy action failed"
```bash
# Ver los logs en GitHub
GitHub → Actions → Workflow Run → Logs
Busca "Deploy to Server" step
```

### "SSH connection refused"
```bash
# Verificar en servidor
cat ~/.ssh/authorized_keys | grep "abcd_deploy"
# Debe mostrar: ssh-ed25519 AAAA...

# Verificar SSH funciona manualmente
ssh -p 2224 -i ~/.ssh/abcd_deploy rubensg@tu-servidor
```

### "Docker not found"
```bash
# En servidor
docker --version
docker-compose --version
# Si falta, instalar primero
```

### "Port 8501 already in use"
```bash
# En servidor
cd /opt/abcd-control
docker-compose down
docker-compose up -d
```

---

## 🔐 Seguridad

✅ **Clave PRIVADA:** Solo en GitHub Secrets (encriptada)
✅ **Clave PÚBLICA:** Solo en servidor
✅ **Nunca** subir clave privada a código
✅ **.gitignore** protege .env con credenciales

---

## 📱 Ejemplo Real

### Scenario: Fix un bug

**1. En tu máquina:**
```bash
git pull origin main
nano app_enhanced.py
# Arreglar bug en búsqueda
git add app_enhanced.py
git commit -m "Fix: búsqueda case-insensitive"
git push
```

**2. GitHub Actions:**
- ✅ Tests pasan
- ✅ Docker image builds
- ✅ SSH deploy al servidor

**3. En 2-3 minutos:**
- ✅ https://abcd.cloud.diagonaleyewear.net funciona con fix
- ✅ Logs limpios
- ✅ Sin downtime

---

## 🎉 Ventajas

✅ **Automático:** Sin comandos manuales
✅ **Fast:** 2-3 minutos de push a producción
✅ **Safe:** Tests antes de deploy
✅ **Visible:** Ver status en GitHub UI
✅ **Auditable:** Historial de todos los deploys
✅ **Reversible:** `git revert` si algo falla

---

## 📚 Archivos Asociados

- `.github/workflows/deploy.yml` - Workflow CI/CD
- `scripts/deploy.sh` - Script que corre en servidor
- `GITHUB_SETUP.md` - Setup detallado
- `GITHUB_DEPLOY.md` - Guía completa

---

## 🚀 ¡Listo!

```
1. ✅ SSH Key generada
2. ✅ GitHub Secrets configurados
3. ✅ Servidor autorizado
4. ✅ Push a GitHub
5. ✅ Auto-deploy!
```

Cada `git push` ahora actualiza automáticamente tu app en `cloud.diagonaleyewear.net` ✨
