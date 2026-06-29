"""
publish.py — Sistema de automatización LinkedIn × Superhuman Hub
Autor: Alejandro Berdonces
Uso:
  python publish.py              # publica en LinkedIn
  python publish.py --dry-run    # solo genera el post, no publica
"""
 
import json
import os
import re
import sys
import requests
import tempfile
from datetime import datetime, timedelta, timezone
from anthropic import Anthropic
from openai import OpenAI
from dotenv import load_dotenv
 
load_dotenv(encoding='utf-8-sig')
 
# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
 
HTML_URL = "https://raw.githubusercontent.com/AlexBerdonces/superhuman-hub/main/index.html"
JSON_URL = "https://raw.githubusercontent.com/AlexBerdonces/superhuman-hub/main/newsletter_news.json"  # fallback legacy
PUBLISHED_FILE = "published_ids.json"
MAX_AGE_DAYS = 10          # solo noticias de los últimos 10 días
TOPICS_COOLDOWN_DAYS = 60  # días de cooldown para no repetir temáticas
FORBIDDEN_PHRASES = ["Acabo de leer", "Es importante destacar", "En conclusión", "En resumen"]
 
# Variables de entorno (configura en .env o GitHub Secrets)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
LINKEDIN_ACCESS_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN")
LINKEDIN_PERSON_URN = os.environ.get("LINKEDIN_PERSON_URN")
 
 
# ─────────────────────────────────────────────
# PASO 1: CARGAR ESTADO (qué hemos publicado ya)
# ─────────────────────────────────────────────
 
def load_published_data() -> dict:
    """
    Carga el estado completo de publicaciones.
    Migra automáticamente el formato antiguo (lista de strings) al nuevo formato
    (lista de objetos {id, date, topics}).
    """
    if not os.path.exists(PUBLISHED_FILE):
        return {"published": [], "last_run": None}
    with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Migración: convertir strings del formato antiguo a objetos
    migrated = []
    needs_migration = False
    for item in data.get("published", []):
        if isinstance(item, str):
            migrated.append({"id": item, "date": None, "topics": []})
            needs_migration = True
        else:
            migrated.append(item)
    if needs_migration:
        print(f"⚙️  Migrado formato antiguo → nuevo ({len(migrated)} entradas)")
    data["published"] = migrated
    return data
 
 
def load_published_ids() -> set:
    """Devuelve el set de IDs ya publicados."""
    data = load_published_data()
    return {item["id"] for item in data["published"]}
 
 
def get_recent_topics(days: int = TOPICS_COOLDOWN_DAYS) -> list:
    """
    Devuelve los topics publicados en los últimos N días.
    Se usa para evitar repetir temáticas en el período de cooldown.
    """
    data = load_published_data()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent_topics = []
    for item in data["published"]:
        if not item.get("date") or not item.get("topics"):
            continue
        try:
            pub_date = datetime.fromisoformat(item["date"])
            if pub_date > cutoff:
                recent_topics.extend(item["topics"])
        except (ValueError, TypeError):
            continue
    if recent_topics:
        print(f"🏷️  Topics en cooldown ({days}d): {list(set(recent_topics))}")
    return recent_topics
 
 
def save_published_id(news_id: str, topics: list = None):
    """Guarda un ID publicado junto con su fecha y topics temáticos."""
    data = load_published_data()
    data["published"].append({
        "id": news_id,
        "date": datetime.now(timezone.utc).isoformat(),
        "topics": topics or []
    })
    data["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ Guardado {news_id} con topics: {topics or []}")
 
 
# ─────────────────────────────────────────────
# PASO 2: OBTENER Y FILTRAR NOTICIAS
# ─────────────────────────────────────────────
 
