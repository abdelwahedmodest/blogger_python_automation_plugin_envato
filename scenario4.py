# Importe le module standard json pour lire et sauvegarder l'historique des URLs déjà traitées
import json
# Importe le module standard os pour manipuler les chemins de fichiers et vérifier leur existence
import os
# Importe le module standard re (expressions régulières) pour nettoyer et découper le texte généré par l'IA
import re
# Importe le module standard time pour marquer une pause polie entre chaque requête HTTP (anti-blocage)
import time
# Importe urljoin pour convertir les liens d'images relatifs (/images/plat.jpg) en URLs complètes (https://...)
from urllib.parse import urljoin, urlparse

# Importe BeautifulSoup pour analyser le HTML des pages web et en extraire le texte et les balises <img>
from bs4 import BeautifulSoup
# Importe requests pour télécharger le code HTML des pages web et télécharger les fichiers images
import requests
# Importe le SDK officiel Google GenAI pour appeler les modèles Gemini
from google import genai
# Importe les classes d'erreurs et de configuration du SDK Google GenAI
from google.genai import errors, types
# Importe la gestion des identifiants OAuth2 de Google pour s'authentifier auprès de Blogger
from google.oauth2.credentials import Credentials
# Importe le flux d'authentification locale OAuth2 lors de la première connexion
from google_auth_oauthlib.flow import InstalledAppFlow
# Importe Request pour rafraîchir automatiquement un jeton OAuth expiré
from google.auth.transport.requests import Request
# Importe build pour construire le client de l'API Google Blogger v3
from googleapiclient.discovery import build

# Tente de charger les variables d'environnement (ex: GEMINI_API_KEY) depuis un fichier .env local
try:
    # Importe la fonction load_dotenv si le paquet python-dotenv est installé
    from dotenv import load_dotenv
    # Charge les variables du fichier .env dans l'environnement du script
    load_dotenv()
# Ignore silencieusement si python-dotenv n'est pas installé
except ImportError:
    pass

# ================= CONFIGURATION =================
# Nom du fichier texte contenant la liste des URLs d'articles existants à scraper (1 URL par ligne)
URLS_FILE = "urls_articles.txt"
# Dossier local où seront sauvegardées les images téléchargées pour chaque article
IMAGES_DIR = "images_scrappees"
# Fichier JSON mémorisant les URLs déjà traitées pour ne pas les republier en double
SEEN_FILE = "seen_urls.json"
# Identifiant unique de ton blog Blogger cible
BLOGGER_BLOG_ID = "2493166214402069743"
# Fichier des identifiants clients OAuth téléchargé depuis Google Cloud Console
CREDENTIALS_FILE = "credentials.json"
# Fichier généré automatiquement pour conserver la session Blogger connectée
TOKEN_FILE = "token.json"
# Délai en secondes entre deux pages scrapées pour éviter de surcharger le serveur cible
REQUEST_DELAY_SECONDS = 2

# En-têtes HTTP simulant un navigateur classique pour éviter d'être bloqué par le site cible
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
}

# Liste des modèles Gemini légers / Free Tier testés en priorité
CANDIDATE_FREE_MODELS = [
    "gemini-4-carbon",
    "gemini-4-hydrogen",
    "gemini-4-neon",
]

# Mots-clés permettant d'écarter les modèles payants, spécialisés ou non textuels
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

# Permission OAuth requise pour créer et gérer des articles sur Blogger
SCOPES = ["https://www.googleapis.com/auth/blogger"]
# =================================================


def load_seen_urls() -> set:
    """Charge depuis le disque l'ensemble des URLs déjà traitées."""
    # Vérifie si le fichier d'historique existe déjà
    if os.path.exists(SEEN_FILE):
        # Ouvre le fichier JSON en lecture avec encodage UTF-8
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            # Convertit la liste JSON en ensemble (set) pour une recherche rapide
            return set(json.load(f))
    # Retourne un ensemble vide si aucun historique n'existe encore
    return set()


def save_seen_urls(seen_urls: set) -> None:
    """Sauvegarde sur le disque la liste mise à jour des URLs déjà traitées."""
    # Ouvre le fichier JSON en écriture avec encodage UTF-8
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        # Écrit les URLs triées de manière lisible (indentation de 2 espaces)
        json.dump(sorted(list(seen_urls)), f, indent=2, ensure_ascii=False)


