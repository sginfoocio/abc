#!/usr/bin/env python3
"""
Script: Crear Scheduler para Descarga Automática (Windows Task Scheduler)
Ejecuta descarga de imágenes Luxoptica cada hora automáticamente
"""

import sys
import subprocess
from pathlib import Path


def create_windows_task():
    """Crea una tarea en Windows Task Scheduler."""
    
    project_path = Path(__file__).resolve().parent
    python_exe = project_path / ".venv" / "Scripts" / "python.exe"
    script_path = project_path / "run_luxoptica_download.py"
    
    print("\n" + "=" * 80)
    print("SCHEDULER: Crear Tarea Automática (Windows Task Scheduler)")
    print("=" * 80)
    
    if not python_exe.exists():
        print(f"\n❌ No encontrado: {python_exe}")
        print(f"   Verifica que el venv está en: {project_path / '.venv'}")
        return False
    
    if not script_path.exists():
        print(f"\n❌ No encontrado: {script_path}")
        return False
    
    task_name = "LuxopticaImageDownloader"
    task_description = "Descarga automática de imágenes de Luxoptica cada hora"
    
    # PowerShell script para crear la tarea
    ps_command = f'''
$taskName = "{task_name}"
$taskPath = "\\Diagonal\\"
$pythonExe = "{python_exe}"
$scriptPath = "{script_path}"

# Crear acción
$action = New-ScheduledTaskAction `
    -Execute $pythonExe `
    -Argument $scriptPath `
    -WorkingDirectory "{project_path}"

# Crear trigger (cada hora)
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddHours(1) `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -RepetitionDuration (New-TimeSpan -Days 365)

# Crear settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable

# Registrar tarea
try {{
    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "{task_description}" `
        -Force
    
    Write-Host "✅ Tarea '$taskName' creada correctamente"
    Write-Host "   Ejecutará cada hora automáticamente"
    Write-Host "   Para ver logs: Get-EventLog Application -Source 'Task Scheduler' -Newest 10"
}} catch {{
    Write-Host "❌ Error: $_"
}}
'''
    
    print(f"\nPara crear la tarea automática, ejecuta esto en PowerShell (como admin):\n")
    print("=" * 80)
    print(ps_command)
    print("=" * 80)
    
    print(f"\nAlternativa - Crear manualmente:")
    print(f"1. Abrir: Task Scheduler (Programador de tareas)")
    print(f"2. Create Basic Task")
    print(f"3. Name: {task_name}")
    print(f"4. Trigger: Daily / At every hour")
    print(f"5. Action: Start a program")
    print(f"   Program: {python_exe}")
    print(f"   Args: {script_path}")
    print(f"   Start in: {project_path}")
    
    return True


def test_manual_download():
    """Prueba ejecutar descarga manualmente."""
    print(f"\n" + "=" * 80)
    print("TEST: Ejecutar descarga manual (ahora)")
    print("=" * 80)
    
    try:
        result = subprocess.run(
            ["python", "run_luxoptica_download.py"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    print(f"\n🤖 CONFIGURACIÓN: Descarga Automática de Imágenes Luxoptica")
    
    print(f"\nOpciones:")
    print(f"  1. Ver instrucciones para crear scheduler")
    print(f"  2. Ejecutar descarga manual (test)")
    print(f"  3. Ver estado actual")
    print(f"  4. Salir")
    
    # Si se ejecuta sin argumentos, mostrar menú
    if len(sys.argv) < 2:
        print(f"\nUso: python scheduler_luxoptica.py [opción]")
        print(f"     python scheduler_luxoptica.py setup  # Ver instrucciones")
        print(f"     python scheduler_luxoptica.py test   # Ejecutar test")
        print(f"     python scheduler_luxoptica.py status # Ver estado")
        return 0
    
    option = sys.argv[1].lower()
    
    if option == "setup":
        create_windows_task()
        return 0
    elif option == "test":
        success = test_manual_download()
        return 0 if success else 1
    elif option == "status":
        import subprocess
        try:
            result = subprocess.run(
                ["python", "monitor_luxoptica_downloads.py"],
                cwd=Path(__file__).parent,
                capture_output=True,
                text=True,
            )
            print(result.stdout)
            return result.returncode
        except Exception as e:
            print(f"❌ Error: {e}")
            return 1
    else:
        print(f"❌ Opción no reconocida: {option}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