def fetch_news() -> list:
    """
    Obtiene noticias desde index.html (fuente principal, siempre actualizada).
    Fallback: newsletter_news.json (fuente legacy).
    """
    # Fuente principal: index.html con RAW_DATA embebido
    try:
        response = requests.get(HTML_URL, timeout=15)
        response.raise_for_status()
        html = response.text
 
        idx = html.find('const RAW_DATA = ')
        if idx == -1:
            raise ValueError("No se encontró RAW_DATA en index.html")
 
        json_str = html[idx + len('const RAW_DATA = '):]
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(json_str)
 
        noticias = data.get("noticias", [])
        print(f"📊 Fuente: index.html — {len(noticias)} items (actualización: {data.get('ultima_actualizacion', '?')})")
        return noticias
 
    except Exception as e:
        print(f"⚠️  Error cargando index.html ({e}). Usando newsletter_news.json como fallback...")
 
    # Fallback: newsletter_news.json
    response = requests.get(JSON_URL, timeout=10)
    response.raise_for_status()
    data = response.json()
    noticias = data.get("noticias", [])
    print(f"📊 Fuente: newsletter_news.json (fallback) — {len(noticias)} items")
    return noticias
 
 
def filter_news(noticias: list, published_ids: set) -> list:
    """
    Filtra noticias de los últimos 10 días no publicadas aún.
    Sin filtro de puntuación — Claude evaluará cuál es más relevante para turismo.
    """
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=MAX_AGE_DAYS)
    candidates = []
 
    for n in noticias:
        # Solo noticias, no prompts
        if n.get("tipo") != "noticia":
            continue
        # Ya publicada
        if n["id"] in published_ids:
            continue
        # Solo últimos 10 días
        try:
            fecha = datetime.strptime(n["fecha"], "%Y-%m-%d").date()
            if fecha < cutoff:
                continue
        except (ValueError, KeyError):
            continue
 
        candidates.append(n)
 
    return candidates
 
 
def filter_by_topics(candidates: list, recent_topics: list) -> list:
    """
    Excluye candidatos que compartan temática con posts publicados en los últimos 60 días.
    Usa text matching contra título+resumen de la noticia.
    Safety net: si todos quedan filtrados, devuelve la lista completa para no bloquear el sistema.
    """
    if not recent_topics:
        return candidates
 
    recent_topics_lower = [t.lower() for t in recent_topics]
    filtered = []
 
    for noticia in candidates:
        text = (noticia.get("titulo", "") + " " + noticia.get("resumen", "")).lower()
        has_overlap = False
        matched_topic = None
 
        for topic in recent_topics_lower:
            # Buscar todas las palabras del topic en el texto
            topic_words = topic.split()
            if topic_words and all(w in text for w in topic_words):
                has_overlap = True
                matched_topic = topic
                break
 
        if has_overlap:
            print(f"⏭️  [{noticia['id']}] omitido — temática reciente: '{matched_topic}'")
        else:
            filtered.append(noticia)
 
    if not filtered:
        print(f"⚠️  Todos los candidatos tienen temáticas en cooldown. Safety net: usando todos.")
        return candidates
 
    print(f"✅ Filtro de temáticas: {len(filtered)}/{len(candidates)} candidatos disponibles")
    return filtered
 
 
