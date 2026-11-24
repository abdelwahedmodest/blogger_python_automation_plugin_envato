import json
import os
import re
from bs4 import BeautifulSoup
import feedparser
from google import genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ================= CONFIGURATION =================
RSS_URL = "https://cookswithsoul.com/feed/"
SEEN_FILE = "seen_posts.json"
BLOGGER_BLOG_ID = "2493166214402069743"  # Ex: "1234567890123456789"
CREDENTIALS_FILE = "credentials.json"  # Téléchargé depuis Google Cloud Console
TOKEN_FILE = "token.json"
#GEMINI_MODEL ="gemini-3.8-flash"
GEMINI_MODEL = "gemini-4-carbon"

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
    # Nettoie les lignes vides multiples
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def rewrite_article_with_gemini(title: str, clean_text: str) -> tuple[str, str]:
    # Utilise automatiquement la variable d'environnement GEMINI_API_KEY
    client = genai.Client()

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

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    raw_output = (response.text or "").strip()

    # Extraction du titre et du contenu HTML
    match = re.search(r"^TITRE:\s*(.+)$", raw_output, flags=re.MULTILINE)
    if match:
        french_title = match.group(1).strip()
        html_body = raw_output[match.end():].strip()
    else:
        french_title = f"Recette : {title}"
        html_body = raw_output

    # Supprime d'éventuelles balises markdown ```html ... ``` si le modèle en ajoute
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
    # isDraft=True enregistre l'article en brouillon avant publication
    post = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=body, isDraft=True).execute()
    return post


def main():
    seen_ids = load_seen_ids()
    feed = feedparser.parse(RSS_URL)

    if not feed.entries:
        print("Aucun article trouvé dans le flux RSS.")
        return

    # On trie du plus ancien au plus récent pour traiter dans l'ordre chronologique
    entries = list(reversed(feed.entries))
    new_entries = []

    for entry in entries:
        entry_id = entry.get("id") or entry.get("link")
        if entry_id and entry_id not in seen_ids:
            new_entries.append((entry_id, entry))

    if not new_entries:
        print("Aucun nouvel article à traiter.")
        return

    blogger_service = get_blogger_service()

    for entry_id, entry in new_entries:
        title = entry.get("title", "Sans titre")
        # Récupère content[0].value si disponible, sinon summary
        raw_html = ""
        if entry.get("content"):
            raw_html = entry.content[0].get("value", "")
        else:
            raw_html = entry.get("summary", "")

        clean_text = strip_html(raw_html)
        print(f"Traitement de : {title}")

        french_title, html_body = rewrite_article_with_gemini(title, clean_text)
        created_post = create_blogger_draft(blogger_service, french_title, html_body)

        print(f"Brouillon créé sur Blogger : '{created_post.get('title')}' (ID: {created_post.get('id')})")
        seen_ids.add(entry_id)
        save_seen_ids(seen_ids)


if __name__ == "__main__":
    main()