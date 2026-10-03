# Emergencias AI

Microservicio sin estado que analiza evidencias de emergencias (imagen, audio y video) y sintetiza el resumen preliminar de un incidente. El backend Spring conserva la verdad de alertas, incidentes, evidencias, trabajos, resultados y versiones del resumen. Este servicio no guarda nada: recibe una solicitud y devuelve el resultado en la misma respuesta.

El análisis es apoyo informativo para el personal. No es un diagnóstico, no es un triaje y no decide el despacho.

## Diseño

| Capa | Contenido |
|---|---|
| `domain/` | Resultado de evidencia, resumen de incidente, vocabularios cerrados (`eventType`, `hazards`, `severity`) y reglas: una gravedad sin justificación pasa a `undetermined`; un rango de personas incoherente pasa a desconocido; en el resumen se descartan las citas a fuentes no recibidas y las afirmaciones que se quedan sin ninguna fuente válida. |
| `application/` | `AnalyzeEvidence` (una evidencia), `SynthesizeIncidentSummary` (todas las del incidente), límites por modalidad (`pipelines/media.py`), esquemas de salida estructurada y los puertos `EvidenceReader`, `MultimodalModel` y `MediaPreparer`. No importa FastAPI ni clientes de proveedores. |
| `adapters/` | HTTP de entrada, descarga con URL temporal, `OpenRouterModel` (el adaptador de proveedor para las tres modalidades y la síntesis), `PruebaModel` (el analizador de prueba) y `FfmpegPreparer` (`media/ffmpeg_preparer.py`), que mide la duración real con ffprobe y reduce el video a fotogramas y audio con ffmpeg. |
| `prompts/` | Un archivo por prompt; el nombre del archivo es la versión que se registra en cada resultado (`image-v2`, `audio-v1`, `video-v1`, `video-v2`, `summary-v1`). |

Imagen, audio y video comparten el caso de uso. Cada modalidad solo define sus límites, su prompt, su esquema y, opcionalmente, su modelo (`ModalityProfile`). Para cambiar de proveedor o usar un modelo local se escribe otro adaptador de `MultimodalModel`.

## Configuración y arranque

Para medir la duración real y analizar el video por fotogramas hacen falta `ffmpeg` y `ffprobe` (en macOS, `brew install ffmpeg`). Sin ellos el servicio arranca igual: la duración se lee de la cabecera y el video se envía completo. La imagen Docker ya los instala.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En producción corre como contenedor (`Dockerfile`: usuario sin privilegios, puerto 8000, healthcheck sobre `/health`). Las versiones de las dependencias se fijan con `constraints.txt`:

```sh
docker build -t emergencias-mia .
docker run --env-file .env -p 127.0.0.1:8000:8000 emergencias-mia
```

`GET /health` no requiere configuración. Para los endpoints `/v1` hacen falta `AI_SERVICE_TOKEN` y `AI_OPENROUTER_API_KEY` en `.env` (ignorado por git) o en variables de entorno; sin ellos responden `503 service_not_configured`.

### Analizador de prueba

Con `AI_PROVIDER=prueba` el servicio no llama a ningún proveedor: `PruebaModel` devuelve salidas fijas que cumplen el esquema de cada modalidad (observaciones y peligros en imagen, transcripción enmascarada en audio, línea de tiempo en video) y un resumen que cita solo las fuentes recibidas. No hace falta `AI_OPENROUTER_API_KEY`, pero `AI_SERVICE_TOKEN` sigue siendo obligatorio. La validación del archivo (MIME, tamaño, firma, SHA-256 y duración) se hace igual que con el proveedor real. Los resultados llevan `provenance.provider = "prueba"` y una limitación que aclara que no vienen de un modelo, para que nadie los confunda con un análisis real. Sirve para probar el backend y las apps sin clave ni costo; no se usa en producción. `.env.example` documenta los modelos por tarea y los límites. IA no configura buckets ni credenciales AWS: el backend entrega URLs temporales de lectura.

## Contrato

Todas las llamadas llevan `Authorization: Bearer <AI_SERVICE_TOKEN>` y son **síncronas**. El backend las hace desde un worker. Su tabla de trabajos es la cola duradera, así que no hay callback.

### `POST /v1/analyses`: una evidencia

```json
{
  "jobId": "a38ab948-4c3f-43d5-b684-a1898c307a7e",
  "evidenceId": 12, "alertId": 7, "incidentId": 3,
  "downloadUrl": "https://almacenamiento.example/objeto?firma=temporal",
  "mimeType": "image/jpeg",
  "checksumSha256": "<sha-256 hexadecimal de los bytes>"
}
```