def select_best_for_tourism(candidates: list, client: Anthropic) -> dict:
    """
    Usa Claude para seleccionar la noticia con más potencial de impacto en turismo.
    """
    if len(candidates) == 1:
        return candidates[0]
 
    noticias_texto = "\n".join([
        f"{i+1}. [{n['id']}] {n['titulo']} — {n.get('categoria','')}\n   {n['resumen'][:150]}"
        for i, n in enumerate(candidates)
    ])
 
    prompt = f"""Eres un experto en turismo y travel tech. Analiza estas noticias recientes y selecciona la que tenga más potencial para un post de LinkedIn dirigido a profesionales del turismo, hotelería, agencias de viajes y travel tech.
 
Criterios de selección:
- ¿Impacta directamente al sector turístico o puede conectarse con él de forma clara?
- ¿Es relevante para PMs, directivos o emprendedores en turismo?
- ¿Tiene un ángulo interesante aunque no sea de IA (puede ser robótica, política, economía, etc.)?
- Preferir noticias con impacto práctico sobre noticias puramente financieras
 
Noticias disponibles:
{noticias_texto}
 
IMPORTANTE: Responde SOLO con el ID exacto de la noticia elegida (el texto entre corchetes, ejemplo: "news_042") y en la siguiente línea una frase corta explicando por qué. No escribas nada antes del ID."""
 
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
 
    respuesta = message.content[0].text.strip()
    lineas = [l.strip() for l in respuesta.split("\n") if l.strip()]
 
    # Buscar el ID de noticia (news_NNN) en la respuesta
    match = re.search(r'news_\d+', respuesta)
    if match:
        news_id = match.group(0)
        for noticia in candidates:
            if noticia["id"] == news_id:
                motivo = lineas[1] if len(lineas) > 1 else ""
                print(f"🎯 Claude seleccionó {news_id}: {motivo}")
                return noticia
 
    print(f"⚠️  No se encontró ID válido. Respuesta de Claude: '{respuesta[:120]}'. Usando la primera candidata.")
    return candidates[0]
 
 
def extract_topics(noticia: dict, client: Anthropic) -> list:
    """
    Extrae 3-6 palabras clave temáticas de una noticia publicada.
    Se guardan para evitar repetir la misma temática en los próximos 60 días.
    """
    prompt = f"""Extrae las palabras clave que describen el TEMA principal de esta noticia.
No incluyas nombres de empresa ni personas, solo temáticas genéricas.
 
Noticia:
Título: {noticia['titulo']}
Resumen: {noticia['resumen'][:300]}
 
Devuelve entre 3 y 6 keywords cortas (1-3 palabras cada una) que capturen los temas principales.
 
Ejemplos:
- Noticia de robotaxis Waymo: ["robotaxis", "vehículos autónomos", "conducción autónoma", "movilidad urbana"]
- Noticia de IA en hoteles: ["IA hotelera", "chatbots hoteles", "atención al cliente IA"]
- Noticia de vuelos baratos en verano: ["vuelos baratos", "low cost", "temporada alta", "precios aéreos"]
- Noticia de traducción simultánea con auriculares: ["traducción IA", "auriculares inteligentes", "idiomas en viaje"]
 
IMPORTANTE: Responde SOLO con un JSON array de strings. Sin texto adicional.
Ejemplo: ["tema1", "tema2", "tema3"]"""
 
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )
 
    try:
        text = message.content[0].text.strip()
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            topics = json.loads(match.group(0))
            print(f"🏷️  Topics extraídos para cooldown: {topics}")
            return topics
    except Exception as e:
        print(f"⚠️  Error extrayendo topics: {e}. Se guardará sin topics.")
    return []
 
 
# ─────────────────────────────────────────────
# PASO 3: GENERAR POST CON CLAUDE
# ─────────────────────────────────────────────
 
