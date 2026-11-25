#!/usr/bin/env python3
"""
Script: Subir Solicitud a Luxoptica Automáticamente
Automatiza: Login → Ir a Imágenes → Cargar fichero → Enviar solicitud
"""

from pathlib import Path
from datetime import datetime
import sys
import time
import re
from tempfile import TemporaryDirectory
from image_repository import ImageRepository
from luxottica_config import LOGIN_HOST, PORTAL_HOST, PORTAL_URL, validate_portal_url
from provider_images import LUXOTTICA_CONFIRMATION_PREFIX
from urllib.parse import urlsplit

CONFIRMATION = re.compile(
    r"^(?:tu solicitud se ha enviado|su solicitud se ha enviado|your request has been sent"
    r"|solicitud enviada|request sent)(?:[.!]|\s|$)", re.I
)


class ViewTypeUnavailable(Exception):
    pass


class CookieConsentUnavailable(Exception):
    pass


def write_luxottica_upload_file(path: Path, codes: list[str]) -> None:
    path.write_bytes(("\r\n".join(codes) + "\r\n").encode("ascii"))


def _choose_all_views(page, ean_file, timeout_error):
    views = page.get_by_text(re.compile(r"^todas las vistas$|^all views$", re.I))
    page.locator("input[type='file']").set_input_files(str(ean_file.resolve()))
    page.get_by_role("button", name=re.compile(r"^continuar$|^continue$", re.I)).click()
    try:
        views.wait_for(state="visible", timeout=15000)
    except timeout_error as error:
        raise ViewTypeUnavailable from error
    views.click()
    page.get_by_role("heading", name=re.compile(r"^enviar solicitud$|^send request$", re.I)).wait_for(timeout=15000)


def _legal_notice(page):
    return page.get_by_role("button", name=re.compile(r"^acepto$|^i agree$|^aceptar y continuar$", re.I)).first


def _pending_conditions(page):
    sign_documents = page.get_by_role("button", name=re.compile(r"^firmar ahora$|^sign now$", re.I)).first
    return sign_documents.is_visible() or _legal_notice(page).is_visible()


def _accept_cookie_consent(page, timeout_error):
    notice = page.get_by_text(re.compile(
        r"(?:utilizamos cookies necesarias|we use necessary cookies).*"
        r"(?:cookies opcionales|optional cookies)", re.I | re.S
    )).first
    if not notice.is_visible():
        return

    accept = notice.locator("xpath=..").get_by_role(
        "button", name=re.compile(r"^aceptar$|^accept$", re.I)
    )
    if not accept.is_visible():
        raise CookieConsentUnavailable
    accept.click()
    try:
        notice.wait_for(state="hidden", timeout=10000)
    except timeout_error as error:
        raise CookieConsentUnavailable from error


def upload_to_luxoptica(
    url: str = PORTAL_URL,
    username: str = "",
    password: str = "",
    ean_file: Path | None = None,
    email_destino: str = "images@diagonaleyewear.com",
    headless: bool = False,
    keep_browser_open: bool = False,
) -> tuple[bool, str]:
    from process_activity import record_process
    with record_process("luxoptica-upload") as receipt:
        result = _prepare_luxoptica_upload(url, username, password, ean_file, email_destino,
                                          headless, keep_browser_open)
        receipt["result"] = "Correcto" if result[0] else "Error"
        return result


def _prepare_luxoptica_upload(
    url: str = PORTAL_URL,
    username: str = "",
    password: str = "",
    ean_file: Path | None = None,
    email_destino: str = "images@diagonaleyewear.com",
    headless: bool = False,
    keep_browser_open: bool = False,
) -> tuple[bool, str]:
    if ean_file is None:
        files = sorted((Path(__file__).resolve().parent / "docs" / "Luxoptica").glob(
            "upc-products-images-request-*.txt"), reverse=True)
        if not files:
            return False, "No se encontro fichero de EAN en docs/Luxoptica/"
        ean_file = files[0]
    if not ean_file.is_file():
        return False, f"Fichero no existe: {ean_file}"
    try:
        eans = list(dict.fromkeys(ean.strip() for ean in ean_file.read_text(encoding="utf-8").splitlines()
                                  if ean.strip()))
        if not eans:
            return False, "El fichero de solicitud no contiene EAN"
        repository = ImageRepository()
        pending = [ean for ean in eans if repository.pending_views(ean, ("frontal", "lateral", "perspectiva"))]
    except (OSError, ValueError) as error:
        return False, f"No se pudo consultar el repositorio de imagenes: {error}"
    if not pending:
        return True, "Todas las vistas ya disponibles; solicitud reutilizada sin acceso al portal."
    with TemporaryDirectory(prefix="luxoptica-request-") as directory:
        request = Path(directory) / ean_file.name
        write_luxottica_upload_file(request, pending)
        return _upload_to_luxoptica(url, username, password, request, email_destino,
                                   headless, keep_browser_open)