`downloadUrl` debe ser HTTPS, sin credenciales, IP literal, puerto alternativo ni redirecciones. Es un secreto temporal: no se registra en logs ni se repite en los errores de validación. Su vigencia debe cubrir el peor caso de la llamada (ver tiempos).

| Modalidad | MIME admitidos | Límite por defecto |
|---|---|---|
| Imagen | `image/jpeg`, `image/png`, `image/webp` | 10 MiB |
| Audio | `audio/mp4`, `audio/m4a`, `audio/x-m4a`, `audio/mpeg`, `audio/aac`, `audio/wav`, `audio/x-wav`, `audio/ogg` | 5 MiB (igual que el backend) y 120 s |
| Video | `video/mp4`, `video/quicktime`, `video/webm` | 20 MiB y 60 s |

Antes de llamar al modelo se comprueban, en este orden: el MIME, que el archivo no esté vacío, el tamaño, la firma de bytes, el SHA-256 y la duración. La duración se lee primero de la cabecera en MP4/M4A/MOV y WAV, y después ffprobe mide la duración real de audio y video en todos los formatos admitidos (en un WebM sin duración declarada, recorriendo sus paquetes). Un archivo que ffprobe no puede leer se rechaza con `unreadable_media`. Si ffprobe no está instalado o no responde a tiempo, queda solo el control por cabecera.

#### Video por fotogramas

Con `AI_VIDEO_MODE=frames` (por defecto) el modelo no recibe el video completo. ffmpeg elige hasta `AI_VIDEO_MAX_FRAMES` fotogramas (8): siempre el primero y el último, luego los cambios de escena cuyo puntaje supera `AI_VIDEO_SCENE_THRESHOLD` (0.3) y el resto repartido de forma uniforme. Cada fotograma va en JPEG con el lado mayor reducido a 1280 px como máximo (sin agrandar los más chicos) y precedido por su segundo exacto. Si el video tiene sonido, la pista se envía aparte en M4A (AAC mono); si no, solo los fotogramas. El prompt es `video-v2` y la salida tiene el mismo esquema que antes (con `transcript` y `timeline`). Los `startSecond` de la línea de tiempo se ajustan al segundo de fotograma más cercano, porque el modelo solo vio esos instantes.

Comparado con el video entero, la solicitud al proveedor pesa mucho menos (unas decenas o cientos de KB por fotograma más el audio, en lugar del archivo completo en base64) y usa menos tokens de entrada. El costo es que lo que pasa entre dos fotogramas no se ve: el modelo solo lo conoce por el audio.

Si no se pueden preparar los fotogramas (ffmpeg no instalado, timeout o error), con `AI_VIDEO_FALLBACK_TO_FULL=true` (por defecto) se envía el video completo con el prompt `video-v1`; con `false` se responde `unreadable_media`. `AI_VIDEO_MODE=full` mantiene el comportamiento anterior. Cada proceso de ffmpeg corre sin stdin, solo con archivos locales (`-protocol_whitelist file`), con el formato de entrada fijado por el MIME, límites de sondeo y un timeout propio (`AI_FFMPEG_TIMEOUT_SECONDS`=10). Los archivos temporales se borran siempre y nunca se registra su contenido.

Respuesta `200`:

```json
{
  "jobId": "…", "evidenceId": 12, "alertId": 7, "incidentId": 3,
  "modality": "image", "schemaVersion": "evidence-analysis.v1",
  "analysis": {
    "summary": "Choque entre dos autos en una intersección.",
    "eventType": "traffic_accident",
    "people": {"min": 1, "max": 2},
    "hazards": ["traffic"],
    "observations": [{"text": "Dos autos con daños frontales.", "basis": "observed"}],
    "risks": ["Tráfico circulando cerca de los vehículos."],
    "severity": {"level": "moderate", "basis": ["Daños frontales visibles en ambos autos."]},
    "limitations": ["El interior de los vehículos no es visible."],
    "transcript": null,
    "timeline": []
  },
  "provenance": {"provider": "openrouter", "model": "…", "promptVersion": "image-v2", "generatedAt": "…", "method": "model"}
}
```

`transcript` aparece en audio y video (con nombres y teléfonos enmascarados), y `timeline` (`startSecond` y `text`) en video. `basis` distingue lo observado de lo inferido. No hay confianza numérica.

### `POST /v1/summaries`: resumen del incidente

