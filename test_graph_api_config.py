#!/usr/bin/env python3
"""
Script de prueba: Validar configuración Microsoft Graph API
Verifica que las credenciales y permisos están correctamente configurados.
"""

from pathlib import Path
import sys

# Agregar el directorio padre al path para importar módulos del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

from graph_mail_downloader import (
    load_m365_config,
    validate_m365_config,
    _get_access_token,
    _list_inbox_messages,
)


def test_config():
    """Prueba 1: Cargar y validar configuración."""
    print("=" * 80)
    print("PRUEBA 1: Cargar configuración desde .env")
    print("=" * 80)
    
    try:
        config = load_m365_config()
        print(f"✅ Configuración cargada")
        print(f"   Tenant ID: {config.tenant_id[:10]}..." if config.tenant_id else "   ❌ Tenant ID vacío")
        print(f"   Client ID: {config.client_id[:10]}..." if config.client_id else "   ❌ Client ID vacío")
        print(f"   Mailbox: {config.mailbox}")
        print(f"   Download Root: {config.download_root}")
        
        missing = validate_m365_config(config)
        if missing:
            print(f"\n❌ Faltan variables en .env:")
            for var in missing:
                print(f"   - {var}")
            return False
        
        print(f"\n✅ Todas las variables configuradas")
        return True, config
    except Exception as e:
        print(f"❌ Error al cargar configuración: {e}")
        return False


def test_token(config):
    """Prueba 2: Obtener access token de Microsoft Graph."""
    print("\n" + "=" * 80)
    print("PRUEBA 2: Autenticar con Microsoft Graph")
    print("=" * 80)
    
    try:
        token = _get_access_token(config)
        print(f"✅ Access token obtenido")
        print(f"   Token length: {len(token)} caracteres")
        print(f"   Primeros 20 caracteres: {token[:20]}...")
        return True, token
    except Exception as e:
        print(f"❌ Error al obtener token: {e}")
        print(f"\nPosibles causas:")
        print(f"   1. Client ID o Client Secret incorrectos")
        print(f"   2. Tenant ID incorrecto")
        print(f"   3. Client secret expirado")
        print(f"   4. App no registrada en Azure AD")
        return False


def test_inbox(token, config):
    """Prueba 3: Leer inbox del mailbox compartido."""
    print("\n" + "=" * 80)
    print("PRUEBA 3: Leer inbox del mailbox compartido")
    print("=" * 80)
    
    try:
        messages = _list_inbox_messages(token, config.mailbox, top=10)
        print(f"✅ Inbox accesible")
        print(f"   Mensajes recuperados: {len(messages)}")
        
        if messages:
            print(f"\n   Últimos mensajes:")
            for i, msg in enumerate(messages[:3], 1):
                subject = msg.get("subject", "[Sin asunto]")
                received = msg.get("receivedDateTime", "")
                has_att = msg.get("hasAttachments", False)
                att_icon = "📎" if has_att else "  "
                print(f"   {i}. {att_icon} {subject} ({received[:10]})")
        else:
            print(f"   (Inbox vacío o sin mensajes en los últimos días)")
        
        return True
    except Exception as e:
        print(f"❌ Error al leer inbox: {e}")
        print(f"\nPosibles causas:")
        print(f"   1. Permisos de Graph API no concedidos (Mail.Read)")
        print(f"   2. Permisos de mailbox compartido no asignados")
        print(f"   3. Mailbox incorrecto o no existe")
        print(f"   4. Esperar 5-10 minutos después de cambiar permisos")
        return False


def main():
    print("\n" + "🔍 " * 20)
    print("VALIDACIÓN: Configuración Microsoft Graph API para Luxottica")
    print("🔍 " * 20 + "\n")
    
    # Paso 1: Configuración
    result = test_config()
    if not result or result is False:
        print("\n❌ Falla en configuración. Ver guía: docs/Luxoptica/CONFIGURACION_GRAPH_API.md")
        return 1
    
    _, config = result
    
    # Paso 2: Token
    result = test_token(config)
    if not result or result is False:
        print("\n❌ Falla en autenticación. Ver guía: docs/Luxoptica/CONFIGURACION_GRAPH_API.md Paso 1-2")
        return 1
    
    _, token = result
    
    # Paso 3: Inbox
    if not test_inbox(token, config):
        print("\n❌ Falla en acceso a inbox. Ver guía: docs/Luxoptica/CONFIGURACION_GRAPH_API.md Paso 3-4")
        return 1
    
    # Éxito
    print("\n" + "=" * 80)
    print("✅ TODAS LAS PRUEBAS PASARON")
    print("=" * 80)
    print("\nConfiguracion lista para usar en:")
    print("  - app_enhanced.py (interfaz web)")
    print("  - Scripts de descarga automática")
    print("\nProximos pasos:")
    print("  1. Probar descarga con: python graph_mail_downloader.py")
    print("  2. Integrar en scheduler para ejecutar periódicamente")
    print("  3. Ver logs en docs/Luxoptica/descargas/.mail_download_state.json")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