PROMPT_TEMPLATE = """<rol>
Eres Alejandro Berdonces, profesional con 15 años en el sector turístico español y apasionado de la inteligencia artificial aplicada a los viajes y la hospitalidad. Escribes en LinkedIn en español con tono cercano, directo y con criterio propio. Tu audiencia mezcla profesionales del turismo, hotelería, agencias de viaje, aerolíneas y tecnólogos del sector.
</rol>
 
<noticia>
Título: {titulo}
Resumen: {resumen}
Categoría: {categoria}
</noticia>
 
<instruccion>
Escribe un post de LinkedIn sobre esta noticia siguiendo estos pasos:
 
PASO 1 — GANCHO: La primera frase debe captar atención de inmediato. Opciones: un dato que sorprende, una afirmación contraintuitiva, una imagen mental vivida, una pregunta que genera curiosidad. Nunca empieces con "Acabo de leer", con tu nombre ni con presentaciones sobre ti mismo.
 
PASO 2 — DESARROLLO: En 2-3 párrafos cortos conecta la noticia con el mundo real del turismo, la hospitalidad o los viajes. Usa ejemplos concretos y visuales. Muestra opinión propia: toma partido, no seas neutro. Transmite que la IA es una oportunidad real, no una amenaza abstracta.
 
PASO 3 — CIERRE CON PREGUNTA: Termina con una pregunta abierta que invite a reflexionar a cualquier profesional del sector turístico, no solo a especialistas técnicos. Que sea específica y genuinamente interesante, no genérica.
 
PASO 4 — HASHTAGS: 3-5 hashtags en línea separada.
</instruccion>
 
<reglas_criticas>
- Escribe en primera persona, con naturalidad y voz propia
- PROHIBIDO usar: "desde mi visión de Product Manager", "como PM", "en mi rol como PM", "en el mundo del product management", "en mi experiencia como Product Manager"
- No des lecciones de metodología, frameworks ni gestión de producto
- El post debe funcionar para alguien de un hotel, una agencia, una aerolínea o una startup de viajes — no solo para tecnólogos
- No suenes como si buscaras trabajo ni como si criticaras a tu empresa actual
- NUNCA inventes experiencias personales de Alex. Prohibido usar "He visto...", "He vivido...", "En mi experiencia...", "He trabajado con..." salvo que el dato venga literalmente del resumen de la noticia. Si quieres dar un ejemplo concreto, usa el sector en general: "Hay hoteles que...", "Muchas agencias pierden...", "El sector ha visto..." — nunca en primera persona inventada
- NUNCA uses markdown: sin negritas (**), sin cursivas (*), sin cabeceras (#)
- Máximo 2 emojis en todo el post
- Longitud: 150-230 palabras. Párrafos de máximo 3 líneas
- No inventes datos que no estén en el resumen
</reglas_criticas>
 
<ejemplos>
<example>
<tipo>MALO — este estilo no funciona</tipo>
<post>
Desde mi visión como Product Manager, este avance en IA es clave para el sector. Como PM con experiencia en travel tech, entiendo que debemos priorizar la experiencia de usuario en nuestros roadmaps. Las empresas que implementen estas soluciones ganarán ventaja competitiva.
¿Cómo integras tú la IA en tu estrategia de producto?
#ProductManagement #IA #Turismo
</post>
</example>
<example>
<tipo>BUENO — aspirar a este estilo</tipo>
<post>
El viajero del futuro no va a esperar en ninguna cola de facturación.
 
Lufthansa acaba de anunciar que el 80% de sus puertas de embarque funcionarán sin personal humano en 2026. Reconocimiento facial, check-in autónomo, asistencia por voz en 40 idiomas. Todo concentrado en el momento más estresante del viaje: el aeropuerto.
 
Lo que más me llama la atención no es la tecnología. Es que los pasajeros están empezando a preferirla. En los pilotos de Schiphol, la satisfacción subió un 23% cuando no había humanos en el proceso. Menos fricción, más control, más confianza.
 
Esto replantea una pregunta que muchos en el sector dábamos por resuelta: ¿dónde añade valor real el trato humano en el viaje, y dónde lo mantenemos simplemente por inercia?
 
¿En qué momentos del viaje crees que el contacto humano es insustituible?
 
#Turismo #IA #HospitalityTech #ExperienciaViajero
</post>
</example>
</ejemplos>
 
Responde directamente con el post, sin introducción ni comentarios."""
 
 
def generate_post(noticia: dict, client: Anthropic) -> str:
    prompt = PROMPT_TEMPLATE.format(
        titulo=noticia["titulo"],
        resumen=noticia["resumen"],
        categoria=noticia.get("categoria", "IA"),
        url=noticia.get("url", "")
    )
 
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
 
    return message.content[0].text.strip()
 
 
# ─────────────────────────────────────────────
# PASO 4: VALIDAR CALIDAD DEL POST
# ─────────────────────────────────────────────
 
