"""
Script d'automatisation RSS vers Google Blogger via Google Gemini.

Ce script surveille un flux RSS (par défaut https://cookswithsoul.com/feed/),
détecte les nouveaux articles non encore traités, nettoie leur contenu HTML,
les traduit et les reformule en français à l'aide de l'API Gemini (avec
conversion des unités américaines vers le système métrique), puis enregistre
chaque article en tant que brouillon sur Google Blogger.
"""

# --- 1. IMPORTS DES BIBLIOTHÈQUES ---
import json  # Lecture et écriture de fichiers JSON (historique des articles et jeton OAuth)
import os  # Vérification de l'existence de fichiers sur le disque (os.path.exists)
import re  # Expressions régulières pour extraire le titre et nettoyer les balises Markdown
from bs4 import BeautifulSoup  # Analyse du HTML et extraction du texte brut
import feedparser  # Téléchargement et analyse des flux RSS/Atom
from google import genai  # SDK officiel Google GenAI pour appeler les modèles Gemini
from google.oauth2.credentials import Credentials  # Gestion des identifiants OAuth 2.0 existants
from google_auth_oauthlib.flow import InstalledAppFlow  # Flux d'authentification OAuth local
from google.auth.transport.requests import Request  # Rafraîchissement d'un jeton OAuth expiré
from googleapiclient.discovery import build  # Construction du client API Google Blogger v3


# --- 2. BLOC DE CONFIGURATION ---
RSS_URL = "https://cookswithsoul.com/feed/"  # URL du flux RSS à surveiller
SEEN_FILE = "seen_posts.json"  # Fichier local mémorisant les IDs des articles déjà traités
BLOGGER_BLOG_ID = "VOTRE_BLOG_ID_GOOGLE_BLOGGER"  # Identifiant numérique de votre blog Blogger
CREDENTIALS_FILE = "credentials.json"  # Fichier client OAuth téléchargé depuis Google Cloud Console
TOKEN_FILE = "token.json"  # Fichier où sera sauvegardé le jeton d'accès après connexion
GEMINI_MODEL = "gemini-2.5-flash"  # Modèle Gemini utilisé pour la traduction et la reformulation

SCOPES = ["https://www.googleapis.com/auth/blogger"]  # Périmètre d'autorisation OAuth pour Blogger


# --- 3. GESTION DE L'HISTORIQUE DES ARTICLES ---
def load_seen_ids() -> set:
    """Charge les identifiants des articles déjà traités depuis SEEN_FILE."""
    if os.path.exists(SEEN_FILE):  # Vérifie si le fichier d'historique existe sur le disque
        with open(SEEN_FILE, "r", encoding="utf-8") as f:  # Ouvre le fichier JSON en lecture UTF-8
            return set(json.load(f))  # Convertit la liste JSON en ensemble (set) pour un accès rapide
    return set()  # Retourne un ensemble vide si aucun historique n'existe encore


def save_seen_ids(seen_ids: set) -> None:
    """Sauvegarde l'ensemble des identifiants d'articles traités dans SEEN_FILE."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:  # Ouvre/crée le fichier JSON en écriture UTF-8
        # Trie les identifiants, indente sur 2 espaces et préserve les caractères accentués
        json.dump(sorted(list(seen_ids)), f, indent=2, ensure_ascii=False)


# --- 4. NETTOYAGE DU CONTENU HTML ---
def strip_html(html_content: str) -> str:
    """Extrait le texte brut d'une chaîne HTML et supprime les lignes vides multiples."""
    soup = BeautifulSoup(html_content or "", "html.parser")  # Parse le HTML (ou chaîne vide si None)
    text = soup.get_text(separator="\n")  # Extrait le texte visible en séparant les blocs par saut de ligne
    # Découpe en lignes, supprime les espaces superflus et ignore les lignes vides
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)  # Recompose le texte propre


