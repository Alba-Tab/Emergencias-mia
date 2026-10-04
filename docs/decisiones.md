# Decisiones del análisis y del resumen

Registro de las decisiones internas de `emergencias-mia` y de su porqué.

- Las decisiones sobre el flujo, los avisos y las pantallas están en [`emergencias-backend/docs/decisiones.md`](../../emergencias-backend/docs/decisiones.md).
- El contrato exacto está en el [README](../README.md).

Cada decisión responde a tres preguntas:

- ¿Así se trabaja en una emergencia real?
- ¿Se puede defender?
- ¿Es fácil de cambiar después?

**Estados:**

- **Decidida:** se aplica o se va a aplicar.
- **Propuesta:** está diseñada y falta confirmarla con el equipo.
- **Abierta:** todavía está en discusión.

| # | Decisión | Estado | Fecha |
|---|---|---|---|
| M1 | Servicio sin estado; síntesis desde todas las fuentes | Decidida | 2026-10-02 |
| M2 | Prompt `summary_v2`: datos de la emergencia en palabras simples | Decidida | 2026-10-04 |
| M3 | Un peligro no desaparece sin motivo | Decidida | 2026-10-04 |
| M4 | Campo `keyPoints` para el paramédico: frases cortas, estilo radio | Decidida | 2026-10-04 |
| M5 | "Persona atrapada" en la lista de peligros | Propuesta | 2026-10-04 |
| M6 | Indicar si una evidencia sirve | Propuesta | 2026-10-04 |
| M7 | La gravedad se mantiene, con motivo obligatorio | Decidida | 2026-10-02 |
| M8 | El análisis de video se conserva, pero no se usa | Decidida | 2026-10-04 |
| M9 | Privacidad con el proveedor: limitar, informar o ambas | Abierta | — |
| M10 | Estado emocional | Abierta | — |

## M1. Servicio sin estado; síntesis desde todas las fuentes

**Decidida, 2026-10-02.**

- **Decisión.**
  - `POST /v1/analyses` analiza cada evidencia una sola vez.
  - `POST /v1/summaries` recibe todas las alertas y todos los análisis guardados, pero no el resumen anterior.
  - Si hay una sola evidencia y ninguna alerta tiene texto, no se llama al modelo.
- **Por qué.**
  - El resultado no depende del orden en que llegan las evidencias.
  - El backend puede aceptar una versión nueva solo si cubre todo lo que cubría la anterior.
  - Un error de una versión no se arrastra a las siguientes.
  - Volver a sintetizar unos pocos KB de texto cuesta poco; lo caro es analizar archivos.
- **Descartado.** Un resumen incremental que parta del anterior.

## M2. Prompt `summary_v2`

**Decidida, 2026-10-04.**

- **Decisión.** El resumen se centra en:
  - qué pasó y dónde;
  - cuántas personas hay y cómo está cada una (consciente, sangra, atrapada, no se mueve);
  - qué peligros siguen activos.
- **Reglas de redacción.**
  - Usar palabras simples. No usar "IA", "modelo", "fotograma", "transcripción" ni "evidencia".
  - No repetir lo que dijo cada persona ni copiar transcripciones.
  - Si las fuentes se contradicen, decirlo en una frase, por ejemplo: "no está claro si son 2 o 3 heridos".
- **Por qué.**
  - Al paramédico le sirven los datos de la emergencia, no el análisis.
  - `summary_v1` no prohíbe la jerga, y algunas limitaciones llegan al paramédico con frases técnicas.
- **También.** Quitar la jerga de los textos que agrega el código (`app/application/use_cases/analyze_evidence.py`) y de los prompts de imagen y audio.
- **Cómo se cambia.** Los prompts están versionados (`app/prompts/summary_v<n>.txt`) y cada resumen guarda la versión que usó, así que se puede volver a `v1`.

## M3. Un peligro no desaparece sin motivo

**Decidida, 2026-10-04.**

- **Problema.**
  - Al rehacer el resumen, el modelo puede omitir un peligro que mencionó una evidencia anterior. Por ejemplo, el humo aparece en la versión 2 y falta en la 3.
  - Nada lo detecta, porque `hazards` es lo que devuelve el modelo (`app/application/use_cases/synthesize_summary.py`).
- **Decisión.**
  1. El modelo devuelve por separado los peligros activos y los resueltos. Para cada peligro resuelto cita la fuente que lo dice, por ejemplo "ya apagaron el fuego".
  2. El código junta todos los peligros de los análisis recibidos. Si alguno no aparece ni entre los activos ni entre los resueltos, lo agrega a los activos con la hora de la última mención, por ejemplo: "humo, último reporte 10:32".
  3. Para los riesgos en texto libre, como "conductor atrapado", se agrega una regla al prompt: no quitarlos salvo que una fuente posterior diga que se resolvieron.
- **Por qué.**
  - Que un peligro desaparezca sin que nadie diga que se resolvió es un error peligroso.
  - La regla se calcula a partir de las fuentes, así que el servicio sigue sin estado.
- **Esquema.** Cambia la forma de `hazards`. El cambio va junto con M4 en `incident-summary.v2`.

## M4. Campo `keyPoints`

**Decidida, 2026-10-04.**