def validate_post(post: str) -> tuple:
    words = len(post.split())
 
    if words < 100:
        return False, f"Post demasiado corto ({words} palabras, mínimo 100)"
    if words > 350:
        return False, f"Post demasiado largo ({words} palabras, máximo 350)"
    if "?" not in post:
        return False, "El post no contiene ninguna pregunta al lector"
    for phrase in FORBIDDEN_PHRASES:
        if phrase.lower() in post.lower():
            return False, f"Contiene frase prohibida: '{phrase}'"
 
    return True, "OK"
 
 
# ─────────────────────────────────────────────
# PASO 5: GENERAR IMAGEN CON DALL-E 3
# ─────────────────────────────────────────────
 
def generate_image_prompt(noticia: dict, client: Anthropic) -> str:
    """Genera un prompt cinematográfico y específico para gpt-image-1."""
    prompt = f"""Eres un director de arte especializado en fotografía de viajes y turismo de ocio para publicaciones como Condé Nast Traveler, Lonely Planet y National Geographic Traveler.
 
Tu tarea: crear un prompt en inglés para gpt-image-1 que genere una imagen IMPACTANTE y EMOTIVA para un post de LinkedIn sobre esta noticia, conectada siempre con el mundo del turismo de ocio.
 
NOTICIA:
Título: {noticia['titulo']}
Categoría: {noticia.get('categoria', '')}
Resumen: {noticia['resumen'][:250]}
 
ESTRUCTURA OBLIGATORIA del prompt (úsala siempre en este orden):
1. SUJETO PRINCIPAL: personas reales en situación de viaje o vacaciones (específico, no genérico)
2. ESTILO: cinematic travel photography / candid documentary / warm lifestyle photography / etc.
3. ILUMINACIÓN: golden hour / soft morning light / bright alpine sun / warm sunset / etc.
4. COMPOSICIÓN: wide establishing shot / candid close-up of expressions / aerial of landscape / etc.
5. PALETA DE COLOR: 2-3 colores dominantes cálidos o vibrantes (ej: warm amber, sky blue, alpine white)
6. ATMÓSFERA/MOOD: alegría, asombro, libertad, conexión, aventura, descanso, descubrimiento
7. CALIDAD: hyperrealistic, sharp focus, 8K, no text, no logos
 
PERSONAS EN LA IMAGEN — REGLAS CRÍTICAS:
- Las personas deben ser turistas, viajeros o familias en contexto de ocio y vacaciones
- Ropa casual de vacaciones: camisetas, gorras, abrigos de montaña, ropa de esquí, mochilas de viaje
- NUNCA trajes, corbatas, ropa de oficina ni expresiones serias/formales de ejecutivos
- Transmitir emociones genuinas en las caras: sonrisas, asombro, risa, alegría compartida
- Diversidad de perfiles: familias con niños, parejas (cualquier género), grupos de amigos, viajeros solos
- Entornos preferidos: Europa (Alpes, ciudades mediterráneas, pueblos con nieve, playas europeas)
- Turismo de esquí: escenas en pistas, remontes, après-ski, vistas alpinas — incluirlo siempre que encaje
- NUNCA imágenes de salas de reuniones, oficinas, laptops en escritorios ni entornos corporativos
 
REGLAS GENERALES:
- NUNCA robots, cerebros digitales, circuitos flotantes ni clichés de IA
- NUNCA texto, letras ni logos en la imagen
- El concepto debe conectar la noticia con una escena de viaje real y reconocible
- Personas de a pie disfrutando de sus vacaciones, no ilustraciones abstractas
 
EJEMPLOS DE PROMPTS BUENOS vs MALOS:
 
MALO: "Business professionals in suits using AI technology in a corporate meeting room"
BUENO: "Candid travel photography of a young couple in casual winter jackets laughing at a snowy alpine village square, one holding a smartphone showing real-time translation on screen. Warm golden afternoon light on snow-covered wooden chalets behind them. Soft amber and crisp white palette. Mood: effortless connection, travel joy. Hyperrealistic, 8K, no text, no logos."
 
MALO: "A robot helping a hotel manager at a front desk in a formal setting"
BUENO: "Wide cinematic shot of a family of four — parents and two kids in colorful ski gear — riding a mountain gondola above a breathtaking snowy Alpine landscape at sunrise. The kids press their faces against the glass in wonder. Warm pink-gold light on snow peaks. Palette: sky blue, rose gold, pure white. Mood: awe and family adventure. Hyperrealistic, sharp focus, 8K, no text."
 
Responde SOLO con el prompt en inglés, sin explicaciones ni introducción."""
 
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text.strip()
 
 
def generate_image(image_prompt: str) -> bytes:
    """Genera imagen con DALL-E 3 y devuelve los bytes."""
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
 
    import base64
    response = openai_client.images.generate(
        model="gpt-image-1",
        prompt=image_prompt,
        size="1024x1024",
        quality="medium",
        n=1
    )
 
    return base64.b64decode(response.data[0].b64_json)
 
 
