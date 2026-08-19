#!/usr/bin/env python3
"""
Script de Monitoreo: Verificar logs y estado de descarga automática
Revisa si graph_mail_downloader está funcionando correctamente
"""

from pathlib import Path
from datetime import datetime
import json
import sys

def check_download_state():
    """Revisa el archivo de estado de descargas."""
    state_file = Path("docs/Luxoptica/descargas/.mail_download_state.json")
    
    print("\n" + "=" * 80)
    print("MONITOREO: Estado de Descargas Automáticas")
    print("=" * 80)
    
    if not state_file.exists():
        print(f"\n❌ No existe archivo de estado: {state_file}")
        print("   Posible causa: Aún no se ha ejecutado la descarga automática")
        print(f"   Path esperado: {state_file.absolute()}")
        return False
    
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        processed_ids = state.get("processed_message_ids", [])
        
        print(f"\n✅ Archivo de estado encontrado")
        print(f"   Ruta: {state_file.absolute()}")
        print(f"   Correos procesados: {len(processed_ids)}")
        
        if processed_ids:
            print(f"\n   IDs de correos procesados:")
            for msg_id in processed_ids[:5]:
                print(f"   - {msg_id[:50]}...")
            if len(processed_ids) > 5:
                print(f"   ... y {len(processed_ids) - 5} más")
        
        return True
    except Exception as e:
        print(f"❌ Error al leer estado: {e}")
        return False


def check_descargas_folder():
    """Revisa si hay archivos descargados."""
    descargas_dir = Path("docs/Luxoptica/descargas")
    
    print(f"\n" + "=" * 80)
    print("MONITOREO: Carpeta de Descargas")
    print("=" * 80)
    
    if not descargas_dir.exists():
        print(f"\n⚠️  Carpeta no existe: {descargas_dir}")
        print("   Se creará automáticamente cuando llegue la primera imagen")
        return True
    
    # Buscar carpetas por fecha y lote
    fecha_dirs = sorted([d for d in descargas_dir.iterdir() if d.is_dir() and d.name != ".mail_download_state.json"])
    
    if not fecha_dirs:
        print(f"\n⏳ Carpeta existe pero vacía (esperando respuesta de Luxoptica)")
        print(f"   {descargas_dir.absolute()}/")
        return True
    
    print(f"\n✅ Se han descargado imágenes:")
    total_images = 0
    
    for fecha_dir in fecha_dirs:
        lote_dirs = sorted([d for d in fecha_dir.iterdir() if d.is_dir()])
        for lote_dir in lote_dirs:
            images = list(lote_dir.glob("*"))
            image_count = len([f for f in images if f.is_file()])
            if image_count > 0:
                total_images += image_count
                print(f"   📁 {fecha_dir.name}/{lote_dir.name}/")
                print(f"      └─ {image_count} archivo(s)")
                # Mostrar primeros archivos
                for img in images[:3]:
                    if img.is_file():
                        size_kb = img.stat().st_size / 1024
                        print(f"         ├─ {img.name} ({size_kb:.1f} KB)")
                if image_count > 3:
                    print(f"         └─ ... y {image_count - 3} más")
    
    if total_images > 0:
        print(f"\n✅ TOTAL: {total_images} imagen(es) descargada(s)")
    
    return True


def check_env_config():
    """Revisa que las variables de entorno están configuradas."""
    from db_config import load_env_file
    import os
    
    load_env_file()
    
    print(f"\n" + "=" * 80)
    print("CONFIGURACIÓN: Variables Microsoft 365")
    print("=" * 80)
    
    required_vars = [
        "M365_TENANT_ID",
        "M365_CLIENT_ID",
        "M365_CLIENT_SECRET",
        "M365_MAILBOX",
        "M365_DOWNLOAD_ROOT",
    ]
    
    all_ok = True
    for var in required_vars:
        value = os.getenv(var, "")
        if value:
            # Ocultar secretos
            if "SECRET" in var:
                display = f"{value[:10]}...{value[-5:]}" if len(value) > 15 else "***"
            else:
                display = value
            print(f"   ✅ {var}: {display}")
        else:
            print(f"   ❌ {var}: NO CONFIGURADA")
            all_ok = False
    
    return all_ok


def check_graph_connectivity():
    """Prueba conectividad con Graph API."""
    from graph_mail_downloader import (
        load_m365_config,
        validate_m365_config,
        _get_access_token,
    )
    
    print(f"\n" + "=" * 80)
    print("CONECTIVIDAD: Microsoft Graph API")
    print("=" * 80)
    
    try:
        config = load_m365_config()
        missing = validate_m365_config(config)
        
        if missing:
            print(f"❌ Faltan variables: {', '.join(missing)}")
            return False
        
        token = _get_access_token(config)
        print(f"✅ Autenticación exitosa")
        print(f"   Token: {token[:20]}..." if len(token) > 20 else f"   Token: {token}")
        return True
    except Exception as e:
        print(f"❌ Error en autenticación: {e}")
        return False


def main():
    print("\n🔍 VERIFICACIÓN COMPLETA: Flujo Automatizado Luxoptica")
    print("   (Si todo es ✅, el sistema está listo)")
    
    results = {
        "Configuración": check_env_config(),
        "Conectividad Graph": check_graph_connectivity(),
        "Estado de descargas": check_download_state(),
        "Carpeta de descargas": check_descargas_folder(),
    }
    
    print(f"\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    
    all_ok = True
    for check, result in results.items():
        status = "✅" if result else "⚠️ "
        print(f"{status} {check}: {'OK' if result else 'REVISAR'}")
        if not result:
            all_ok = False
    
    if all_ok:
        print(f"\n🎉 TODO ESTÁ LISTO")
        print(f"\n   El sistema descargará automáticamente las imágenes cuando Luxoptica responda.")
        print(f"   Revisar: docs/Luxoptica/descargas/.mail_download_state.json")
        return 0
    else:
        print(f"\n⚠️  Hay items que revisar")
        print(f"\n   1. Verificar .env tiene todas las variables M365")
        print(f"   2. Ejecutar: python test_graph_api_config.py")
        print(f"   3. Revisar documentación: docs/Luxoptica/CONFIGURACION_GRAPH_API.md")
        return 1


if __name__ == "__main__":
    sys.exit(main())