def load_target_urls(filepath: str) -> list[str]:
    """Lit le fichier texte contenant les URLs d'articles à scraper (une par ligne)."""
    # Vérifie si le fichier d'URLs existe ; sinon crée un fichier modèle et avertit l'utilisateur
    if not os.path.exists(filepath):
        # Crée un fichier exemple vide avec des commentaires explicatifs
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Colle ici une URL d'article par ligne (les lignes commençant par # sont ignorées)\n")
            f.write("# Exemple : https://cookswithsoul.com/mon-article-recette/\n")
        # Informe l'utilisateur qu'il doit remplir ce fichier
        print(f"Fichier '{filepath}' créé. Ajoute tes URLs d'articles dedans puis relance le script.")
        return []

    # Initialise la liste des URLs valides à retourner
    urls = []
    # Ouvre le fichier d'URLs en lecture UTF-8
    with open(filepath, "r", encoding="utf-8") as f:
        # Parcourt chaque ligne du fichier
        for line in f:
            # Supprime les espaces et retours à la ligne autour de l'URL
            clean_line = line.strip()
            # Ignore les lignes vides et les lignes de commentaires commençant par #
            if not clean_line or clean_line.startswith("#"):
                continue
            # Évite les doublons tout en conservant l'ordre du fichier
            if clean_line not in urls:
                urls.append(clean_line)
    # Retourne la liste propre des URLs à scraper
    return urls


def slugify(text: str) -> str:
    """Transforme un titre ou un nom de fichier en chaîne propre sans caractères spéciaux."""
    # Passe le texte en minuscules et supprime les espaces superflus
    text = text.lower().strip()
    # Remplace tout caractère non alphanumérique par un tiret
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Supprime les tirets en début ou fin de chaîne et limite la longueur à 60 caractères
    return text.strip("-")[:60] or "article"


def is_valid_article_image(img_url: str, alt_text: str = "") -> bool:
    """Filtre les images parasites (icônes, logos, pixels de suivi, avatars)."""
    # Convertit l'URL et le texte alternatif en minuscules pour faciliter les vérifications
    lower_url = img_url.lower()
    lower_alt = (alt_text or "").lower()

    # Ignore les images encodées en base64 (souvent des placeholders vides)
    if lower_url.startswith("data:"):
        return False

    # Liste de mots-clés typiques des éléments d'interface non liés à la recette
    ignored_keywords = (
        "logo",
        "icon",
        "avatar",
        "gravatar",
        "spinner",
        "loader",
        "pixel",
        "badge",
        "pinterest",
        "facebook",
        "instagram",
        "twitter",
        "emoji",
        "ads",
        "banner",
    )
    # Rejette l'image si l'URL ou son texte alternatif contient un mot-clé parasite
    if any(kw in lower_url or kw in lower_alt for kw in ignored_keywords):
        return False

    # Ignore les fichiers vectoriels SVG souvent utilisés pour les icônes du thème
    if urlparse(lower_url).path.endswith(".svg"):
        return False

    # Accepte l'image si elle a passé tous les filtres
    return True


def download_image(img_url: str, save_folder: str, index: int) -> str | None:
    """Télécharge une image dans le dossier local de l'article et retourne son chemin local."""
    try:
        # Envoie une requête GET en mode streaming pour télécharger l'image
        resp = requests.get(img_url, headers=HTTP_HEADERS, timeout=20, stream=True)
        # Lève une exception si le serveur renvoie une erreur HTTP (403, 404, 500...)
        resp.raise_for_status()

        # Vérifie le type MIME renvoyé par le serveur pour s'assurer qu'il s'agit bien d'une image
        content_type = resp.headers.get("Content-Type", "").lower()
        if "image/" not in content_type:
            return None

        # Détermine l'extension du fichier à partir de l'URL ou du Content-Type
        path_ext = os.path.splitext(urlparse(img_url).path)[1].lower()
        if path_ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            if "png" in content_type:
                path_ext = ".png"
            elif "webp" in content_type:
                path_ext = ".webp"
            else:
                path_ext = ".jpg"

        # Construit le nom du fichier local numéroté (ex: image_01.jpg)
        filename = f"image_{index:02d}{path_ext}"
        # Construit le chemin complet de sauvegarde
        filepath = os.path.join(save_folder, filename)

        # Écrit le contenu binaire de l'image sur le disque par blocs de 8 Ko
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Ignore et supprime les fichiers minuscules (< 5 Ko, souvent des icônes résiduelles)
        if os.path.getsize(filepath) < 5120:
            os.remove(filepath)
            return None

        # Retourne le chemin local de l'image enregistrée
        return filepath
    # Capture toute erreur réseau ou disque sans faire planter tout le script
    except Exception as e:
        print(f"    [Image ignorée] Impossible de télécharger {img_url} ({e})")
        return None