def upload_image_to_linkedin(image_bytes: bytes) -> str:
    """
    Sube una imagen a LinkedIn en dos pasos:
    1. Registrar el upload y obtener URL + asset URN
    2. Subir el binario
    Devuelve el asset URN para usar en el post.
    """
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }
 
    # Paso 1: Registrar upload
    register_payload = {
        "registerUploadRequest": {
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "owner": LINKEDIN_PERSON_URN,
            "serviceRelationships": [{
                "relationshipType": "OWNER",
                "identifier": "urn:li:userGeneratedContent"
            }]
        }
    }
 
    register_response = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=headers,
        json=register_payload,
        timeout=15
    )
 
    if register_response.status_code != 200:
        raise Exception(f"Error registrando upload: {register_response.status_code} {register_response.text}")
 
    register_data = register_response.json()
    upload_url = register_data["value"]["uploadMechanism"]["com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"]["uploadUrl"]
    asset_urn = register_data["value"]["asset"]
 
    # Paso 2: Subir imagen
    upload_response = requests.put(
        upload_url,
        data=image_bytes,
        headers={"Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}"},
        timeout=30
    )
 
    if upload_response.status_code not in (200, 201):
        raise Exception(f"Error subiendo imagen: {upload_response.status_code}")
 
    print(f"✅ Imagen subida a LinkedIn. Asset URN: {asset_urn}")
    return asset_urn
 
 
# ─────────────────────────────────────────────
# PASO 6: PUBLICAR EN LINKEDIN
# ─────────────────────────────────────────────
 
def publish_to_linkedin(post_text: str, asset_urn: str = None) -> str:
    """
    Publica en LinkedIn via UGC Posts API.
    Si se proporciona asset_urn, incluye imagen en el post.
    Devuelve el ID del post publicado.
    """
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0"
    }
 
    if asset_urn:
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": "IMAGE",
            "media": [{
                "status": "READY",
                "media": asset_urn
            }]
        }
    else:
        share_content = {
            "shareCommentary": {"text": post_text},
            "shareMediaCategory": "NONE"
        }
 
    payload = {
        "author": LINKEDIN_PERSON_URN,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": share_content
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }
 
    response = requests.post(url, headers=headers, json=payload, timeout=15)
 
    if response.status_code not in (200, 201):
        raise Exception(f"LinkedIn API error {response.status_code}: {response.text}")
 
    post_id = response.headers.get("X-RestLi-Id", "unknown")
    return post_id
 
 
# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
 
