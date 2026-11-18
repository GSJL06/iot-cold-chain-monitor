# ADR 0003 — Prácticas de ingeniería

| | |
|---|---|
| Estado | Aceptada |
| Fecha | 2026-11-18 |
| Relacionada | ADR 0001, ADR 0002 |

## Contexto

Proyecto individual de portafolio, 4–6 semanas a 8–10 h/semana, repositorio público. Las prácticas deben ser visibles para un revisor externo (CI verde, historial limpio, seguridad básica) sin consumir el tiempo que necesita el código.

## Decisión

1. **Flujo:** `main` protegido; todo cambio entra por PR con squash merge. Conventional Commits y SemVer (0.x hasta el MVP).
2. **CI:** GitHub Actions con `lint` y `test` obligatorios y `audit` informativo. Dependabot semanal agrupado.
3. **Calidad local:** ruff y pre-commit, con el mismo conjunto de reglas que el CI.
4. **Seguridad:** secret scanning con push protection, `pip-audit`, reglas `S` de ruff, modelo de amenazas STRIDE y checklist de endurecimiento para la Fase 4.
5. **Observabilidad:** logs JSON, healthchecks y un dashboard de salud del pipeline calculado en SQL. Sin Prometheus en v1.
6. **Entrega:** GitHub Releases manuales. Sin despliegue continuo.

## Consecuencias

**A favor**

- Historial legible y cada PR verificable con su resultado de CI.
- Las mismas comprobaciones en local y en CI: pocas sorpresas al subir.
- La salud del pipeline se demuestra con las mismas consultas SQL que sostienen los objetivos medibles.

**En contra**

- Cada cambio exige rama y PR, incluso trabajando solo.
- Sin Prometheus no hay métricas de proceso (CPU, memoria) ni alertas sobre el propio pipeline más allá de las de datos.
- Releases manuales: dependen de acordarse de hacerlas.

## Alternativas descartadas

| Alternativa | Motivo |
|---|---|
| Git Flow (ramas `develop` y `release`) | Complejidad sin beneficio en un repositorio individual |
| Prometheus + Loki + Alertmanager | Tres servicios más para operar; no caben en el alcance |
| `release-please` o `semantic-release` | Automatización extra que se justifica con más de un colaborador |
| Pull requests con revisores obligatorios | No hay segundo revisor; el CI cumple ese papel |
