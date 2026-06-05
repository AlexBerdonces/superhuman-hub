---
name: actualizar-newsletter-hub
description: Actualizar Newsletter Hub con nuevas noticias y prompts de SuperHuman y The Rundown AI
---

Eres un asistente que actualiza el Newsletter Hub del usuario Alex (aberdonces@gmail.com).

## QUÉ TIENES QUE HACER

Dos tareas en cada ejecución:
1. Añadir las newsletters nuevas (noticias + prompts) de AMBAS newsletters
2. Aplicar time-decay y boosts de relevancia cruzada a las noticias existentes

Archivos del proyecto:
- `C:\Users\aberd\OneDrive\Escritorio\Claude Cowork\Superhuman\SuperHuman\newsletter_news.json`
- `C:\Users\aberd\OneDrive\Escritorio\Claude Cowork\Superhuman\SuperHuman\newsletter_news.html`

---

## PASO 1 — Buscar emails nuevos sin leer

Usa Gmail para buscar ambas newsletters:
1. `from:superhuman@mail.joinsuperhuman.ai is:unread`
2. `from:news@daily.therundown.ai is:unread`

Si no hay emails sin leer en ninguna de las dos, salta directamente al PASO 5 (time-decay).

---

## PASO 2 — Leer cada email

Usa `get_thread` para cada thread encontrado. Procesa los archivos resultantes con Python.

---

## PASO 3 — Extraer NOTICIAS nuevas

Por cada newsletter, extrae todas las noticias (1 principal + 2-3 secundarias). Por cada noticia:

```json
{
  "id": "news_XXX",
  "tipo": "noticia",
  "titulo": "Título en español (máx 10 palabras)",
  "resumen": "2-3 frases en español, claras y directas",
  "categoria": "Inteligencia Artificial | Robótica | Tecnología | Espacio y Aviación | Salud y Ciencia | Negocios e Impacto Económico | Ciencia y Matemáticas | Política y Regulación",
  "puntuacion": 0-100,
  "fecha": "YYYY-MM-DD",
  "fuente_asunto": "Subject del email",
  "url": "URL del artículo original o null",
  "newsletter": "SuperHuman"
}
```

**CRÍTICO:** el campo `newsletter` debe ser el nombre EXACTO de la fuente del email que estás procesando:
- Emails de superhuman@mail.joinsuperhuman.ai → `"newsletter": "SuperHuman"`
- Emails de news@daily.therundown.ai → `"newsletter": "The Rundown AI"`

Criterios de puntuación inicial:
- 80-100: cambios estructurales en empleo/economía global, AGI, robótica humanoide autónoma, OPIs masivas
- 50-79: lanzamientos de modelos relevantes, nuevas capacidades AI, acuerdos corporativos importantes
- 20-49: productos de nicho, actualizaciones menores, curiosidades tecnológicas
- 0-19: noticias de impacto marginal

Para URLs: buscar links a artículos reales (theverge.com, techcrunch.com, bloomberg.com, wired.com, reuters.com, bbc.com, etc.). Ignorar links de tracking/unsubscribe.

---

## PASO 3b — Relevancia cruzada (CRÍTICO)

Al extraer cada noticia nueva, comparar con los títulos y resúmenes de los items EXISTENTES en el JSON:
- Si es seguimiento directo (mismo tema, misma empresa, mismo evento): **+10 puntos** al item existente (máx 95)
- Si es relación indirecta (mismo sector, mismo actor en contexto diferente): **+5 puntos** al item existente (máx 95)

Anotar en el resumen final qué items existentes fueron boosteados y por qué.

---

## PASO 4 — Extraer PROMPTS nuevos

Cada newsletter tiene una sección con tutoriales prácticos de AI:
- SuperHuman: sección "ALSO: How to..."
- The Rundown AI: sección "PLUS: ..."

```json
{
  "id": "prompt_XXX",
  "tipo": "prompt",
  "titulo": "Título descriptivo en español",
  "herramienta": "ChatGPT | Claude | Gemini | Grok | Perplexity | ...",
  "puntuacion": 0-100,
  "fecha": "YYYY-MM-DD",
  "fuente_asunto": "Subject del email",
  "newsletter": "SuperHuman",
  "etiquetas": ["Productividad", "Escritura", "Análisis", ...],
  "prompt_completo": "El prompt completo traducido al español",
  "desglose": {
    "rol": "...", "contexto": "...", "instruccion": "...",
    "cadena_pensamiento": "...", "formato": "...", "restricciones": "..."
  }
}
```

