# AGENTS.md — dépôt `bots`

Lis ceci avant de toucher à quoi que ce soit ici.

## La règle qui définit ce dépôt : aucun LLM

Ce sont des **pipelines**, pas des agents. Aucun modèle de raisonnement, aucune mémoire, aucun outil.
`echo` transcrit et lit à voix haute, c'est tout.

Cette frontière est un choix, pas un accident. Un pipeline est prévisible, testable, sans coût par
message, sans latence de modèle et sans surface d'injection de prompt. **Chaque fois qu'un problème
peut se résoudre par une table plutôt que par un modèle, résous-le par une table.**

L'exemple vivant : la prononciation des anglicismes en synthèse vocale. Un LLM aurait pu deviner les
graphies ; `respell_fr.py` est un dictionnaire de ~80 entrées, enrichi par la commande Telegram
`/dico`. Le jugement est humain, rendu une fois par mot, et le résultat est déterministe.

Si tu es tenté d'ajouter un appel LLM ici, la bonne réponse est probablement : ce besoin appartient à
un agent Hermes, pas à ce dépôt.

## Ce dépôt EST l'emplacement d'exécution

Contrairement à `~/agents`, il n'y a pas de copie : `docker compose` se lance directement depuis
`~/bots/echo`. Ce que tu édites ici part en production au prochain `up -d`.

## Secrets

`echo/.env` (mode 600) porte `TELEGRAM_TOKEN` et `GROQ_API_KEY`. Jamais versionné.

**Piège vérifié :** `python-telegram-bot` passe par `httpx`, qui journalise chaque URL au niveau
INFO — jeton compris — dans `docker logs`. D'où, dans `bot.py` :

```python
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
```

Ne retire pas ces deux lignes.

## Docker sur cette machine — deux pièges

1. **Le DNS est mort dans les `docker build`.** Depuis le durcissement `worker_guard` du 4 août
   2026, le build passe par le bridge par défaut avec le `resolv.conf` de l'hôte (`192.168.1.1`),
   que la chain `forward` bloque. Les conteneurs `compose` ne sont pas touchés — ils utilisent le
   résolveur embarqué de Docker. Symptôme : `apt-get update` sort en 100, uniquement au build.
   Correctif en place : `build.network: host` dans le compose.
2. **`COPY` préserve les modes.** Un fichier déposé par `scp` en 600 est illisible par le user 1000
   du conteneur, qui boucle sur `Permission denied`. Vérifie les modes avant de builder.

## Modèles et volumes

Les modèles ne sont **jamais** copiés dans l'image : `kokoro_models` et le cache XTTS sont montés en
volumes lecture seule. Une image de 3,3 Go est déjà assez grosse.

Le conteneur tourne en `user: 1000:1000` et sous `mem_limit: 4g` — XTTS résident pèse ~2,2 Go, et
GitLab et Minecraft partagent la même RAM sur cette machine.

## Avant de valider une modification de la synthèse

Tu ne peux pas juger une voix en lisant du code. Génère un échantillon et envoie-le sur Telegram pour
écoute humaine :

```bash
hermes -p tank send -t telegram:<chat_id> "MEDIA:/chemin/vers/echantillon.ogg"
```

C'est la seule recette valable pour ce dépôt.