- **Decisión.**
  - El resumen agrega `keyPoints` además de `summary`: hasta 4 frases de unos 90 caracteres como máximo, cada una con su tipo.
  - `keyPoints` es para el paramédico (tarjeta y voz) y `summary` es para el panel.
  - Las frases siguen el estilo de radio: cortas, en presente, con el dato primero y sin "se observa" ni "según las fuentes".
  - La app agrega el encabezado al leerlas, según el tipo: "Atención, unidad 12", "Precaución:" antes de un peligro y "Actualización:" en los cambios. Así el mismo texto sirve en pantalla y en voz.

  ```json
  "keyPoints": [
    {"kind": "what",     "text": "Choque de dos motos en la esquina del mercado."},
    {"kind": "people",   "text": "Dos heridos; uno no se mueve."},
    {"kind": "hazard",   "text": "Sale humo de un auto."},
    {"kind": "critical", "text": "Conductor atrapado, podría estar inconsciente."}
  ]
  ```

- **Por qué un campo aparte.**
  - La tarjeta del paramédico y la lectura en voz necesitan justo esto. Pedir un párrafo y cortarlo en la app es frágil.
  - El tipo permite ordenar las frases, ponerles un ícono y leerlas en voz siempre en el mismo orden.
  - El código puede hacer cumplir el máximo de 4 frases y su largo, como ya hace con las demás listas.
- **Por qué 90 caracteres.** Es una línea en el teléfono y unos 5 segundos leída en voz alta.
- **No hace falta cambiar la base de datos.**
  - El backend guarda el resumen tal cual en una columna JSON (`resumen_incidente.resumen`) y lo devuelve sin interpretarlo, así que el campo nuevo llega a las apps sin cambios en el backend.
  - `schemaVersion` pasa a `incident-summary.v2`.
  - Las apps leen `keyPoints`. En las versiones viejas, que no tienen el campo, muestran `summary`.
- **`summary` se mantiene**, porque lo usa el panel.

## M5. "Persona atrapada" en la lista de peligros

**Propuesta, 2026-10-04.**

- **Propuesta.** Agregar `entrapment` al vocabulario cerrado de `hazards`.
- **Por qué.**
  - Hoy no existe: solo puede aparecer como texto libre o como `other`.
  - Es uno de los datos que más cambian el trabajo de la tripulación, porque implica rescate y más tiempo en el lugar.

## M6. Indicar si una evidencia sirve

**Propuesta, 2026-10-04.**

- **Propuesta.** El análisis de cada evidencia agrega dos campos:
  - `usable`: sí o no.
  - `unusableReason`, de una lista cerrada: `no_emergency_content`, `too_dark`, `too_blurry`, `silent`, `no_useful_speech`.
- **Cómo se decide.**
  1. Primero, sin usar el modelo: se detecta el audio en silencio (ya existe) y, si se agrega, la imagen casi negra o de un solo color.
  2. Después, en la misma llamada al modelo que ya analiza la evidencia. No se agrega otra llamada.
- **Regla.** Ante la duda, `usable: true`. Una foto oscura de un choque de noche igual puede servir.
- **Por qué.** La IA indica si la evidencia sirve y el backend decide qué hacer con ella (B5). Así el servicio sigue sin estado.
- **Hoy.** El audio en silencio vuelve como un análisis normal (`method: silent_audio`), se guarda como analizado y entra al resumen. Con esta decisión pasaría a `usable: false`.

## M7. La gravedad se mantiene, con motivo obligatorio

**Decidida, 2026-10-02.**

- **Decisión.**
  - `severity` se mantiene, con los valores `low`, `moderate`, `high` y `undetermined`.
  - Si no tiene motivo, el código la cambia a `undetermined`.
  - No hay prioridad de triaje ni equipo sugerido.
- **Por qué.**
  - Orienta a la tripulación como lo hace la prioridad que da una central.
  - El motivo obligatorio evita una gravedad sin fundamento.
- **Cómo se muestra.** Ver la decisión B11 del backend.

## M8. El análisis de video se conserva, pero no se usa

**Decidida, 2026-10-04.**

- **Decisión.** El análisis de video (fotogramas con ffmpeg y audio) queda en el código y en las pruebas, pero en esta entrega no llegan videos (decisión B2 del backend).
- **Por qué.**
  - Ya está hecho y probado, y quitarlo no ahorra nada.
  - Volver a activarlo es solo un cambio de configuración.

## M9. Privacidad con el proveedor

**Abierta.**

- **Hoy.** Las fotos y los audios se envían a OpenRouter sin ninguna opción que limite lo que guarda el proveedor (`app/adapters/outbound/providers/openrouter_model.py`).
- **Opciones.**
  - **Limitar.** Pedirle a OpenRouter que use solo proveedores que no guardan datos (`provider.data_collection: "deny"`; hay que confirmarlo en su documentación). Puede reducir los modelos disponibles.
  - **Informar.** Decir en la nota al ciudadano (decisión B6 del backend) y en el aviso de privacidad que un servicio externo analiza los archivos.
  - **Hacer ambas.**
- **A considerar.**
  - Son datos de salud de terceros: la persona herida no aceptó nada.
  - La decisión tiene que coincidir con la parte legal del documento del proyecto.

## M10. Estado emocional

**Abierta.**

- **Hoy.** No hay un campo para esto. El prompt de audio guarda el estado emocional de quien habla (agitación, llanto, dificultad para hablar) como una observación inferida.
- **A favor de una tarjeta.** Estaba en el prototipo y puede indicar urgencia.
- **En contra.** Es inferido y poco confiable. Además, los nervios de un testigo no dicen cómo está el paciente.
- **Criterio sugerido.**
  - Usarlo solo cuando quien habla es el paciente (`reporterIsPatient`). En ese caso, "habla con dificultad" es un dato clínico.
  - El estado emocional de los testigos queda como una observación más.