**CRÍTICO:** el campo `newsletter` debe ser el nombre exacto de la fuente (igual que en el PASO 3).

Criterios puntuación prompts:
- 80-100: muy reutilizable, alto impacto productividad diaria
- 50-79: útil para casos específicos relevantes
- 20-49: nicho o uso ocasional

---

## PASO 5 — Time-decay automático

Recorrer TODOS los items existentes de tipo "noticia" y aplicar:
- Items con fecha < 30 días desde hoy: sin cambio
- Items con fecha 30-60 días y puntuación < 75: **−2 puntos**
- Items con fecha > 60 días y puntuación < 75: **−3 puntos**
- Items con puntuación ≥ 75: sin decay (son hitos estructurales)
- Suelo absoluto: nunca bajar de 10

Los prompts NO reciben decay. Anotar cuántos items recibieron decay en el resumen final.

---

## PASO 6 — Actualizar el JSON

1. Leer `newsletter_news.json`
2. Aplicar time-decay (PASO 5) a los items existentes
3. Aplicar boosts de relevancia cruzada (PASO 3b) a los items existentes
4. Añadir los nuevos items al array `noticias` (NUNCA borrar los existentes)
5. Generar IDs únicos continuando la numeración existente
6. Actualizar `total_noticias` y `ultima_actualizacion` (fecha de hoy)
7. Guardar el JSON

---

## PASO 7 — Actualizar el HTML

1. Leer `newsletter_news.html`
2. Reemplazar `const RAW_DATA = {...};` con el JSON actualizado usando este patrón Python EXACTO (crítico: usar lambda para evitar que re.sub interprete los \n del JSON como saltos de línea reales):
```python
import json, re
pattern = r'const RAW_DATA = \{.*?\};'
replacement = f'const RAW_DATA = {json_str};'
new_html = re.sub(pattern, lambda m: replacement, html, flags=re.DOTALL)
```
3. Copiar `newsletter_news.html` como `index.html`:
```python
import shutil
shutil.copy(html_path, html_path.replace('newsletter_news.html', 'index.html'))
```
4. Guardar el HTML
5. Validar con Node.js que no hay errores de sintaxis:
```
node -e "const fs=require('fs');const h=fs.readFileSync('...newsletter_news.html','utf8');const s=h.slice(h.indexOf('<script>')+8,h.lastIndexOf('</script>'));try{new Function(s);console.log('OK')}catch(e){console.log('ERROR:',e.message)}"
```
6. Si hay error, depurarlo antes de continuar.

---

## PASO 8 — Publicar en GitHub

Repositorio: `https://github.com/AlexBerdonces/superhuman-hub.git`
Token: leer de `C:\Users\aberd\OneDrive\Escritorio\Claude Cowork\Superhuman\SuperHuman\.github_token.txt`

```bash
cd /sessions/*/mnt/SuperHuman
TOKEN=$(cat .github_token.txt | tr -d '[:space:]')
git config user.email "aberdonces@gmail.com"
git config user.name "AlexBerdonces"
git remote set-url origin "https://${TOKEN}@github.com/AlexBerdonces/superhuman-hub.git"
git add index.html newsletter_news.json
git commit -m "Deploy: actualización $(date +%Y-%m-%d)"
git push origin main
# Limpiar token de la URL del remote
git remote set-url origin "https://github.com/AlexBerdonces/superhuman-hub.git"
```

Si git push falla por `index.lock`, eliminarlo con: `rm -f .git/index.lock` y reintentar.

---

## PASO 9 — Marcar emails como leídos

Usa `unlabel_thread` con `labelIds: ["UNREAD"]` para cada thread procesado en este run (tanto SuperHuman como The Rundown AI).

---

## PASO 10 — Presentar resultado

Usa `present_files` para mostrar `newsletter_news.html`.

Indica en el mensaje final:
- Cuántas noticias nuevas añadidas por newsletter (o "ninguna" si no había emails sin leer)
- Cuántos prompts nuevos añadidos
- Cuántos items boosteados por relevancia cruzada (con sus títulos)
- Cuántos items recibieron time-decay
- Total acumulado en el sistema
- Confirmación de push a GitHub (OK o error)
