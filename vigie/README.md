# Vigie

Veille quotidienne pour Matterhorn : `sources → collecte → filtre embeddings (Ollama local) →
tri fin + synthèse (Kimi) → digest Telegram`. Tout tourne en Docker sur worker, état en SQLite
(`data/vigie.db`), déclenché une fois par jour par le cron de l'hôte.

## Lancer

```bash
docker compose run --rm vigie                       # chaîne complète (fetch → filter → synth → deliver)
docker compose run --rm vigie python -m vigie fetch     # une étape seule…
docker compose run --rm vigie python -m vigie filter    #   (--dry : montre les scores sans rien écrire)
docker compose run --rm vigie python -m vigie synth     #   (--limit 3 : n'envoie que 3 articles à Kimi)
docker compose run --rm vigie python -m vigie deliver
docker compose run --rm vigie python -m vigie stats     # état de la base par source et statut
```

## Tester le chemin ALERTE

```bash
docker compose run --rm vigie python -m vigie test-alert
```

Injecte un article factice « Kanta annonce le support des lettres de mission ITAA en Belgique »,
le fait passer par le filtre puis Kimi, et livre le digest : l'article doit sortir **en tête**,
préfixé 🚨 ALERTE. L'article factice est purgé de la base à la fin.

## Ajouter une source

Éditer `sources.yaml` (monté en volume : pas de rebuild). Trois types :
- `rss` : flux RSS/Atom ;
- `listing` : page de liste — régler `include:` (regex) sur le chemin des articles pour couper la navigation ;
- `page-diff` : surveillance d'une page (pricing, changelog) — tout changement de texte devient un article.

`brand: <nom>` sur les sites de concurrents : le garde-fou mots-clés ignore la marque sur son propre site.

## Ajuster un seuil ou un thème

Éditer `themes.yaml` (monté en volume : pas de rebuild). `threshold` par thème : monter pour moins
d'articles, descendre pour plus de rappel. Pour calibrer : `filter --dry` affiche les scores des
gardés et des écartés les plus proches. T5 est volontairement bas : sur le thème concurrents,
on paie du bruit pour ne rien rater. Les mots-clés `tripwire_keywords` court-circuitent le filtre
dans la presse tierce.

## Le code

`vigie/*.py` est copié dans l'image : `docker compose build` après toute modification du code
(pas nécessaire pour les YAML). Modèle de synthèse : `kimi-k2.6` (surchargeable par `KIMI_MODEL`
dans l'environnement, endpoint par `KIMI_BASE_URL`).

## Secrets

`.env` (chmod 600, jamais commité) : `KIMI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Le bot est `@vigie_st00mp_bot` ; le `chat_id` s'obtient en écrivant au bot puis `getUpdates`.

## Écran TRMNL

Vitrine e-ink optionnelle : après la livraison Telegram, `deliver` pousse un teaser
(≤ 2 Ko : la une + 5 titres + compteurs) au webhook du plugin privé si `TRMNL_PLUGIN_UUID`
est présent dans `.env`. Re-pousser à la main : `python -m vigie trmnl`. Le markup du
plugin (pixel-art, unités `cqw`, polices Silkscreen/VT323) est le miroir de
`trmnl/markup.html`, géré via le serveur MCP de TRMNL (`https://trmnl.com/mcp`, clé
`TRMNL_MCP_KEY` scopée au plugin). Un échec TRMNL n'empêche jamais le digest Telegram.

## Cron

Sur l'hôte (`crontab -l`) : la chaîne complète tourne chaque jour à 07h10, journal dans
`data/cron.log`. Particularités worker : `build.network: host` et `network_mode: host` dans le
compose sont **obligatoires** (durcissement worker_guard : DNS du bridge mort, IPv6 sortant cassé —
le code force IPv4 partout).