def _upload_to_luxoptica(
    url: str,
    username: str,
    password: str,
    ean_file: Path,
    email_destino: str,
    headless: bool,
    keep_browser_open: bool,
    *,
    preview_only: bool = False,
    before_send=None,
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
    
    clicked = False
    try:
        validate_portal_url(url)
        with sync_playwright() as playwright:
            print(f"\n🌐 Iniciando navegador...")
            browser = playwright.chromium.launch(headless=headless)
            page = browser.new_page()

            def _count(selector: str) -> int:
                try:
                    return page.locator(selector).count()
                except Exception:
                    return 0
            
            # 1. Navegar al portal
            print(f"📍 Accediendo a: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(2)
            
            # 2. Login
            print(f"🔐 Autenticando...")
            
            # Buscar campo de usuario (en este portal suele ser "Username")
            username_inputs = [
                "input[placeholder='Username']",
                "input[aria-label='Username']",
                "input[placeholder*='username' i]",
                "input[name*='username' i]",
                "input[name*='user' i]",
                "input[id*='username' i]",
                "input[type='text']",
                "input[type='email']",
                "form input:not([type='hidden']):not([type='password'])",
            ]
            username_field = None
            for _ in range(30):
                for selector in username_inputs:
                    try:
                        locator = page.locator(selector)
                        if locator.count() > 0 and locator.first.is_visible():
                            username_field = locator.first
                            break
                    except Exception:
                        continue
                if username_field is not None:
                    break
                time.sleep(1)
            
            if username_field is None:
                browser.close()
                return False, "No se encontró campo de usuario en el login"
            
            if urlsplit(page.url).hostname != LOGIN_HOST:
                return False, "dominio_de_autenticacion_inesperado"
            username_field.fill(username)
            
            # El portal muestra la contraseña después de CONTINUE.
            try:
                continue_btn = page.get_by_role("button", name=re.compile(r"continue|continuar|next", re.I))
                continue_btn.first.click(timeout=15000)
            except Exception:
                page.locator("button[type='submit']").first.click(timeout=15000)
            
            # Buscar campo de contraseña
            password_field = None
            for _ in range(10):
                password_inputs = [
                    "input[type='password']",
                    "input[name='password']",
                    "input#password",
                ]
                for selector in password_inputs:
                    if _count(selector) > 0:
                        password_field = selector
                        break
                if password_field:
                    break
                time.sleep(1)
            
            if password_field is None:
                browser.close()
                return False, "No se encontró campo de contraseña después de CONTINUE"

            if urlsplit(page.url).hostname != LOGIN_HOST:
                return False, "dominio_de_autenticacion_inesperado"
            page.fill(password_field, password)

            # Enviar formulario una sola vez.
            submit_btn = page.get_by_role("button", name=re.compile(r"sign in|login|continue", re.I))
            if submit_btn.count() > 0:
                submit_btn.first.click()
            else:
                page.locator("button[type='submit']").first.click()
            
            # Esperar a que cargue después del login
            print(f"⏳ Esperando carga del portal...")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PlaywrightTimeoutError:
                pass
            time.sleep(5)
            if urlsplit(page.url).hostname != PORTAL_HOST:
                return False, "sesion_no_verificada"
            _accept_cookie_consent(page, PlaywrightTimeoutError)
            if _pending_conditions(page):
                return False, "condiciones_pendientes"

            # 3. Navegar a Imágenes de Producto
            print(f"📸 Navegando a sección de Imágenes...")
            
            # El menú del portal es dinámico y no siempre usa enlaces <a>.
            def _click_text(labels: list[str]) -> bool:
                for label in labels:
                    try:
                        target = page.get_by_text(label, exact=True).first
                        if target.count() > 0:
                            target.click(timeout=15000)
                            time.sleep(2)
                            return True
                    except Exception:
                        continue
                return False

            if not _click_text(["Servicios", "Services"]):
                browser.close()
                return False, "No se encontró el menú Servicios"

            if not _click_text(["Recursos digitales", "Recursos Digitales", "Digital Resources"]):
                browser.close()
                return False, "No se encontró Recursos digitales"

            if not _click_text(["Imágenes de producto", "Imágenes de Producto", "Product Images"]):
                browser.close()
                return False, "No se encontró Imágenes de producto"

            page.wait_for_load_state("domcontentloaded", timeout=30000)
            time.sleep(3)

            _accept_cookie_consent(page, PlaywrightTimeoutError)
            if _pending_conditions(page):
                return False, "condiciones_pendientes"

            _choose_all_views(page, ean_file, PlaywrightTimeoutError)

            # 6. Especificar email
            print(f"📧 Configurando email: {email_destino}")

            # El portal deja info@diagonaleyewear.com por defecto y exige
            # añadir explícitamente cada dirección adicional.
            email_inputs = page.get_by_role("textbox", name=re.compile(r"dirección de email|email address", re.I))
            if email_destino not in [field.input_value() for field in email_inputs.all()]:
                add_email = page.get_by_text(
                    re.compile(r"añadir dirección de email|add email address", re.I)
                ).first
                try:
                    add_email.click(timeout=15000)
                    time.sleep(1)
                except PlaywrightTimeoutError:
                    browser.close()
                    return False, "correo_destino_no_configurable"

                editable = [field for field in email_inputs.all() if field.is_editable()]
                if len(editable) != 1:
                    browser.close()
                    return False, "No apareció el campo para añadir la dirección de email"
                editable[0].fill(email_destino)
            
            # Mark even a click timeout as uncertain; the click may already have reached the server.
            send = page.get_by_role("button", name=re.compile(r"^enviar solicitud$|^send request$", re.I))
            visible = [button for button in send.all() if button.is_visible() and button.is_enabled()]
            if not visible or urlsplit(page.url).hostname != PORTAL_HOST:
                return False, "boton_envio_no_identificado"
            confirmation = page.get_by_text(CONFIRMATION).first
            if confirmation.is_visible():
                return False, "confirmacion_preexistente"
            if preview_only:
                return True, "Vista previa lista sin enviar"
            if before_send is not None and not before_send():
                return False, "configuracion_cambiada_antes_del_envio"
            clicked = True
            visible[-1].click(timeout=15000)
            confirmation.wait_for(state="visible", timeout=20000)
            if urlsplit(page.url).hostname != PORTAL_HOST:
                return False, "envio_sin_confirmacion"
            if keep_browser_open:
                try:
                    page.wait_for_event("close", timeout=600000)
                except (KeyboardInterrupt, PlaywrightTimeoutError):
                    pass
            return True, LUXOTTICA_CONFIRMATION_PREFIX + " ".join(confirmation.inner_text().split())[:200]
    except ViewTypeUnavailable:
        return False, "ver_tipo_sin_todas_las_vistas"
    except CookieConsentUnavailable:
        return False, "cookie_consent_no_resuelto"
    except Exception:
        return False, "envio_sin_confirmacion" if clicked else "preparacion_portal_fallida"


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
    url = os.getenv("LUXOPTICA_URL", PORTAL_URL).strip()
    email = os.getenv("LUXOPTICA_REQUEST_EMAIL", "images@diagonaleyewear.com").strip()
    headless = os.getenv("LUXOPTICA_HEADLESS", "true").strip().lower() not in {"0", "false", "no"}
    keep_browser_open = os.getenv("LUXOPTICA_KEEP_BROWSER_OPEN", "false").strip().lower() in {"1", "true", "yes"}
    
    if not username or not password:
        print(f"\n❌ Faltan credenciales en .env")
        print(f"\n   Configura en .env:")
        print(f"   LUXOPTICA_USERNAME=tu_usuario")
        print(f"   LUXOPTICA_PASSWORD=tu_contraseña")
        print(f"   LUXOPTICA_URL={PORTAL_URL}")
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
        headless=headless,
        keep_browser_open=keep_browser_open,
    )
    
    print(f"\n{message}")
    
    if success:
        print(f"\n✅ Solicitud completada!")
        print(f"   Email destino: {email}")
        print(f"   Fichero: {ean_file.name}")
        return 0
    else:
        print(f"\n❌ Fallo en la subida")
        return 1


if __name__ == "__main__":
    sys.exit(main())
