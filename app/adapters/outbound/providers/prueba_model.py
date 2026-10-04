"""Analizador de prueba: implementa `MultimodalModel` sin llamar a ningún proveedor.

Devuelve salidas fijas y válidas para cada esquema, de modo que el backend y las apps puedan
probar el flujo completo sin clave ni costo. La salida depende solo de la entrada recibida.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.application.pipelines.media import duration_seconds
from app.application.ports.multimodal_model import MediaPart, ModelReply, media_parts
from app.domain.errors import AiError
from app.domain.evidence_reference import Modality

PROVIDER = "prueba"
MODEL = "analizador-de-prueba"
LIMITATION = "Resultado del analizador de prueba: no proviene de un modelo real."
_SEVERITY_ORDER = ("low", "moderate", "high")


def _evidence(**specific: Any) -> dict[str, Any]:
    return {
        "eventType": "other",
        "peopleMin": 1,
        "peopleMax": 2,
        "risks": ["Posible riesgo para las personas cercanas."],
        "severity": "moderate",
        "limitations": [LIMITATION],
        **specific,
    }


def _image(parts: tuple[MediaPart, ...]) -> dict[str, Any]:
    size = sum(len(part.data) for part in parts)
    return _evidence(
        summary="Imagen de prueba con una escena de emergencia simulada.",
        hazards=["smoke"],
        observations=[
            {"text": f"Imagen recibida de {size} bytes.", "basis": "observed"},
            {"text": "Se observa humo en la escena simulada.", "basis": "observed"},
            {"text": "Podría haber personas cerca del humo.", "basis": "inferred"},
        ],
        severityBasis=["Humo visible en la escena simulada."],
    )


def _audio(parts: tuple[MediaPart, ...]) -> dict[str, Any]:
    return _evidence(
        summary="Audio de prueba en el que quien habla pide ayuda.",
        hazards=["other"],
        observations=[
            {"text": "Quien habla indica que hay una persona herida.", "basis": "observed"},
            {"text": "Quien habla parece agitado.", "basis": "inferred"},
        ],
        severityBasis=["Quien habla indica que hay una persona herida."],
        transcript="Hola, soy [nombre], hay una persona herida acá, mi número es [número], vengan rápido.",
    )


def _video(parts: tuple[MediaPart, ...]) -> dict[str, Any]:
    frames = [part for part in parts if part.second is not None]
    if frames:  # video por fotogramas: la línea de tiempo usa solo los segundos recibidos
        timeline = [{"startSecond": frame.second, "text": f"Fotograma de prueba {position}."}
                    for position, frame in enumerate(frames, start=1)]
        has_audio = any(part.modality is Modality.AUDIO for part in parts)
        transcript = "Ayuda, hay humo, llamen a [nombre]." if has_audio else None
    else:
        seconds = next((duration_seconds(p.data, p.mime_type) for p in parts if p.modality is Modality.VIDEO), None)
        timeline = [{"startSecond": 0, "text": "Inicio del video de prueba."}]
        if seconds:
            timeline.append({"startSecond": round(seconds / 2, 2), "text": "Mitad del video de prueba."})
        transcript = "Ayuda, hay humo, llamen a [nombre]."
    heard = [{"text": "Se oye a una persona pidiendo ayuda.", "basis": "observed"}] if transcript else []
    return _evidence(
        summary="Video de prueba con una escena de emergencia simulada.",
        hazards=["smoke"],
        observations=[
            {"text": "Se observa humo durante el video simulado.", "basis": "observed"},
            *heard,
        ],
        severityBasis=["Humo visible durante el video simulado."],
        transcript=transcript,
        timeline=timeline,
    )


def _sources(text: str) -> dict[str, Any]:
    try:
        data = text.split("<fuentes>\n", 1)[1].rsplit("\n</fuentes>", 1)[0]
        document = json.loads(data)
    except (IndexError, ValueError) as exc:
        raise AiError("invalid_model_output", retryable=True) from exc
    return document


def _summary(text: str) -> dict[str, Any]:
    """Cita solo fuentes recibidas: evidencias y alertas con descripción o cantidad."""
    document = _sources(text)
    evidences = document.get("evidences") or []
    alerts = [
        alert for alert in document.get("alerts") or []
        if (alert.get("citizenDescription") or "").strip() or alert.get("reportedAffectedCount") is not None
    ]
    findings, risks = [], []
    for evidence in evidences:
        analysis = evidence["analysis"]
        observation = next(iter(analysis.get("observations") or []), None)
        findings.append({
            "text": observation["text"] if observation else analysis["summary"],
            "basis": observation["basis"] if observation else "inferred",
            "evidenceIds": [evidence["evidenceId"]], "alertIds": [],
        })
        for risk in (analysis.get("risks") or [])[:1]:
            risks.append({"text": risk, "evidenceIds": [evidence["evidenceId"]], "alertIds": []})
    for alert in alerts:
        findings.append({"text": f"La alerta {alert['alertId']} trae un reporte escrito del ciudadano.",
                         "basis": "inferred", "evidenceIds": [], "alertIds": [alert["alertId"]]})

    event_types = Counter(e["analysis"]["eventType"] for e in evidences)
    event_type = min(event_types, key=lambda kind: (-event_types[kind], kind)) if event_types else "undetermined"
    conflicts = []
    if len(event_types) > 1:
        conflicts.append({"text": "Las evidencias sugieren tipos de evento distintos.",
                          "evidenceIds": sorted(e["evidenceId"] for e in evidences), "alertIds": []})

    ranges = [e["analysis"]["people"] for e in evidences if e["analysis"].get("people")]
    counts = [a["reportedAffectedCount"] for a in alerts if a.get("reportedAffectedCount") is not None]
    minimum = max([r[0] for r in ranges] + counts, default=None)
    maximum = max([r[1] for r in ranges] + counts, default=None)

    graded = [(e["analysis"]["severity"], e["evidenceId"]) for e in evidences
              if e["analysis"]["severity"] in _SEVERITY_ORDER]
    if graded:
        level, evidence_id = max(graded, key=lambda item: (_SEVERITY_ORDER.index(item[0]), -item[1]))
        severity, severity_basis = level, [f"Gravedad más alta reportada en la evidencia {evidence_id}."]
    else:
        severity, severity_basis = "undetermined", []

    hazards = sorted({hazard for e in evidences for hazard in e["analysis"].get("hazards") or []})
    key_points = [{"kind": "what", "text": "Emergencia de prueba."}]
    if maximum is not None:
        key_points.append({"kind": "people", "text": f"Hasta {maximum} personas."})
    if hazards:
        key_points.append({"kind": "hazard", "text": f"Peligro de prueba: {hazards[0]}."})
    return {
        "summary": f"Resumen de prueba con {len(evidences)} evidencias y {len(alerts)} alertas con texto.",
        "keyPoints": key_points,
        "eventType": event_type,
        "peopleMin": minimum,
        "peopleMax": maximum,
        "hazards": hazards,
        "resolvedHazards": [],
        "findings": findings,
        "risks": risks,
        "severity": severity,
        "severityBasis": severity_basis,
        "conflicts": conflicts,
        "limitations": [LIMITATION],
    }


_BUILDERS = {"image_evidence": _image, "audio_evidence": _audio, "video_evidence": _video}


class PruebaModel:
    async def generate(
        self,
        *,
        instructions: str,
        text: str,
        media: MediaPart | Sequence[MediaPart] | None,
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelReply:
        if schema_name == "incident_summary":
            content = _summary(text)
        elif schema_name in _BUILDERS:
            content = _BUILDERS[schema_name](media_parts(media))
        else:
            raise AiError("provider_rejected")
        return ModelReply(content=content, provider=PROVIDER, model=MODEL)
