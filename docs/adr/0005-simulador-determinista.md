# ADR 0005 — Simulador determinista con generación separada de la publicación

| | |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-11-19 |
| Relacionada | ADR 0004, `docs/simulator-design.md` |

## Contexto

El simulador alimenta todo el pipeline y es la fuente de la verdad de referencia con la que se mide el motor de alertas (recall y precisión). Hace falta que los datos sean reproducibles, que 24 h se simulen en segundos y que la publicación en MQTT no condicione la lógica.

## Decisión

1. **Determinismo por semilla.** Un generador aleatorio por dispositivo, sembrado con `semilla:device_id`, con orden fijo de sorteos. Misma configuración, mismo archivo de salida byte a byte.
2. **Generar y publicar son capas distintas.** `generate` produce lecturas y ground truth sin red; `publish` envía lo generado al broker.
3. **Tiempo de simulación, no reloj del sistema.** Solo el inicio se toma del reloj real.
4. **Escenarios y verdad de referencia como datos.** `config/scenario_catalog.yaml` fija clase, reglas esperadas, prohibidas y permitidas; el evaluador de la Fase 4D lee el mismo archivo.
5. **Sin aceleración del tiempo al publicar.** Un `ts` futuro se rechaza por `clock_skew`. El histórico se publica con `ts` pasados (backfill).
6. **Plan de 24 h explícito para los fallos** y generado de forma determinista para los eventos benignos.
7. **Un corte de red eléctrica también deshabilita el compresor.** Es lo que ocurre en un corte real; por eso R01 es una consecuencia permitida (`allowed_rules`) de `power_outage`.

## Consecuencias

**A favor**

- Las pruebas son rápidas y exactas; el objetivo 5 de `architecture.md` se mide sin esperar 24 h.
- La misma configuración alimenta pruebas, demo y evaluación.
- El diseño se validó con un prototipo antes de escribir el código.

**En contra**

- Modelo físico simple: no sirve como evidencia de rendimiento en equipos reales.
- Mantener fleet.yaml y el seed SQL sincronizados exige un test (incluido en la fase).
- Sin aceleración al publicar, una demostración de 24 h en directo no es posible; se compensa con backfill.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Simulador en tiempo real únicamente | Una prueba de 24 h duraría 24 h |
| Datos públicos de sensores reales | Sin etiquetas de fallos ni control sobre los escenarios |
| NumPy o SimPy | Dependencias sin ganancia con un modelo de primer orden |
| Aceleración del tiempo al publicar | Choca con `clock_skew` y confunde las métricas de latencia |
