# Prueba de IA local — inventario por voz

Equipo: 11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz, 15 GB de RAM, sin GPU dedicada.

## Voz a texto (Whisper, CPU int8) + intérprete por reglas

| Modelo | RAM | Tiempo por frase (~2,4 s de audio) | Orden correcta (limpio) | Orden correcta (ruido) |
|---|---|---|---|---|
| base | 420 MB | 0.39 s (máx 1.13 s) | 39/60 (65 %) | 39/60 (65 %) |
| small | 1216 MB | 1.18 s (máx 3.04 s) | 53/60 (88 %) | 48/60 (80 %) |
| large-v3-turbo | 2384 MB | 4.0 s (máx 8.43 s) | 54/60 (90 %) | 53/60 (88 %) |

Respuesta hablada (Piper): 57 ms por frase.

## LLM local (llama.cpp, cuantizado Q4)

| Modelo | Motor | RAM | Carga | Respuesta típica | Tokens/s | Frases normales | Frases difíciles | Transcripciones reales |
|---|---|---|---|---|---|---|---|---|
| qwen2.5-1.5b | CPU | 719 MB | 1.2 s | 1.36 s (máx 1.67 s) | 32.5 | 18/20 | 2/6 | 37/64 |
| qwen2.5-1.5b | Iris Xe (Vulkan) | 1392 MB | 1.8 s | 2.12 s (máx 2.71 s) | 20.6 | 18/20 | 3/6 | 36/64 |
| qwen2.5-3b | CPU | 1514 MB | 2.0 s | 2.62 s (máx 3.28 s) | 16.1 | 19/20 | 6/6 | 42/64 |
| qwen2.5-3b | Iris Xe (Vulkan) | 2371 MB | 1.2 s | 3.71 s (máx 4.4 s) | 11.3 | 17/20 | 6/6 | 40/64 |
| qwen3-4b | CPU | 2040 MB | 3.0 s | 2.73 s (máx 3.23 s) | 13.3 | 20/20 | 6/6 | 42/64 |
| qwen3-4b | Iris Xe (Vulkan) | 3398 MB | 3.0 s | 3.94 s (máx 4.59 s) | 8.8 | 20/20 | 6/6 | 42/64 |

## Híbrido sobre Whisper small (reglas → LLM cuando las reglas tienen confianza baja)

| LLM de respaldo | Limpio | Ruido | Frases que pasaron al LLM | Frases difíciles (texto) |
|---|---|---|---|---|
| qwen2.5-1.5b (cpu) | 54/60 (90 %) | 52/60 (87 %) | 19 de 120 | 4/6 |
| qwen2.5-1.5b (vulkan) | 54/60 (90 %) | 51/60 (85 %) | 19 de 120 | 5/6 |
| qwen2.5-3b (cpu) | 54/60 (90 %) | 52/60 (87 %) | 19 de 120 | 6/6 |
| qwen2.5-3b (vulkan) | 54/60 (90 %) | 52/60 (87 %) | 19 de 120 | 6/6 |
| qwen3-4b (cpu) | 53/60 (88 %) | 50/60 (83 %) | 19 de 120 | 6/6 |
| qwen3-4b (vulkan) | 53/60 (88 %) | 50/60 (83 %) | 19 de 120 | 6/6 |
