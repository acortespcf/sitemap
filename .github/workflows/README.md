# Self-hosted runner

Este workflow corre en un runner self-hosted con la etiqueta `sitemap`.

## Como registrar el runner
1) Crear un runner en GitHub (Settings > Actions > Runners).
2) Asignar la etiqueta `sitemap`.
3) Levantar el servicio del runner en el host.

## Concurrencia
El workflow usa `concurrency` para evitar ejecuciones simultaneas del mismo sitemap.
Si necesitas mas paralelismo, levanta mas runners con la misma etiqueta y ejecuta otros workflows en paralelo.