# --- 5. TRADUCTION ET REFORMULATION AVEC GEMINI ---
def rewrite_article_with_gemini(title: str, clean_text: str) -> tuple[str, str]:
    """Traduit, convertit les unités et reformule l'article en français via Gemini."""
    # Instancie le client GenAI (lit automatiquement la variable d'environnement GEMINI_API_KEY)
    client = genai.Client()

    # Prompt d'instruction détaillant la traduction, la conversion métrique et le format HTML attendu
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

    # Envoie le prompt au modèle Gemini configuré
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    raw_output = (response.text or "").strip()  # Récupère la réponse brute nettoyée des espaces externes

    # Recherche la ligne commençant par "TITRE:" pour séparer le titre du corps HTML
    match = re.search(r"^TITRE:\s*(.+)$", raw_output, flags=re.MULTILINE)
    if match:
        french_title = match.group(1).strip()  # Extrait le titre généré en français
        html_body = raw_output[match.end():].strip()  # Récupère tout le contenu HTML situé après le titre
    else:
        # Solution de repli si le modèle n'a pas respecté le préfixe "TITRE:"
        french_title = f"Recette : {title}"
        html_body = raw_output

    # Supprime d'éventuelles balises de bloc Markdown (```html ou ```) ajoutées par le modèle
    html_body = re.sub(r"^```(?:html)?\s*|\s*```$", "", html_body, flags=re.MULTILINE).strip()
    return french_title, html_body


# --- 6. AUTHENTIFICATION ET PUBLICATION SUR GOOGLE BLOGGER ---
def get_blogger_service():
    """Authentifie l'utilisateur via OAuth 2.0 et retourne le client de l'API Blogger v3."""
    creds = None  # Initialise la variable des identifiants
    if os.path.exists(TOKEN_FILE):  # Charge le jeton existant s'il a déjà été généré
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Si aucun jeton n'existe ou s'il n'est plus valide
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())  # Rafraîchit automatiquement le jeton expiré
        else:
            # Lance la connexion OAuth dans le navigateur local à partir de credentials.json
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Sauvegarde le jeton valide dans token.json pour les prochaines exécutions
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("blogger", "v3", credentials=creds)  # Construit et retourne le service Blogger v3


def create_blogger_draft(service, title: str, html_content: str) -> dict:
    """Crée un nouvel article en mode brouillon (isDraft=True) sur Google Blogger."""
    body = {
        "kind": "blogger#post",  # Type de ressource Blogger
        "blog": {"id": BLOGGER_BLOG_ID},  # Référence du blog cible
        "title": title,  # Titre français de l'article
        "content": html_content,  # Contenu HTML de l'article
    }
    # Appelle l'API Blogger pour insérer le post en brouillon avant publication manuelle
    post = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=body, isDraft=True).execute()
    return post


# --- 7. BOUCLE PRINCIPALE ---
def main():
    """Orchestre la lecture du flux RSS, le filtrage, la réécriture IA et l'envoi vers Blogger."""
    seen_ids = load_seen_ids()  # Charge l'historique des articles déjà traités
    feed = feedparser.parse(RSS_URL)  # Télécharge et analyse le flux RSS

    if not feed.entries:  # Arrête le script si le flux ne renvoie aucune entrée
        print("Aucun article trouvé dans le flux RSS.")
        return

    # Inverse l'ordre du flux (du plus ancien au plus récent) pour un traitement chronologique
    entries = list(reversed(feed.entries))
    new_entries = []  # Liste des nouveaux articles à traiter

    for entry in entries:
        entry_id = entry.get("id") or entry.get("link")  # Récupère un identifiant unique (GUID ou URL)
        if entry_id and entry_id not in seen_ids:  # Vérifie que l'article n'a pas déjà été traité
            new_entries.append((entry_id, entry))

    if not new_entries:  # Quitte sans appeler les APIs si aucun nouvel article n'est détecté
        print("Aucun nouvel article à traiter.")
        return

    blogger_service = get_blogger_service()  # Initialise la connexion à Google Blogger

    for entry_id, entry in new_entries:
        title = entry.get("title", "Sans titre")  # Récupère le titre original de l'article
        # Récupère le contenu complet (content) s'il est présent, sinon le résumé (summary)
        raw_html = ""
        if entry.get("content"):
            raw_html = entry.content[0].get("value", "")
        else:
            raw_html = entry.get("summary", "")

        clean_text = strip_html(raw_html)  # Nettoie les balises HTML pour obtenir le texte brut
        print(f"Traitement de : {title}")

        # Traduit et reformule l'article en français via Gemini
        french_title, html_body = rewrite_article_with_gemini(title, clean_text)
        # Enregistre le résultat en brouillon sur Google Blogger
        created_post = create_blogger_draft(blogger_service, french_title, html_body)

        print(f"Brouillon créé sur Blogger : '{created_post.get('title')}' (ID: {created_post.get('id')})")
        seen_ids.add(entry_id)  # Marque l'article comme traité en mémoire
        save_seen_ids(seen_ids)  # Persiste immédiatement la mise à jour dans seen_posts.json


# --- 8. POINT D'ENTRÉE DU SCRIPT ---
if __name__ == "__main__":
    main()  # Exécute main() uniquement si le fichier est lancé directement
