import json
import os
import re
from bs4 import BeautifulSoup
import feedparser
from google import genai
from google.genai import errors, types
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Charge automatiquement les variables du fichier .env s'il est présent
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ================= CONFIGURATION =================
RSS_URL = "https://cookswithsoul.com/feed/"
SEEN_FILE = "seen_posts.json"
BLOGGER_BLOG_ID = "2493166214402069743"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"

# Modèles candidats légers / Free Tier essayés en priorité
CANDIDATE_FREE_MODELS = [
    "gemini-4-carbon",
    "gemini-4-hydrogen",
    "gemini-4-neon",
]

# Mots-clés des modèles payants uniquement, lourds ou non textuels à exclure
NON_FREE_OR_INCOMPATIBLE_KEYWORDS = (
    "embedding",
    "deep-research",
    "antigravity",
    "imagen",
    "veo",
    "tts",
    "aqa",
    "ultra",
)

SCOPES = ["https://www.googleapis.com/auth/blogger"]
# =================================================


def load_seen_ids() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids: set) -> None:
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f, indent=2, ensure_ascii=False)


def strip_html(html_content: str) -> str:
    soup = BeautifulSoup(html_content or "", "html.parser")
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def get_active_free_models(client: genai.Client) -> list[str]:
    """Liste les modèles candidats et ne garde que ceux ayant un quota Free Tier actif."""
    discovered = []
    try:
        for m in client.models.list():
            name = (m.name or "").replace("models/", "")
            if not name:
                continue
            if any(kw in name.lower() for kw in NON_FREE_OR_INCOMPATIBLE_KEYWORDS):
                continue
            actions = getattr(m, "supported_actions", None) or getattr(
                m, "supported_generation_methods", None
            )
            if actions and "generateContent" not in actions:
                continue
            discovered.append(name)
    except Exception as e:
        print(f"Impossible de lister les modèles dynamiquement ({e}).")

    candidates = []
    for model_name in CANDIDATE_FREE_MODELS + discovered:
        if model_name not in candidates:
            candidates.append(model_name)

    active_free_models = []
    print("Vérification des modèles avec quota Free Tier actif...")
    for model_name in candidates:
        try:
            client.models.generate_content(
                model=model_name,
                contents="OK",
                config=types.GenerateContentConfig(max_output_tokens=2),
            )
            print(f"  [DISPONIBLE - FREE TIER] {model_name}")
            active_free_models.append(model_name)
        except errors.ClientError as e:
            # 429 = quota Free Tier à 0 ou épuisé ; 404 = modèle non accessible
            print(f"  [IGNORÉ - Code {e.code}] {model_name}")
        except errors.ServerError as e:
            # 503 = surcharge temporaire, le modèle reste éligible au Free Tier
            print(f"  [DISPONIBLE MAIS SURCHARGÉ - 503] {model_name}")
            active_free_models.append(model_name)

    return active_free_models


def rewrite_article_with_gemini(
    client: genai.Client, models_to_try: list[str], title: str, clean_text: str
) -> tuple[str, str]:
    prompt = f"""Tu es un rédacteur culinaire expert en français.
À partir de l'article suivant (titre et contenu), rédige un nouvel article original en français destiné à mon blog.
- Traduis fidèlement les étapes et les ingrédients.
- Convertis toutes les mesures américaines (cups, ounces, tablespoons, teaspoons, °F) en unités métriques francophones (grammes, millilitres, cuillères à soupe/café, °C).
- Reformule entièrement le texte avec un ton chaleureux, fluide et naturel, sans copier mot pour mot l'original.
- Structure l'article avec une introduction, les ingrédients, les étapes de préparation et une conclusion.

Format de sortie OBLIGATOIRE :
Ligne 1 : TITRE: <titre accrocheur en français>
Puis, à partir de la ligne suivante, le corps de l'article en HTML propre (balises <h2>, <p>, <ul>, <li>, <ol>, sans balises ```html).

Article source :
Titre : {title}
Contenu :
{clean_text}
"""

    response = None
    last_error = None

    for model_name in models_to_try:
        try:
            print(f"  -> Génération avec le modèle gratuit : {model_name}")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            break
        except (errors.ServerError, errors.ClientError) as e:
            last_error = e
            print(f"  -> Modèle {model_name} indisponible ({e.code}), essai du suivant...")

    if response is None:
        raise last_error if last_error else RuntimeError("Aucun modèle gratuit disponible.")

    raw_output = (response.text or "").strip()

    match = re.search(r"^TITRE:\s*(.+)$", raw_output, flags=re.MULTILINE)
    if match:
        french_title = match.group(1).strip()
        html_body = raw_output[match.end():].strip()
    else:
        french_title = f"Recette : {title}"
        html_body = raw_output

    html_body = re.sub(r"^```(?:html)?\s*|\s*```$", "", html_body, flags=re.MULTILINE).strip()
    return french_title, html_body


def get_blogger_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("blogger", "v3", credentials=creds)


def create_blogger_draft(service, title: str, html_content: str) -> dict:
    body = {
        "kind": "blogger#post",
        "blog": {"id": BLOGGER_BLOG_ID},
        "title": title,
        "content": html_content,
    }
    post = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=body, isDraft=True).execute()
    return post


def main():
    seen_ids = load_seen_ids()
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("Aucun article trouvé dans le flux RSS.")
        return

    entries = list(reversed(feed.entries))
    new_entries = []

    for entry in entries:
        entry_id = entry.get("id") or entry.get("link")
        if entry_id and entry_id not in seen_ids:
            new_entries.append((entry_id, entry))

    if not new_entries:
        print("Aucun nouvel article à traiter.")
        return

    gemini_client = genai.Client()
    free_models = get_active_free_models(gemini_client)

    if not free_models:
        print("Aucun modèle gratuit (Free Tier) disponible actuellement sur cette clé API. Réessayez plus tard.")
        return

    print(f"Modèles gratuits retenus ({len(free_models)}) : {', '.join(free_models)}")
    blogger_service = get_blogger_service()

    for entry_id, entry in new_entries:
        title = entry.get("title", "Sans titre")
        raw_html = ""
        if entry.get("content"):
            raw_html = entry.content[0].get("value", "")
        else:
            raw_html = entry.get("summary", "")

        clean_text = strip_html(raw_html)
        print(f"Traitement de : {title}")

        try:
            french_title, html_body = rewrite_article_with_gemini(
                gemini_client, free_models, title, clean_text
            )
            created_post = create_blogger_draft(blogger_service, french_title, html_body)
            print(f"Brouillon créé sur Blogger : '{created_post.get('title')}' (ID: {created_post.get('id')})")
            seen_ids.add(entry_id)
            save_seen_ids(seen_ids)
        except Exception as e:
            print(f"Arrêt du traitement pour '{title}' : {e}")
            break


if __name__ == "__main__":
    main()