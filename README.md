# 📦 ABCD Control - Sistema de Clasificación de Productos

> Aplicación web Streamlit para gestionar la clasificación ABCD de 13,454 productos con análisis de demanda, períodos de agotamiento y optimización de inventario.

## 🎯 ¿Qué Es?

Sistema inteligente que clasifica productos en 4 categorías:
- **A**: Alta demanda → REPONER
- **B**: Demanda media → MANTENER  
- **C**: 60+ días sin ventas → REVISAR
- **D**: 120+ días sin ventas → LIQUIDAR

Con análisis único de **ventanas de agotamiento** que entiende la diferencia entre "sin demanda" vs "sin stock".

---

## 🚀 Cómo Desplegar (Elige tu caso)

### 📍 **OPCIÓN 1: Tienes ISPConfig** ⭐ (Recomendado)

**Tiempo:** ~35 minutos

```bash
# Ver guía rápida:
cat QUICKSTART_ISPCONFIG.md

# O guía detallada:
cat DEPLOYMENT_ISPCONFIG.md
```

**Ventajas:**
- ✅ Gestión visual desde ISPConfig
- ✅ SSL automático
- ✅ Fácil de mantener
- ✅ Integrado con tus otros sitios

---

### 📍 **OPCIÓN 2: Servidor Linux sin ISPConfig**

**Tiempo:** ~40 minutos

```bash
# Con Apache2:
cat DEPLOYMENT_GUIDE.md   # Busca "Opción 1"

# O con Nginx:
cat DEPLOYMENT_GUIDE.md   # Busca "Opción 2"
```

**Ventajas:**
- Máximo control
- Fácil de automatizar

---

### 📍 **OPCIÓN 3: Heroku (Quick & Easy)**

**Tiempo:** ~10 minutos

```bash
cat DEPLOYMENT_GUIDE.md   # Busca "Opción 3"
```

**Ventajas:**
- Rápido de desplegar
- No requiere servidor propio

**Desventajas:**
- Costo variable
- Limitaciones de recursos

---

### 📍 **OPCIÓN 4: AWS/Google Cloud (Enterprise)**

```bash
cat DEPLOYMENT_GUIDE.md   # Busca "Opción 4"
```

---

## 📚 Documentación

| Archivo | Para Qué |
|---------|----------|
| **QUICKSTART_ISPCONFIG.md** | ⭐ Los 5 pasos rápidos (ISPConfig) |
| **DEPLOYMENT_ISPCONFIG.md** | Guía completa + troubleshooting (ISPConfig) |
| **DEPLOYMENT_GUIDE.md** | Todas las opciones de deployment |
| **DOCKER_README.md** | Explicación técnica de Docker |
| **GUIA_APP_ENHANCED.md** | Cómo usar la aplicación |
| **nginx.conf** | Configuración Nginx (si no usas Apache2) |
| **apache2.conf** | Configuración Apache2 |
| **docker-compose.yml** | Configuración Docker |
| **Dockerfile** | Definición de imagen Docker |

---

## 📦 Qué Incluye

```
.
├── Aplicación Streamlit
│   ├── app_enhanced.py          ← La app web
│   ├── db_loader.py             ← Carga de datos
│   ├── engine.py                ← Lógica ABCD
│   └── db_config.py             ← Config BD
│
├── Docker (Deployment)
│   ├── Dockerfile               ← Imagen Docker
│   ├── docker-compose.yml       ← Orquestación
│   ├── docker-compose.prod.yml  ← Producción
│   ├── .dockerignore            ← Exclusiones
│   └── start.sh                 ← Script inicio
│
├── Web Server (Proxy)
│   ├── apache2.conf             ← Apache2 config
│   ├── nginx.conf               ← Nginx config (alternativa)
│   └── DEPLOYMENT_ISPCONFIG.md  ← Con ISPConfig
│
└── Documentación
    ├── QUICKSTART_ISPCONFIG.md  ← Los 5 pasos
    ├── DEPLOYMENT_ISPCONFIG.md  ← Guía completa
    ├── DEPLOYMENT_GUIDE.md      ← Todas opciones
    ├── DOCKER_README.md         ← Técnico
    ├── GUIA_APP_ENHANCED.md     ← Uso app
    └── README.md                ← Este archivo
```

---

## 🎬 Demostración Local (Antes de Desplegar)

**Si quieres probar antes en tu máquina:**