def scrape_article(url: str) -> dict:
    """Scrape le titre, le texte propre et les images d'un article web à partir de son URL."""
    # Télécharge le code HTML complet de l'article
    response = requests.get(url, headers=HTTP_HEADERS, timeout=25)
    # Vérifie que la page a bien répondu avec un statut HTTP 200 OK
    response.raise_for_status()

    # Analyse le HTML récupéré avec BeautifulSoup
    soup = BeautifulSoup(response.text, "html.parser")

    # Extrait le titre principal depuis la balise <h1> (ou <title> en secours)
    h1_tag = soup.find("h1")
    title = h1_tag.get_text(strip=True) if h1_tag else (soup.title.get_text(strip=True) if soup.title else "Sans titre")

    # Recherche le conteneur principal de l'article (classes courantes sous WordPress / blogs culinaires)
    content_container = (
        soup.select_one("article .entry-content")
        or soup.select_one(".entry-content")
        or soup.select_one(".post-content")
        or soup.select_one("article")
        or soup.body
    )

    # Initialise la liste des données d'images extraites (URL + légende/alt)
    extracted_images = []
    # Ensemble pour éviter de traiter deux fois la même URL d'image
    seen_img_urls = set()

    # Crée un sous-dossier dédié aux images de cet article
    article_folder = os.path.join(IMAGES_DIR, slugify(title))
    os.makedirs(article_folder, exist_ok=True)

    # Parcourt toutes les balises <img> présentes dans le contenu de l'article
    if content_container:
        for img in content_container.find_all("img"):
            # Récupère la source réelle en gérant le lazy-loading (data-src, data-lazy-src, src)
            raw_src = (
                img.get("data-lazy-src")
                or img.get("data-src")
                or img.get("data-orig-file")
                or img.get("src")
            )
            # Ignore la balise si aucune source n'est trouvée
            if not raw_src:
                continue

            # Convertit une éventuelle URL relative en URL absolue complète
            full_img_url = urljoin(url, raw_src.strip())
            # Récupère le texte alternatif (alt) utile pour décrire l'image
            alt_text = (img.get("alt") or "").strip()

            # Évite les doublons et filtre les images décoratives/parasites
            if full_img_url in seen_img_urls or not is_valid_article_image(full_img_url, alt_text):
                continue

            # Mémorise cette URL d'image comme déjà vue
            seen_img_urls.add(full_img_url)
            # Télécharge l'image localement dans le dossier de l'article
            local_path = download_image(full_img_url, article_folder, len(extracted_images) + 1)

            # Conserve les métadonnées si le téléchargement a réussi
            if local_path:
                extracted_images.append({
                    "url": full_img_url,
                    "alt": alt_text,
                    "local_path": local_path,
                })

        # Supprime les balises non textuelles (scripts, styles, formulaires, navigation) avant d'extraire le texte
        for unwanted in content_container(["script", "style", "nav", "footer", "aside", "form", "noscript"]):
            unwanted.decompose()

        # Extrait le texte brut structuré ligne par ligne
        raw_text = content_container.get_text(separator="
")
        clean_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        clean_text = "
".join(clean_lines)
    else:
        clean_text = ""

    # Retourne un dictionnaire complet avec toutes les données scrapées
    return {
        "url": url,
        "title": title,
        "clean_text": clean_text,
        "images": extracted_images,
        "images_folder": article_folder,
    }


def get_active_free_models(client: genai.Client) -> list[str]:
    """Liste les modèles Gemini disponibles et ne garde que ceux actifs en Free Tier."""
    # Liste destinée à recevoir les modèles découverts via l'API
    discovered = []
    try:
        # Interroge la liste des modèles accessibles par la clé API
        for m in client.models.list():
            # Nettoie le préfixe 'models/' du nom technique
            name = (m.name or "").replace("models/", "")
            if not name:
                continue
            # Exclut les modèles payants ou non textuels selon les mots-clés définis
            if any(kw in name.lower() for kw in NON_FREE_OR_INCOMPATIBLE_KEYWORDS):
                continue
            # Vérifie que le modèle supporte la génération de texte (generateContent)
            actions = getattr(m, "supported_actions", None) or getattr(
                m, "supported_generation_methods", None
            )
            if actions and "generateContent" not in actions:
                continue
            discovered.append(name)
    except Exception as e:
        print(f"Impossible de lister les modèles dynamiquement ({e}).")

    # Fusionne les modèles prioritaires et les modèles découverts sans doublons
    candidates = []
    for model_name in CANDIDATE_FREE_MODELS + discovered:
        if model_name not in candidates:
            candidates.append(model_name)

    # Teste chaque modèle candidat avec une micro-requête pour vérifier le quota gratuit
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
            # 429 = quota à 0 ou dépassé ; 404 = modèle introuvable
            print(f"  [IGNORÉ - Code {e.code}] {model_name}")
        except errors.ServerError as e:
            # 503 = serveur temporairement surchargé mais modèle éligible
            print(f"  [DISPONIBLE MAIS SURCHARGÉ - 503] {model_name}")
            active_free_models.append(model_name)

    return active_free_models


def rewrite_article_with_gemini(
    client: genai.Client,
    models_to_try: list[str],
    title: str,
    clean_text: str,
    images: list[dict],
) -> tuple[str, str]:
    """Réécrit l'article en français avec Gemini et intègre les balises <img> extraites."""
    # Prépare la liste textuelle des images disponibles pour que Gemini puisse les insérer aux bons endroits
    if images:
        images_instruction = "
".join(
            [f"- Image {i+1} : URL={img['url']} | Description={img['alt'] or 'Photo de la recette'}" for i, img in enumerate(images)]
        )
    else:
        images_instruction = "Aucune image disponible."

    # Construit le prompt détaillé de traduction, conversion métrique et intégration des images
    prompt = f"""Tu es un rédacteur culinaire expert en français.
À partir de l'article suivant (titre, contenu et liste d'images), rédige un nouvel article original en français destiné à mon blog.
- Traduis fidèlement les étapes et les ingrédients.
- Convertis toutes les mesures américaines (cups, ounces, tablespoons, teaspoons, °F) en unités métriques francophones (grammes, millilitres, cuillères à soupe/café, °C).
- Reformule entièrement le texte avec un ton chaleureux, fluide et naturel, sans copier mot pour mot l'original.
- Structure l'article avec une introduction, les ingrédients, les étapes de préparation et une conclusion.
- Intègre harmonieusement les images fournies ci-dessous dans le corps HTML avec des balises <figure><img src="URL" alt="description en français" loading="lazy"/></figure> aux endroits pertinents.

Images extraites de l'article source :
{images_instruction}

Format de sortie OBLIGATOIRE :
Ligne 1 : TITRE: <titre accrocheur en français>
Puis, à partir de la ligne suivante, le corps de l'article en HTML propre (balises <h2>, <p>, <ul>, <li>, <ol>, <figure>, <img>, sans balises ```html).

Article source :
Titre : {title}
Contenu :
{clean_text}
"""

    response = None
    last_error = None

    # Essaie les modèles gratuits disponibles dans l'ordre jusqu'à obtenir une réponse valide
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

    # Lève une exception si aucun modèle n'a pu générer le contenu
    if response is None:
        raise last_error if last_error else RuntimeError("Aucun modèle gratuit disponible.")

    # Récupère le texte brut généré par Gemini
    raw_output = (response.text or "").strip()

    # Extrait le titre français depuis la première ligne commençant par 'TITRE:'
    match = re.search(r"^TITRE:\s*(.+)$", raw_output, flags=re.MULTILINE)
    if match:
        french_title = match.group(1).strip()
        html_body = raw_output[match.end():].strip()
    else:
        french_title = f"Recette : {title}"
        html_body = raw_output

    # Nettoie d'éventuelles balises Markdown ```html résiduelles
    html_body = re.sub(r"^```(?:html)?\s*|\s*```$", "", html_body, flags=re.MULTILINE).strip()
    return french_title, html_body


