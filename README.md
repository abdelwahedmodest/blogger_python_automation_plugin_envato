# AUTOMATION_PROJECT

Projet d'automatisation en Python permettant d'extraire, filtrer et traiter du contenu web (articles, recettes, images) tout en évitant les doublons grâce à un suivi d'historique local.

## Fonctionnalités

- **Extraction et filtrage d'images :** Sélection automatique des images pertinentes d'un article et exclusion des éléments d'interface (`logo`, `icon`, `avatar`, `spinner`, réseaux sociaux, etc.).
- **Exécution multi-scénarios :** Plusieurs scripts modulaires (`scenario.py`, `scenario2.py`, `scenario3.py`, `scenario4.py`) adaptés à différents flux d'automatisation[cite: 1].
- **Suivi des publications traitées :** Mémorisation des éléments déjà publiés ou analysés via `seen_posts.json` afin d'éviter les doublons[cite: 1].
- **Intégration API :** Connexion aux services externes (Google API / AI Studio) via variables d'environnement et jetons d'authentification[cite: 1].

## Structure du projet

```text
AUTOMATION_PROJECT/
├── aienv/                     # Environnement virtuel Python (ignoré par Git)
├── scenario.py                # Scénario d'automatisation principal
├── scenario2.py               # Variante / flux secondaire 2
├── scenario3.py               # Variante / flux secondaire 3
├── scenario4.py               # Extraction et validation d'images d'articles
├── scenario_documente.py      # Version documentée du script principal
├── seen_posts.json            # Historique des publications déjà traitées
├── cordis.patch.yml           # Fichier de configuration / patch
├── .env                       # Variables d'environnement et clés API (ignoré par Git)
├── credentials.json           # Identifiants d'authentification Google (ignoré par Git)
├── token.json                 # Jeton de session généré automatiquement (ignoré par Git)
└── README.md                  # Documentation du projet