```bash
# Entrar en directorio
cd "c:\Users\Ruben\OneDrive - Diagonal Eyewear\Proyectos\ABC"

# Opción A: Streamlit directo (desarrollo)
streamlit run app_enhanced.py

# Opción B: Docker local
docker-compose up -d
# Acceder a: http://localhost:8501
```

---

## 🔐 Seguridad

✅ SSL/TLS (Let's Encrypt)
✅ Headers de seguridad HTTP
✅ Rate limiting
✅ Compresión Gzip
✅ Sin credenciales en código
✅ Variables de entorno (.env)

---

## 📊 Características de la App

### Dashboard
- KPIs principales (productos, capital, categorías)
- Gráficos interactivos
- Tabla resumen

### Búsqueda
- Por nombre, código barras o ID
- Detalle completo del producto
- Períodos de agotamiento
- Histórico de ventas

### Reportes
- Filtros por ABCD, capital, etc.
- Gráficos de distribución
- Exportar a CSV

### Análisis Detallado
- Histórico completo
- Período de agotamiento
- Motivo de clasificación
- Acción recomendada

---

## 🛠️ Stack Técnico

- **Frontend:** Streamlit + Plotly (gráficos)
- **Backend:** Python + Pandas + SQLAlchemy
- **BD:** PostgreSQL (Odoo)
- **Deployment:** Docker + Docker Compose
- **Web Server:** Apache2 (ISPConfig) o Nginx
- **SSL:** Let's Encrypt

---

## 📞 Soporte

### Antes del Deploy
- Revisa la guía específica para tu caso
- Verifica que tienes acceso SSH al servidor
- Asegúrate de tener Docker instalado

### Después del Deploy
- Revisa logs: `docker-compose logs abcd-app`
- Checkea que BD es accesible: `psql -h 10.3.0.13 -U user_sg_informatica -d DiagonalDBProd`
- Prueba HTTPS: `curl -I https://abcd.cloud.diagonaleyewear.net`

### Errores Comunes

| Error | Causa | Solución |
|-------|-------|----------|
| "Connection refused" | BD no accesible | Verificar IP/credenciales en .env |
| "Port 8501 in use" | Otro proceso usa puerto | `docker-compose down` y reiniciar |
| "SSL not trusted" | Certificado pendiente | Esperar 5 min, limpiar cache navegador |
| "Proxy error" | Apache2/Nginx no configurado | Ver DEPLOYMENT_ISPCONFIG.md paso 2 |

---

## 🚦 Siguientes Pasos

1. ✅ **Hoy:** Elige tu opción de deployment (ISPConfig/Linux/Heroku)
2. ⏳ **Mañana:** Deploy usando la guía correspondiente
3. 🧪 **Después:** Testear en producción
4. 📚 **Capacitar:** Mostrar a equipo cómo usar la app

---

## 📈 Roadmap Futuro

- ⏳ Dashboard con tendencias
- ⏳ Alertas automáticas de reposición
- ⏳ Predicción de demanda (ML)
- ⏳ Multi-usuario con permisos
- ⏳ Integración directa con Odoo
- ⏳ Reportes exportables (PDF)

---

## ✨ Comandos Rápidos

### Desarrollo Local
```bash
streamlit run app_enhanced.py
```

### Docker Local
```bash
docker-compose up -d
curl http://localhost:8501
docker-compose logs -f
docker-compose down
```

### Servidor (SSH)
```bash
# Ver estado
docker-compose ps

# Ver logs
docker-compose logs -f abcd-app --tail=50

# Reiniciar
docker-compose restart abcd-app

# Actualizar
cd /opt/abcd-control
git pull
docker-compose build
docker-compose up -d
```

---

## 📄 Versión

**v1.0** - Lanzamiento inicial
- ✅ Clasificación ABCD mejorada
- ✅ Análisis de agotamientos
- ✅ Dashboard web
- ✅ Reportes + exportación
- ✅ Docker ready

---

## 👤 Autor

Sistema desarrollado para **Diagonal Eyewear** - Gestión inteligente de inventario

---

**¿Listo para desplegar?** → Lee la guía para tu caso en la sección "Cómo Desplegar"

```
ISPConfig → QUICKSTART_ISPCONFIG.md
Linux sin ISPConfig → DEPLOYMENT_GUIDE.md (Opción 1 o 2)
Heroku → DEPLOYMENT_GUIDE.md (Opción 3)
AWS/Cloud → DEPLOYMENT_GUIDE.md (Opción 4)
```
