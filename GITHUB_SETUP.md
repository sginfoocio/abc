# Configuración de Repositorio GitHub

## Step 1: Crear GitHub Repository

1. Ve a [github.com/new](https://github.com/new)
2. Crea un repo con nombre: `abcd-control`
3. Privado recomendado (contiene credenciales)
4. **NO** inicialices con README (ya tienes uno)

---

## Step 2: Configurar Secrets

### En GitHub:

1. **Ir a:** Repo → Settings → Secrets and variables → Actions
2. **Crear estos 4 secrets:**

```
Name: SERVER_HOST
Value: cloud.diagonaleyewear.net
(o la IP de tu servidor)
```

```
Name: SERVER_USER
Value: rubensg
(o el usuario SSH de tu servidor)
```

```
Name: SERVER_PORT
Value: 2224
```

```
Name: SERVER_SSH_KEY
Value: [contenido de tu clave privada SSH]
```

### Generar Clave SSH:

```bash
# En tu máquina Windows/Mac/Linux
ssh-keygen -t ed25519 -f abcd_deploy -N ""

# Ver la clave privada (para GitHub Secret)
cat abcd_deploy

# Ver la clave pública (para servidor)
cat abcd_deploy.pub
```

### Agregar clave al servidor:

```bash
# SSH al servidor
ssh -p 2224 rubensg@cloud.diagonaleyewear.net

# Agregar clave pública
mkdir -p ~/.ssh
chmod 700 ~/.ssh
cat >> ~/.ssh/authorized_keys << 'EOF'
[Pegar aquí contenido de abcd_deploy.pub]
EOF
chmod 600 ~/.ssh/authorized_keys
```

---

## Step 3: Subir Código

```bash
# En tu directorio del proyecto
cd "c:\Users\Ruben\OneDrive - Diagonal Eyewear\Proyectos\ABC"

# Inicializar Git
git init
git add .
git commit -m "Initial commit: ABCD Control"

# Agregar remote (reemplaza USERNAME)
git remote add origin https://github.com/USERNAME/abcd-control.git

# Subir a GitHub
git branch -M main
git push -u origin main
```

---

## Step 4: Verificar Deploy

1. Ve a tu repo en GitHub
2. Click en "Actions"
3. Deberías ver "Deploy to Production" corriendo
4. Espera 3-5 minutos
5. Si es verde (✅), tu app está actualizada

---

## Configuraciones Recomendadas

### Branch Protection (Opcional pero Recomendado)

1. **Repo → Settings → Branches**
2. **Add rule**
   - Pattern: `main`
   - ✅ Require a pull request before merging
   - ✅ Require status checks to pass
   - ✅ Dismiss stale pull request approvals when new commits are pushed
   - ✅ Require branches to be up to date before merging

---

## Colaboradores

Si otros van a trabajar en el código:

1. **Repo → Settings → Collaborators**
2. Invita a otros usuarios
3. ⚠️ **NO compartas Secrets con ellos** (GitHub maneja eso)

---

## Archivo de Configuración del Repo

El archivo `.github/workflows/deploy.yml` ya está configurado para:

✅ Build Docker automático
✅ Tests en pull requests
✅ Deploy en push a main
✅ Health checks post-deploy
✅ Linting de Python

---

## Workflow (Después del Setup)

```
1. Local: git push
   ↓
2. GitHub: Tests & Build
   ↓
3. SSH Deploy: Servidor actualiza
   ↓
4. ✅ Producción actualizada
```

---

## Comandos Git Comunes

```bash
# Crear rama feature
git checkout -b feature/nueva-feature

# Hacer cambios
nano archivo.py
git add .
git commit -m "Add: nueva funcionalidad"

# Subir rama
git push origin feature/nueva-feature

# En GitHub: Abrir Pull Request
# → Reviews/Tests/Merge → Auto-deploy a main

# Actualizar desde main
git pull origin main
```

---

## Troubleshooting

### "Permission denied (publickey)"

```bash
# Verificar que key está en servidor
ssh -p 2224 -i abcd_deploy rubensg@cloud.diagonaleyewear.net

# Si no funciona, reinstalar
ssh-copy-id -i abcd_deploy.pub -p 2224 rubensg@cloud.diagonaleyewear.net
```

### GitHub Actions falla

1. Ve a Actions → Ver el workflow fallido
2. Expande "SSH Deploy" step
3. Lee los errores
4. Verifica que Secrets están configurados correctamente

### "Deploy script not found"

```bash
# En el servidor
chmod +x /opt/abcd-control/scripts/deploy.sh
```

---

## 🎯 Próximos Pasos

1. ✅ Crear repo en GitHub
2. ✅ Configurar Secrets
3. ✅ Push código
4. ✅ Esperar a que GitHub Actions depliegue
5. ✅ Acceder a: https://abcd.cloud.diagonaleyewear.net
6. 🎉 ¡Auto-deploy listo!

---

## 📚 Ver Más

- [GITHUB_DEPLOY.md](GITHUB_DEPLOY.md) - Guía completa
- [.github/workflows/deploy.yml](.github/workflows/deploy.yml) - Workflow config
- [scripts/deploy.sh](scripts/deploy.sh) - Script de deploy
