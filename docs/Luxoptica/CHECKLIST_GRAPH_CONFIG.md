# Checklist: Configuración Graph API - Luxottica Image Downloader

## ☐ Fase 1: Registro en Azure AD (5 min)

- [ ] Ir a [https://portal.azure.com](https://portal.azure.com)
- [ ] Buscar **Azure Active Directory**
- [ ] Ir a **App registrations** → **New registration**
- [ ] Nombre: `Luxottica Image Downloader`
- [ ] Account type: `Accounts in this organizational directory only`
- [ ] Click **Register**

## ☐ Fase 2: Obtener Credenciales (5 min)

- [ ] Copiar **Directory (tenant) ID** → guardar en `.env` como `M365_TENANT_ID`
- [ ] Copiar **Application (client) ID** → guardar en `.env` como `M365_CLIENT_ID`
- [ ] Ir a **Certificates & secrets**
- [ ] **New client secret**
- [ ] Description: `Luxottica image download`
- [ ] Expires: `24 months`
- [ ] Click **Add**
- [ ] **Copiar el valor** (aparece solo una vez) → guardar en `.env` como `M365_CLIENT_SECRET`

## ☐ Fase 3: Configurar Permisos Graph API (5 min)

- [ ] Ir a **API permissions**
- [ ] **Add a permission**
- [ ] **Microsoft Graph** → **Application permissions**
- [ ] Buscar y agregar `Mail.Read`
- [ ] Click **Add permissions**
- [ ] **Grant admin consent for [organizacion]** → Confirmar

## ☐ Fase 4: Permisos en Mailbox Compartido (5 min)

**Opción A - PowerShell (recomendado):**
```powershell
Connect-ExchangeOnline
Add-MailboxPermission -Identity "images@diagonaleyewear.com" `
  -User "<M365_CLIENT_ID>" `
  -AccessRights FullAccess `
  -InheritanceType All
```

**Opción B - Exchange Admin Center:**
- [ ] Ir a [https://admin.exchange.microsoft.com](https://admin.exchange.microsoft.com)
- [ ] Buscar `images@diagonaleyewear.com`
- [ ] Propiedades → **Manage delegates**
- [ ] Agregar app con Full Access

## ☐ Fase 5: Configurar .env (2 min)

```env
M365_TENANT_ID=<tu-tenant-id>
M365_CLIENT_ID=<tu-app-client-id>
M365_CLIENT_SECRET=<tu-app-client-secret>
M365_MAILBOX=images@diagonaleyewear.com
M365_DOWNLOAD_ROOT=docs/Luxoptica/descargas
```

## ☐ Fase 6: Verificar Configuración (2 min)

```powershell
# Activar venv
& ".\.venv\Scripts\Activate.ps1"

# Probar descarga
python -c "from graph_mail_downloader import download_luxoptica_mail_attachments; s = download_luxoptica_mail_attachments(); print(f'OK: {s.messages_scanned} mensajes, {s.attachments_downloaded} adjuntos')"
```

## ✅ Completado

- [ ] Todas las fases completadas
- [ ] Script funciona sin errores
- [ ] Archivos descargados en `docs/Luxoptica/descargas/`

## 📚 Referencia
Ver [CONFIGURACION_GRAPH_API.md](CONFIGURACION_GRAPH_API.md) para detalles completos.