def main():
    dry_run = "--dry-run" in sys.argv
    print(f"🚀 Iniciando{'  [DRY RUN]' if dry_run else ''} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
 
    # Validar credenciales
    if not dry_run:
        missing = [k for k, v in {
            "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
            "LINKEDIN_ACCESS_TOKEN": LINKEDIN_ACCESS_TOKEN,
            "LINKEDIN_PERSON_URN": LINKEDIN_PERSON_URN
        }.items() if not v]
        if missing:
            print(f"❌ Variables de entorno faltantes: {', '.join(missing)}")
            sys.exit(1)
    elif not ANTHROPIC_API_KEY:
        print("❌ ANTHROPIC_API_KEY no configurada (necesaria incluso en dry-run)")
        sys.exit(1)
 
    # Cargar estado
    published_ids = load_published_ids()
    print(f"📋 {len(published_ids)} noticias ya publicadas anteriormente")
 
    # Cargar topics en cooldown (últimos 60 días)
    recent_topics = get_recent_topics()
 
    # Obtener y filtrar noticias
    print(f"📥 Descargando noticias...")
    noticias = fetch_news()
    candidates = filter_news(noticias, published_ids)
 
    if not candidates:
        print("ℹ️  No hay noticias de los últimos 10 días sin publicar. Fin.")
        sys.exit(0)
 
    print(f"📰 {len(candidates)} noticias de los últimos 10 días disponibles")
 
    # Filtrar por temáticas recientes (cooldown 60 días)
    candidates = filter_by_topics(candidates, recent_topics)
 
    # Claude selecciona la más relevante para turismo
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    noticia_elegida = select_best_for_tourism(candidates, client)
    # Poner la elegida primera, resto como fallback ordenado por puntuación
    resto = sorted([n for n in candidates if n["id"] != noticia_elegida["id"]],
                   key=lambda x: x.get("puntuacion", 0), reverse=True)
    candidates_ordenadas = [noticia_elegida] + resto
 
    # Intentar con las top 3 candidatas por si alguna falla validación
    for noticia in candidates_ordenadas[:3]:
        print(f"\n🤖 Generando post para: {noticia['id']} — {noticia['titulo'][:50]}...")
        post = generate_post(noticia, client)
 
        valid, reason = validate_post(post)
        if not valid:
            print(f"⚠️  Post inválido ({reason}). Intentando con siguiente noticia...")
            continue
 
        # Generar imagen
        print("🎨 Generando prompt de imagen...")
        image_prompt = generate_image_prompt(noticia, client)
        print(f"   Prompt completo:\n   {image_prompt}\n")
        print("🖼️  Generando imagen con DALL-E 3...")
        image_bytes = generate_image(image_prompt)
        print(f"   Imagen generada ({len(image_bytes)//1024} KB)")
 
        print(f"\n{'─'*50}")
        print("📝 POST GENERADO:")
        print(f"{'─'*50}")
        print(post)
        print(f"{'─'*50}")
        print(f"Palabras: {len(post.split())}")
 
        if dry_run:
            # Guardar imagen localmente para poder verla
            img_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Imagenes_Publicaciones")
            os.makedirs(img_dir, exist_ok=True)
            img_path = os.path.join(img_dir, f"dry_run_imagen_{noticia['id']}.png")
            with open(img_path, "wb") as f:
                f.write(image_bytes)
            print(f"\n[DRY RUN] Imagen guardada en: {img_path}")
            print("[DRY RUN] No se publicó en LinkedIn.")
            sys.exit(0)
 
        # Subir imagen a LinkedIn
        print("\n📤 Subiendo imagen a LinkedIn...")
        asset_urn = upload_image_to_linkedin(image_bytes)
 
        # Publicar post con imagen
        print("📤 Publicando post en LinkedIn...")
        post_id = publish_to_linkedin(post, asset_urn)
        print(f"✅ Publicado con éxito. Post ID: {post_id}")
 
        # Extraer topics temáticos y guardar estado
        print("🏷️  Extrayendo topics para cooldown...")
        topics = extract_topics(noticia, client)
        save_published_id(noticia["id"], topics)
        print("\n🎉 ¡Proceso completado!")
        sys.exit(0)
 
    print("❌ Todas las candidatas fallaron validación. Revisa los logs.")
    sys.exit(1)
 
 
if __name__ == "__main__":
    main()

