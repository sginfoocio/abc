# Checklist de validación Graph — Luxottica

> **No es un checklist de activación.** No registra una autorización ni demuestra que Graph esté
> configurado o en funcionamiento. No registres aplicaciones, concedas permisos, pruebes
> credenciales ni leas/descargues correo sin autorización específica.

- [ ] Entorno y SHA candidatos identificados.
- [ ] Buzón, intervalo de lectura, muestra y permisos mínimos autorizados.
- [ ] Runtime actual del monitor comprobado en modo de solo lectura; no iniciar una segunda instancia.
- [ ] Secrets disponibles solo mediante el mecanismo aprobado; no impresos ni guardados en Git.
- [ ] Lectura acotada verificada antes de cualquier descarga o escritura local.
- [ ] Descarga/importación con efectos autorizada por separado y aislada.
- [ ] Evidencia reproducible guardada fuera de Git, sin mensajes, identificadores ni adjuntos sensibles.
- [ ] Comprobaciones no ejecutables marcadas como bloqueadas.

Sigue la [validación local de Cloud](../CLOUD_LOCAL_VALIDATION.md). La recepción por Graph no
demuestra que una solicitud se haya vinculado a un pedido por EAN coincidente.
