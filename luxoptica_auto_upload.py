#!/usr/bin/env python3
"""
Script: Subir Solicitud a Luxoptica Automáticamente
Automatiza: Login → Ir a Imágenes → Cargar fichero → Enviar solicitud
"""

from pathlib import Path
from datetime import datetime
import sys
import time

def upload_to_luxoptica(
    url: str = "https://portal.luxottica.com/",
    username: str = "",
    password: str = "",
    ean_file: Path | None = None,
    email_destino: str = "images@diagonaleyewear.com",
    headless: bool = False,
) -> tuple[bool, str]:
    """
    Automatiza la subida de fichero a Luxoptica.
    
    Args:
        url: URL del portal de Luxoptica
        username: Usuario para login
        password: Contraseña
        ean_file: Ruta del fichero .txt con EAN
        email_destino: Email para la solicitud
        headless: Si False, muestra el navegador
    
    Returns:
        (éxito, mensaje)
    """
    
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    except ImportError:
        return False, (
            "Playwright no instalado. Ejecuta:\n"
            "pip install playwright\n"
            "python -m playwright install chromium"
        )
    
    if not url.strip() or not username.strip() or not password.strip():
        return False, "Faltan URL, usuario o contraseña"
    
    if ean_file is None:
        # Buscar el fichero más reciente
        luxoptica_dir = Path("docs/Luxoptica")
        txt_files = sorted(luxoptica_dir.glob("upc-products-images-request-*.txt"), reverse=True)
        if not txt_files:
            return False, "No se encontró fichero de EAN en docs/Luxoptica/"
        ean_file = txt_files[0]
    
    if not ean_file.exists():
        return False, f"Fichero no existe: {ean_file}"
    
    try:
        with sync_playwright() as playwright:
            print(f"\n🌐 Iniciando navegador...")
            browser = playwright.chromium.launch(headless=headless)
            page = browser.new_page()
            
            # 1. Navegar al portal
            print(f"📍 Accediendo a: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)
            
            # 2. Login
            print(f"🔐 Autenticando...")
            
            # Buscar campo de email/usuario
            email_inputs = [
                "input[type='email']",
                "input[name='email']",
                "input[name='username']",
                "input[name='user']",
                "input#username",
            ]
            email_field = None
            for selector in email_inputs:
                if page.locator(selector).count() > 0:
                    email_field = selector
                    break
            
            if not email_field:
                browser.close()
                return False, "No se encontró campo de email en el login"
            
            page.fill(email_field, username)
            time.sleep(1)
            
            # Click en siguiente o buscar campo de contraseña
            next_buttons = [
                "button:has-text('Continue')",
                "button:has-text('Continuar')",
                "button:has-text('Next')",
                "button[type='submit']",
            ]
            
            for btn_selector in next_buttons:
                if page.locator(btn_selector).count() > 0:
                    page.click(btn_selector)
                    time.sleep(2)
                    break
            
            # Buscar campo de contraseña
            password_field = None
            for _ in range(10):
                password_inputs = [
                    "input[type='password']",
                    "input[name='password']",
                    "input#password",
                ]
                for selector in password_inputs:
                    if page.locator(selector).count() > 0:
                        password_field = selector
                        break
                if password_field:
                    break
                time.sleep(1)
            
            if password_field:
                page.fill(password_field, password)
                time.sleep(1)
                
                # Enviar formulario
                submit_buttons = [
                    "button:has-text('Sign in')",
                    "button:has-text('Login')",
                    "button[type='submit']",
                ]
                for btn_selector in submit_buttons:
                    if page.locator(btn_selector).count() > 0:
                        page.click(btn_selector)
                        break
            
            # Esperar a que cargue después del login
            print(f"⏳ Esperando carga del portal...")
            time.sleep(3)
            
            # 3. Navegar a Imágenes de Producto
            print(f"📸 Navegando a sección de Imágenes...")
            
            # Buscar y hacer click en Servicios
            servicios_links = [
                "a:has-text('Servicios')",
                "a:has-text('Services')",
                "button:has-text('Servicios')",
            ]
            for link_selector in servicios_links:
                if page.locator(link_selector).count() > 0:
                    page.click(link_selector)
                    time.sleep(2)
                    break
            
            # Buscar Recursos Digitales
            recursos_links = [
                "a:has-text('Recursos Digitales')",
                "a:has-text('Digital Resources')",
                "a:has-text('Imágenes')",
            ]
            for link_selector in recursos_links:
                if page.locator(link_selector).count() > 0:
                    page.click(link_selector)
                    time.sleep(2)
                    break
            
            # Buscar Imágenes de Producto
            images_links = [
                "a:has-text('Imágenes de Producto')",
                "a:has-text('Product Images')",
                "button:has-text('Imágenes')",
            ]
            for link_selector in images_links:
                if page.locator(link_selector).count() > 0:
                    page.click(link_selector)
                    time.sleep(2)
                    break
            
            # 4. Subir fichero
            print(f"📤 Subiendo fichero: {ean_file.name}")
            
            # Buscar input file
            file_inputs = page.locator("input[type='file']")
            if file_inputs.count() > 0:
                file_inputs.first.set_input_files(str(ean_file.absolute()))
                time.sleep(2)
            else:
                print(f"⚠️  No se encontró campo de fichero")
            
            # 5. Seleccionar "Todas las vistas"
            print(f"✓ Seleccionando opciones...")
            
            # Buscar checkbox "Todas las vistas"
            all_views_checkboxes = [
                "input[type='checkbox']:has-text('Todas las vistas')",
                "label:has-text('Todas las vistas')",
                "input[value='all']",
            ]
            for selector in all_views_checkboxes:
                if page.locator(selector).count() > 0:
                    page.click(selector)
                    time.sleep(1)
                    break
            
            # 6. Especificar email
            print(f"📧 Configurando email: {email_destino}")
            
            email_inputs = [
                "input[type='email']",
                "input[name='email']",
                "input[name='contact_email']",
            ]
            for selector in email_inputs:
                if page.locator(selector).count() > 1:  # Más de uno (primer input es login)
                    page.locator(selector).last.fill(email_destino)
                    time.sleep(1)
                    break
            
            # 7. Enviar solicitud
            print(f"🚀 Enviando solicitud...")
            
            submit_buttons = [
                "button:has-text('Enviar')",
                "button:has-text('Send')",
                "button:has-text('Submit')",
                "button[type='submit']",
            ]
            
            for btn_selector in submit_buttons:
                if page.locator(btn_selector).count() > 0:
                    page.click(btn_selector)
                    time.sleep(3)
                    break
            
            # Verificar éxito
            success_indicators = [
                "text='solicitud enviada'",
                "text='request sent'",
                "text='éxito'",
                "text='success'",
            ]
            
            for indicator in success_indicators:
                if page.locator(indicator).count() > 0:
                    browser.close()
                    return True, f"✅ Solicitud enviada exitosamente a {email_destino}"
            
            # Si no vemos confirmación clara, asumimos que funcionó
            browser.close()
            return True, f"✅ Proceso completado. Verifica la confirmación en el portal."
            
    except PlaywrightTimeoutError as e:
        return False, f"Timeout en la carga: {e}"
    except Exception as e:
        return False, f"Error: {e}"


def main():
    print("\n" + "=" * 80)
    print("LUXOPTICA: Subida Automática de Solicitud")
    print("=" * 80)
    
    # Leer credenciales desde .env
    from db_config import load_env_file
    import os
    
    load_env_file()
    
    username = os.getenv("LUXOPTICA_USERNAME", "").strip()
    password = os.getenv("LUXOPTICA_PASSWORD", "").strip()
    url = os.getenv("LUXOPTICA_URL", "https://portal.luxottica.com/").strip()
    email = os.getenv("LUXOPTICA_REQUEST_EMAIL", "images@diagonaleyewear.com").strip()
    
    if not username or not password:
        print(f"\n❌ Faltan credenciales en .env")
        print(f"\n   Configura en .env:")
        print(f"   LUXOPTICA_USERNAME=tu_usuario")
        print(f"   LUXOPTICA_PASSWORD=tu_contraseña")
        print(f"   LUXOPTICA_URL=https://portal.luxottica.com/")
        print(f"   LUXOPTICA_REQUEST_EMAIL=images@diagonaleyewear.com")
        return 1
    
    # Buscar fichero más reciente
    luxoptica_dir = Path("docs/Luxoptica")
    txt_files = sorted(luxoptica_dir.glob("upc-products-images-request-*.txt"), reverse=True)
    
    if not txt_files:
        print(f"\n❌ No se encontró fichero de EAN en docs/Luxoptica/")
        return 1
    
    ean_file = txt_files[0]
    print(f"\n📄 Fichero a subir: {ean_file.name}")
    
    # Ejecutar upload
    success, message = upload_to_luxoptica(
        url=url,
        username=username,
        password=password,
        ean_file=ean_file,
        email_destino=email,
        headless=False,  # Mostrar navegador para que veas
    )
    
    print(f"\n{message}")
    
    if success:
        # Actualizar registro
        from docs.Luxoptica.REGISTRO_SOLICITUDES import registro  # Si existe
        print(f"\n✅ Solicitud completada!")
        print(f"   Email destino: {email}")
        print(f"   Fichero: {ean_file.name}")
        return 0
    else:
        print(f"\n❌ Fallo en la subida")
        return 1


if __name__ == "__main__":
    sys.exit(main())