El backend envía **todas** las alertas del incidente y **todos** los análisis vigentes, reenviando sin cambios el objeto `analysis` que guardó. Ningún archivo se vuelve a leer.

```json
{
  "incidentId": 3,
  "alerts": [
    {"alertId": 7, "reportedAt": "…", "description": null, "affectedCount": null, "reporterIsPatient": false},
    {"alertId": 8, "reportedAt": "…", "description": "Hay alguien atrapado", "affectedCount": 3}
  ],
  "evidences": [
    {"evidenceId": 12, "alertId": 7, "modality": "image", "receivedAt": "…", "analysis": { "...": "..." }}
  ]
}
```

La respuesta `200` trae `summary` (`summary`, `eventType`, `people`, `hazards`, `findings`, `risks`, `severity`, `conflicts`, `limitations`), `usedEvidenceIds`, `usedAlertIds` y `provenance`. Cada hallazgo, riesgo o contradicción incluye `evidenceIds`, `alertIds` y `corroboratingAlerts`, que es la cantidad de alertas distintas que lo respaldan. Ese número lo calcula el código, no el modelo, y solo con las citas válidas. Si el modelo cita una fuente que no recibió (una evidencia desconocida o una alerta sin descripción ni cantidad), esa cita se descarta; si una afirmación se queda sin ninguna fuente válida, se descarta la afirmación. El resto del resumen se conserva y el servicio registra cuántas citas y afirmaciones descartó, sin su contenido. La respuesta se rechaza con `invalid_model_output` solo si no sirve como resumen (por ejemplo, `summary` vacío o un JSON que no cumple el esquema).

`usedEvidenceIds` y `usedAlertIds` son las fuentes que recibió la síntesis, todas válidas porque el pedido se valida al entrar; no dependen de qué citó el modelo.

- Con una sola evidencia y sin texto en las alertas, el resumen se arma sin llamar al modelo (`method: "single_evidence"`).
- La síntesis no recibe el resumen anterior: depende solo de sus fuentes, así que el orden de llegada no la altera.
- **Regla para el backend:** guardar la propuesta como nueva versión solo si `usedEvidenceIds` incluye todas las evidencias de la versión vigente y agrega alguna. Una propuesta que llega tarde nunca reemplaza a una más completa.

### Errores

Todos tienen la forma `{"errorCode": "...", "retryable": true|false}`.

| Estado | Códigos | Qué hace el backend |
|---|---|---|
| 401 | `unauthorized` | Revisar el token. |
| 422 | `invalid_request`, `unsupported_media_type`, `evidence_empty`, `evidence_too_large`, `mime_mismatch`, `checksum_mismatch`, `duration_exceeded`, `unreadable_media`, `object_not_found`, `download_failed`, `invalid_download_url`, `unknown_alert`, `duplicate_source`, `nothing_to_summarize`, `too_many_sources` | Fallo definitivo de la evidencia o de la solicitud: no reintentar. |
| 503 | `download_unavailable`, `download_forbidden` (URL vencida: firmar una nueva), `provider_unavailable`, `invalid_model_output` | Reintentar con espera creciente y un límite de intentos. |
| 502 / 503 | `provider_rejected` / `provider_misconfigured`, `service_not_configured` | No reintentar automáticamente; requiere revisión. |
| 500 | `internal_error` | Reintentar con límite. |

### Tiempos

Cada descarga y cada llamada al proveedor se intenta como máximo dos veces, con un timeout por intento (`AI_DOWNLOAD_TIMEOUT_SECONDS`=20, `AI_PROVIDER_TIMEOUT_SECONDS`=90). Para audio y video se suman los procesos de ffprobe/ffmpeg, cada uno con `AI_FFMPEG_TIMEOUT_SECONDS`=10: normalmente tardan uno o dos segundos, pero en el peor caso agregan unos 40 s. El peor caso total es de unos 265 s. El timeout de lectura del cliente en el backend debe ser mayor (por ejemplo, 280 s), y la URL temporal debe durar al menos ese tiempo. `AI_MAX_CONCURRENT_MODEL_CALLS` limita las llamadas simultáneas al proveedor.

## Pruebas

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q app tests scripts
```

`scripts/live_smoke.py` es la prueba real opcional: analiza el objeto de `AI_SMOKE_DOWNLOAD_URL` (con `AI_SMOKE_SHA256` y `AI_SMOKE_MIME_TYPE`) y luego sintetiza un incidente de dos alertas con OpenRouter. El backend prepara y limpia el objeto. Se ejecuta con `.venv/bin/python -m scripts.live_smoke`.
