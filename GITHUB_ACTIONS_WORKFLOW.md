# 📊 GitHub Actions - Visual Workflow

## Diagrama del Flujo

```
┌─────────────────────────────────────────────────────────────────┐
│ TU MÁQUINA                                                      │
│                                                                 │
│  nano app_enhanced.py  (arreglar bug)                          │
│         ↓                                                       │
│  git add .                                                      │
│         ↓                                                       │
│  git commit -m "Fix: bug"                                      │
│         ↓                                                       │
│  git push origin main  ← 🚀 INICIA AUTO-DEPLOY                │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ GITHUB ACTIONS (2-3 minutos)                                   │
│                                                                 │
│  🔍 Checkout código                                            │
│  🧪 Tests Python                                               │
│     - flake8 (linting)                                         │
│     - Syntax check                                             │
│  🐳 Build Docker                                               │
│     - Multi-stage build                                        │
│     - Push a registry                                          │
│  🔐 SSH Deploy                                                 │
│     ├─ git pull origin main                                    │
│     ├─ docker-compose build                                    │
│     ├─ docker-compose down                                     │
│     ├─ docker-compose up -d                                    │
│     └─ Health check ✅                                         │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ TU SERVIDOR (Cloud)                                            │
│                                                                 │
│  /opt/abcd-control/                                            │
│      ├─ app_enhanced.py (ACTUALIZADO)                         │
│      ├─ db_loader.py                                           │
│      ├─ engine.py                                              │
│      └─ .env (credenciales seguras)                           │
│           ↓                                                    │
│      Docker Container (8501)                                  │
│      Apache2 Reverse Proxy (443 → 8501)                       │
│           ↓                                                    │
│  https://abcd.cloud.diagonaleyewear.net ✅                   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Eventos Trigger

```
Event: push a rama main
    ↓
Workflow: Deploy to Production
    ├─ Job: build-and-push
    │   ├─ if: github.event_name == 'push'
    │   └─ Construir image + Push a registry
    │
    └─ Job: tests
        ├─ if: github.event_name == 'pull_request'
        └─ Lint + Type checks

