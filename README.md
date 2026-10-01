# Emergencias AI

Microservicio de análisis preliminar de imágenes. El backend conserva la verdad de alertas, incidentes, evidencias, resultados y resúmenes; este servicio solo procesa una evidencia por trabajo. Audio, video y fusión por incidente quedan para etapas posteriores.

`AnalyzeEvidence` coordina la modalidad registrada y la entrega del resultado. Por ahora solo registra `AnalyzeImage`; audio y video requerirán sus propios casos de uso y contratos de resultado cuando se implementen, sin rutas vacías ni procesamiento simulado.

## Configuración y arranque

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`GET /health` no requiere configuración y devuelve `{"status":"ok","service":"emergencias-ai"}`. Para `POST /v1/jobs`, configurar `AI_SERVICE_TOKEN`, `AI_OPENROUTER_API_KEY`, `AI_CALLBACK_URL` y `AI_CALLBACK_TOKEN` en `.env` ignorado o en variables de entorno. El backend es el único responsable de S3 y entrega una URL temporal de lectura; IA no configura buckets ni credenciales AWS. `.env.example` documenta los nombres sin secretos reales. El modelo inicial es `google/gemini-3.8-flash` y se cambia con `AI_OPENROUTER_MODEL`.

## Contrato de trabajo

El backend autorizado llama `POST /v1/jobs` con `Authorization: Bearer <AI_SERVICE_TOKEN>` y JSON:

```json
{
  "jobId": "a38ab948-4c3f-43d5-b684-a1898c307a7e",
  "evidenceId": 12,
  "alertId": 7,
  "incidentId": 3,
  "downloadUrl": "https://almacenamiento.example/objeto?firma=temporal",
  "mimeType": "image/png",
  "checksumSha256": "sha256-hexadecimal-de-64-caracteres"
}
```

Los IDs deben ser positivos, `jobId` un UUID y `mimeType` JPEG, PNG o WebP. `downloadUrl` debe ser HTTPS, sin credenciales embebidas, IP literal, puerto alternativo ni redirecciones; es un secreto temporal y no debe aparecer en logs. El backend debe generarla para el objeto ya autorizado, con tiempo suficiente para comenzar la descarga y sus posibles reintentos. El checksum real debe ser el SHA-256 hexadecimal de los bytes; el valor de ejemplo es ilustrativo. Responde `202 {"jobId":"...","status":"accepted"}` tras validar y programar el trabajo, o `401`, `422`/`503` según el problema. La URL de callback viene de configuración, nunca de la petición.

El trabajo descarga como máximo 10 MiB desde la URL temporal, comprueba Content-Type, firma de bytes y checksum, llama a OpenRouter con `data:` URL privada y `response_format: json_schema` más `provider.require_parameters=true`, y valida de nuevo el JSON recibido. El prompt pide observaciones, riesgos y limitaciones, sin diagnóstico. El resultado contiene proveedor, modelo, versión de prompt y fecha, sin confianza numérica inventada. La red de despliegue debe restringir los destinos salientes permitidos; la validación de la URL en la aplicación no sustituye ese control.

El callback autenticado con `AI_CALLBACK_TOKEN` envía `jobId`, `evidenceId`, `alertId`, `incidentId`, `status: completed` y `result` (summary, observations, risks, limitations, provider, model, promptVersion, analyzedAt); o `status: failed` y `errorCode`. El receptor debe ser idempotente por `jobId`, porque un reintento puede repetir la entrega. No se registra la imagen ni los tokens.

## Límites de esta versión

Se usa `BackgroundTasks` del proceso FastAPI. `202` significa programado en memoria, **no persistido**: un reinicio o caída puede perder un trabajo aceptado. No hay procesamiento exactamente una vez, cola, Redis, Celery ni SQLite. Las llamadas OpenRouter y callback aplican timeout y hasta 3 intentos para fallos transitorios; un fallo definitivo del callback queda en el log para intervención, sin base local de reintentos. El backend deberá decidir su política de expiración/reenvío de trabajos.

## Pruebas

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
.venv/bin/python -m compileall -q app tests scripts
```

`scripts/live_image_smoke.py` realiza la prueba optativa real con una URL HTTPS de descarga entregada por el backend, llama a la API con un receptor de callback en localhost y verifica el resultado. Requiere `AI_OPENROUTER_API_KEY`, `AI_SMOKE_DOWNLOAD_URL` y `AI_SMOKE_SHA256` en el `.env` ignorado o el entorno; `AI_SMOKE_MIME_TYPE` es opcional (`image/png` por defecto). El backend prepara y limpia el objeto de prueba; este servicio no crea ni elimina objetos S3. Ejecutar desde esta carpeta: `.venv/bin/python -m scripts.live_image_smoke`. No publicar el repositorio ni fusionar la feature si esta prueba real falta o falla.
