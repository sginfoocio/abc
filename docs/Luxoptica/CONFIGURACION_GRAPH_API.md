# Configuración Microsoft Graph API - Descarga Automática de Imágenes

## Objetivo
Registrar una aplicación en Azure AD para leer correos del mailbox compartido `images@diagonaleyewear.com` y descargar automáticamente los adjuntos de Luxoptica.

## Credenciales Necesarias
```env
M365_TENANT_ID=<tenant-id>
M365_CLIENT_ID=<app-id>
M365_CLIENT_SECRET=<app-secret>
M365_MAILBOX=images@diagonaleyewear.com
M365_DOWNLOAD_ROOT=docs/Luxoptica/descargas
```

---

## Paso 1: Acceder a Azure Portal

1. Ir a [https://portal.azure.com](https://portal.azure.com)
2. Iniciar sesión con cuenta de administrador de Diagonal Eyewear
3. Buscar **Azure Active Directory** (o **Entra ID**)

---

## Paso 2: Registrar Nueva Aplicación

1. En Azure AD, ir a **App registrations**
2. Click en **+ New registration**
3. Completar:
   - **Name**: `Luxottica Image Downloader` (o similar)
   - **Supported account types**: `Accounts in this organizational directory only`
   - **Redirect URI**: Dejar vacío (backend app, sin UI interactiva)
4. Click **Register**

---

## Paso 3: Obtener Credenciales

### 3a. Tenant ID
1. En la app registrada, copiar **Directory (tenant) ID**
2. Guardar en `.env`:
   ```env
   M365_TENANT_ID=<valor-copiado>
   ```

### 3b. Client ID
1. En la misma página, copiar **Application (client) ID**
2. Guardar en `.env`:
   ```env
   M365_CLIENT_ID=<valor-copiado>
   ```

### 3c. Client Secret
1. Ir a **Certificates & secrets**
2. Click **+ New client secret**
3. Completar:
   - **Description**: `Luxottica image download`
   - **Expires**: `24 months` (o según política de seguridad)
4. Click **Add**
5. **Copiar inmediatamente** el valor del secret (solo aparece una vez)
6. Guardar en `.env`:
   ```env
   M365_CLIENT_SECRET=<valor-copiado>
   ```

---

## Paso 4: Configurar Permisos de Graph API

1. En la app registrada, ir a **API permissions**
2. Click **+ Add a permission**
3. Seleccionar **Microsoft Graph**
4. Elegir **Application permissions** (no Delegated)
5. Buscar y agregar:
   - `Mail.Read` - Leer correos
   - `User.Read` - (opcional) Validar identidad del usuario

6. Click **Add permissions**

### Otorgar Consentimiento de Administrador
1. Click **Grant admin consent for [organizacion]**
2. Confirmar

---

## Paso 5: Configurar Permisos del Mailbox Compartido

### Opción A: Via PowerShell (recomendado)
```powershell
# Conectar a Exchange Online
Connect-ExchangeOnline

# Dar permisos FullAccess a la app (usar el client_id como ID de objeto)
Add-MailboxPermission -Identity "images@diagonaleyewear.com" `
  -User "<client-id>" `
  -AccessRights FullAccess `
  -InheritanceType All

# Verificar
Get-MailboxPermission -Identity "images@diagonaleyewear.com" | 
  Where-Object {$_.User -eq "<client-id>"}
```

### Opción B: Via Exchange Admin Center
1. Ir a [https://admin.exchange.microsoft.com](https://admin.exchange.microsoft.com)
2. Buscar mailbox `images@diagonaleyewear.com`
3. En propiedades, ir a **Manage delegates**
4. Agregar la app como Full Access

---

## Paso 6: Verificar Configuración

Ejecutar test en terminal:
```powershell
# Activar venv
& "c:/.venv/Scripts/Activate.ps1"

# Importar y probar
python -c "from graph_mail_downloader import download_luxoptica_mail_attachments; summary = download_luxoptica_mail_attachments(); print(f'Scanned: {summary.messages_scanned}, Downloaded: {summary.attachments_downloaded}')"
```

Si todo es correcto, debería mostrar número de mensajes escaneados y adjuntos descargados.

---

## Paso 7: Integrar en App ABC

La funcionalidad de descarga está disponible en `app_enhanced.py`:

```python
from graph_mail_downloader import download_luxoptica_mail_attachments

summary = download_luxoptica_mail_attachments(
    sender_hint="luxottica",
    subject_hint="image",
    lookback_days=7,
    top_messages=100
)
print(f"Descargadas {summary.attachments_downloaded} imágenes")
```

---

## Troubleshooting

### Error: "Faltan variables de entorno"
- Verificar que `.env` tiene todas las 5 variables:
  - M365_TENANT_ID
  - M365_CLIENT_ID
  - M365_CLIENT_SECRET
  - M365_MAILBOX
  - M365_DOWNLOAD_ROOT

### Error: "No se obtuvo access_token"
- Verificar client_id y client_secret
- Verificar que el secret no ha expirado

### Error: "Unauthorized" en acceso al mailbox
- Verificar que la app tiene permisos FullAccess en `images@diagonaleyewear.com`
- Esperar 5-10 minutos después de otorgar permisos (propagación de AD)

### Carpeta de descargas vacía
- Verificar que hay correos en inbox de `images@diagonaleyewear.com`
- Verificar que los correos tienen adjuntos
- Revisar logs del script

---

## Seguridad

- ✅ Usar `client_credentials` (no interactivo)
- ✅ No guardar secretos en código
- ✅ Guardar en `.env` (no commiteado en git)
- ✅ Rotación de secretos cada 6-12 meses
- ✅ Auditoría de accesos en Azure AD
- ✅ Revisar intentos fallidos en Microsoft 365 Security & Compliance

---

## Referencias
- [Azure AD App Registration](https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app)
- [Microsoft Graph Permissions](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Graph Mail API](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0)