Event: push a rama feature/*
    ↓
Workflow: Tests solo (sin deploy)
    └─ Lint + Type checks
```

---

## Status Check

### Verde ✅ = Todo OK
```
✅ build-and-push
   ├─ Checkout code
   ├─ Build Docker
   ├─ Deploy to Server
   └─ Health check

✅ tests (solo en PR)
   ├─ Lint
   └─ Syntax check
```

### Rojo ❌ = Error
```
❌ build-and-push
   └─ Deploy to Server (FAILED)
      Error: SSH connection refused
      
Revisar:
- GitHub Secrets configurados?
- Clave SSH válida?
- Servidor accesible?
```

---

## En GitHub UI

```
Tu Repo → Actions → Deploy to Production
│
└─ Latest Runs
   ├─ Run #5: ✅ Passed (2 min)
   │   └─ Triggered by: john@example.com
   │       "Fix: búsqueda case-insensitive"
   │
   ├─ Run #4: ✅ Passed (2 min)
   │   └─ Triggered by: jane@example.com
   │       "Feature: nueva búsqueda"
   │
   └─ Run #3: ❌ Failed (1 min)
       └─ Error: docker-compose not found
```

---

## Logs Detallados

### Click en Run → Expandir "Deploy to Server"

```
Run # Timestamp              Status  Duration
───────────────────────────────────────────────
Step: Checkout code         ✅      0.5s
Step: Set up Docker         ✅      1.2s
Step: Log in to Registry    ✅      0.3s
Step: Build and push        ✅      45s
Step: Deploy to Server      ✅      30s
  - Connecting via SSH...
  - Pulling from git...
  - Building Docker image...
  - Stopping old container...
  - Starting new container...
  - Health check...
  ✅ All checks passed
Step: Logs                  ✅      2s
  [Últimas 20 líneas del container]
```

---

## Timeline Real

```
14:32:10  🚀 git push origin main
14:32:15  GitHub detecta push
14:32:20  ✅ Checkout code
14:32:30  ✅ Build Docker (paralelo)
14:32:45  ✅ Tests passed
14:33:00  🔐 SSH al servidor
14:33:05  ✅ git pull exitoso
14:33:15  ✅ docker build completo
14:33:45  ✅ Container iniciado
14:33:50  ✅ Health check OK
14:33:55  🎉 DEPLOY EXITOSO (3 min 45 seg)

Tu app actualizada en:
https://abcd.cloud.diagonaleyewear.net
```

---

## Cambios Entre Versiones

```
Antes (Deploy Manual):
├─ SSH al servidor manualmente
├─ cd /opt/abcd-control
├─ git pull
├─ docker build
├─ docker restart
└─ Esperar 5-10 min
Tiempo: 10-15 minutos
Errores: Manuales

Después (GitHub Actions):
├─ git push
├─ GitHub Actions automático
│  ├─ Tests
│  ├─ Build
│  ├─ Deploy
│  └─ Health check
└─ ¡Listo!
Tiempo: 2-3 minutos
Errores: Evitados con tests
```

---

## Branches y Workflows

### Branch: main
```
push → Tests + Build + Deploy a PRODUCCIÓN
```

### Branch: feature/nueva-feature
```
push → Solo Tests (sin deploy)
↓
Pull Request
↓
Review
↓
Merge a main
↓
Auto-Deploy a PRODUCCIÓN
```

---

## Secrets Flujo

```
GitHub Secrets (Encriptados)
├─ SERVER_HOST: cloud.diagonaleyewear.net
├─ SERVER_USER: root
└─ SERVER_SSH_KEY: -----BEGIN...-----END-----

        ↓ (En GitHub Actions)

Ambiente de Job (Temporalmente)
├─ ${{ secrets.SERVER_HOST }}
├─ ${{ secrets.SERVER_USER }}
└─ ${{ secrets.SERVER_SSH_KEY }}

        ↓ (SSH al servidor)

SSH Connection
├─ Host: cloud.diagonaleyewear.net
├─ User: root
└─ Key: SERVER_SSH_KEY

        ↓ (Deploy)

Tu servidor
└─ Ejecuta deploy.sh
```

---

## Notificaciones

```
Si el deploy falla:
├─ GitHub marca workflow como ❌ FAILED
├─ Puedes recibir email (si habilitado)
└─ Ver logs en GitHub Actions UI

Si el deploy exitoso:
├─ GitHub marca workflow como ✅ PASSED
├─ Logs disponibles en Actions
└─ Tu app está actualizada
```

---

## Rollback Rápido

```
Si algo falla después del deploy:

git revert HEAD
git push

✅ GitHub Actions reinicia
❌ Vuelve a la versión anterior
✅ Deploy en 2-3 minutos

Listo! Versión anterior en producción
```

---

## Almacenamiento de Logs

```
GitHub
├─ Logs públicos del workflow
├─ Disponibles por 90 días
└─ Ver en Actions UI

Servidor
├─ /var/log/abcd-deploy.log
├─ Logs locales permanentes
└─ Ver: tail -f /var/log/abcd-deploy.log
```

---

## Integración con Slack (Opcional)

```
GitHub Actions
    ↓
Si success:
    └─ Envía a Slack: "✅ Deploy exitoso"
    
Si fail:
    └─ Envía a Slack: "❌ Deploy falló - ver logs"

Necesita:
- Webhook de Slack configurado
- SLACK_WEBHOOK en GitHub Secrets
```

---

## Seguridad

```
✅ Clave SSH privada
   ├─ Almacenada en GitHub Secrets (encriptada)
   ├─ Nunca visible en logs
   └─ Descartada después del deploy

✅ Credenciales .env
   ├─ No en repositorio
   ├─ En servidor (.env real)
   └─ GitHub Actions no toca .env

✅ Acceso limitado
   ├─ SSH solo desde GitHub
   ├─ Solo a usuario configurado
   └─ Solo ejecutar scripts permitidos
```

---

## Monitoreo Post-Deploy

```
Después de cada deploy:

✅ Health check automático
   └─ curl http://localhost:8501/_stcore/health

✅ Logs capturados
   └─ Últimas 20 líneas en GitHub Actions

✅ App verificada
   └─ https://abcd.cloud.diagonaleyewear.net

Si algo falla:
├─ Ver logs en GitHub Actions
├─ SSH al servidor: tail -f /var/log/abcd-deploy.log
└─ Investigar error
```

---

## Estadísticas de Deploy

```
Después de 1 mes de uso:

Deploys: 30+
Éxito: 29 ✅
Fallo: 1 ❌ (git merge conflict)

Tiempo promedio: 2m 45s
Rango: 2m 30s - 3m 20s

Errores más comunes:
├─ Typo en código (tests lo atrapan)
├─ BD no accesible (health check lo detecta)
└─ Port en uso (restart lo soluciona)
```

---

Este es el flujo completo de GitHub Actions con auto-deploy. ¡Automatizado, rápido y seguro! 🚀
