# Emergencias AI

Servicio Python para analizar evidencias de emergencias. El backend principal conserva las alertas, los incidentes, los archivos registrados, los análisis persistidos y el resumen oficial. Este servicio recibe una referencia autorizada, procesa la evidencia y devuelve un resultado individual.

## Estado actual

La API expone `GET /health`. Ya existe un caso de uso independiente de infraestructura para analizar **una imagen**, con puertos de lectura, análisis y entrega del resultado. Aún no hay endpoint de trabajos, adaptadores S3/Gemini/callback, fusión de resultados ni integración con el backend. Por ello, esta base arranca y sus reglas de aplicación se pueden probar, pero todavía no procesa imágenes reales de extremo a extremo.

Imagen, audio y video forman parte del alcance del proyecto. Se implementarán por etapas, comenzando con imagen. No se necesita una cola de mensajes para esta base; se reevaluará si la carga o los reintentos lo requieren.

## Arranque local

Desde esta carpeta:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m uvicorn app.main:app --reload
```

Consultar `http://127.0.0.1:8000/health` debe devolver `{"status":"ok","service":"emergencias-ai"}`. La documentación de los endpoints actuales está en `http://127.0.0.1:8000/docs`.

Para ejecutar las pruebas de la base:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

`.env.example` muestra la configuración no sensible. Copiarlo a `.env` solo si hay que cambiar los valores por defecto; `.env` está excluido de Git. No colocar credenciales reales en el ejemplo ni en el código.

## Organización y siguiente contrato

- `app/domain`: referencias y resultados propios de IA; no replica las entidades Java.
- `app/application`: caso de uso y puertos de capacidades; no importa FastAPI ni SDKs externos.
- `app/adapters`: entradas HTTP y futuras conexiones con almacenamiento, proveedor y backend.
- `app/core`: configuración transversal y composición de la aplicación.

Antes de agregar `POST /jobs`, acordar con el backend: `jobId`, `evidenceId`, `alertId`, `incidentId`, referencia privada al archivo, MIME, checksum, contexto mínimo, autenticación entre servicios y formato del callback. La API de trabajos debe responder `202 Accepted` solo cuando el trabajo quedó aceptado. El backend controlará idempotencia y versiones del resumen; un resultado repetido no debe duplicar el análisis.

Las decisiones del proyecto y la secuencia completa están en [`../docs/registros_reuniones_26_27_septiembre_2026.md`](../docs/registros_reuniones_26_27_septiembre_2026.md).