def get_blogger_service():
    """Authentifie l'utilisateur auprès de Google et retourne le client API Blogger."""
    creds = None
    # Recharge le jeton OAuth existant s'il est déjà sauvegardé localement
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    # Si le jeton est absent ou invalide, le rafraîchit ou ouvre le navigateur pour autorisation
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Sauvegarde le nouveau jeton dans token.json pour les prochaines exécutions
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    # Construit et retourne le service API Blogger v3
    return build("blogger", "v3", credentials=creds)


def create_blogger_draft(service, title: str, html_content: str) -> dict:
    """Publie l'article réécrit en tant que brouillon (isDraft=True) sur Blogger."""
    # Prépare le corps de la requête conforme à l'API Blogger v3
    body = {
        "kind": "blogger#post",
        "blog": {"id": BLOGGER_BLOG_ID},
        "title": title,
        "content": html_content,
    }
    # Insère l'article en mode brouillon sans le publier publiquement tout de suite
    post = service.posts().insert(blogId=BLOGGER_BLOG_ID, body=body, isDraft=True).execute()
    return post


def main():
    """Point d'entrée principal orchestrant le scraping d'URLs, la réécriture et l'envoi vers Blogger."""
    # Charge la liste des URLs cibles depuis urls_articles.txt
    target_urls = load_target_urls(URLS_FILE)
    if not target_urls:
        return

    # Charge l'historique des URLs déjà traitées
    seen_urls = load_seen_urls()
    # Filtre uniquement les URLs qui n'ont pas encore été traitées
    pending_urls = [u for u in target_urls if u not in seen_urls]

    if not pending_urls:
        print("Toutes les URLs du fichier ont déjà été traitées.")
        return

    print(f"{len(pending_urls)} article(s) à scraper et traiter.")

    # Initialise le client Gemini et détecte les modèles gratuits actifs
    gemini_client = genai.Client()
    free_models = get_active_free_models(gemini_client)

    if not free_models:
        print("Aucun modèle gratuit (Free Tier) disponible actuellement sur cette clé API.")
        return

    print(f"Modèles gratuits retenus ({len(free_models)}) : {', '.join(free_models)}")
    # Initialise la connexion authentifiée à Blogger
    blogger_service = get_blogger_service()

    # Traite chaque URL l'une après l'autre
    for index, url in enumerate(pending_urls, start=1):
        print(f"\n[{index}/{len(pending_urls)}] Scraping de : {url}")
        try:
            # 1. Scrape le titre, le texte propre et télécharge les images de la page
            scraped = scrape_article(url)
            print(f"  -> Titre extrait : {scraped['title']}")
            print(f"  -> {len(scraped['images'])} image(s) téléchargée(s) dans '{scraped['images_folder']}'")

            # Vérifie qu'un contenu textuel suffisant a bien été extrait
            if len(scraped["clean_text"]) < 100:
                print("  -> Contenu trop court ou bloqué, article ignoré.")
                continue

            # 2. Réécrit l'article en français avec Gemini en intégrant les images
            french_title, html_body = rewrite_article_with_gemini(
                gemini_client,
                free_models,
                scraped["title"],
                scraped["clean_text"],
                scraped["images"],
            )

            # 3. Crée le brouillon sur Blogger
            created_post = create_blogger_draft(blogger_service, french_title, html_body)
            print(f"  -> Brouillon Blogger créé : '{created_post.get('title')}' (ID: {created_post.get('id')})")

            # 4. Marque l'URL comme traitée et sauvegarde immédiatement l'historique
            seen_urls.add(url)
            save_seen_urls(seen_urls)

            # Pause polie avant de passer à l'URL suivante
            if index < len(pending_urls):
                time.sleep(REQUEST_DELAY_SECONDS)

        except Exception as e:
            print(f"  -> Erreur lors du traitement de '{url}' : {e}")


# Exécute la fonction main() uniquement lorsque le script est lancé directement
if __name__ == "__main__":
    main()
