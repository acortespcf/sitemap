# Generador automatico de sitemap

Genera un sitemap XML combinando productos, categorias y paginas Modyo.

## Requisitos
- Python 3.10+
- requests

## Configuracion
1) Ajusta el archivo `config.json`:

```
cat config.json
```

2) Define la cookie de Modyo por variable de entorno (local o GitHub secret):

```
export MODYO_COOKIE="_pcfactory_session=..."
```

3) Ajusta reglas si necesitas filtros, prioridades o limites.

## Uso
```
python generate_sitemap.py --config config.json
```

Salida por defecto: `output/sitemap.xml` o multiples sitemaps + `output/sitemap_index.xml` si supera limites.

## Notas
- Las paginas Modyo solo se incluyen si `current_published=true` y `private=false`.
- Se eliminan duplicados por URL.
- Se remueven query params por defecto (`strip_query=true`).
- El generador evita reescribir si no detecta cambios (hash por URL).
- Opcionalmente copia los XMLs a `publish_dir`.

## GitHub Actions (self-hosted)
- Workflow en `.github/workflows/sitemap.yml`
- Runner con etiqueta `sitemap`
- Cron diario 05:00 UTC (ajustable)
- Publica en `gh-pages` para acceso publico (GitHub Pages)

## Dry run
```
python generate_sitemap.py --config config.json --dry-run
```

## Forzar generacion
```
python generate_sitemap.py --config config.json --force
```
