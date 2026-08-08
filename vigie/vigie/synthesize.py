"""Tri fin + synthèse : Kimi lit les survivants du filtre, note l'impact et rédige."""
import json
import os
import sys

import httpx

from .db import connect

BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
MODEL = os.environ.get("KIMI_MODEL", "kimi-k2.6")

SYSTEM = """Tu es l'analyste de veille de Matterhorn, un copilote d'avant-vente pour les fiduciaires \
comptables belges francophones (cabinets de 1 à 10 personnes, sans service informatique). \
Matterhorn couvre le trajet : demande entrante d'un prospect → réponse chiffrée → lettre de mission (LDM) \
signable. La LDM est le contrat d'engagement obligatoire entre cabinet et client, encadré par la \
déontologie de l'ITAA et la loi belge de 2019. Principe non négociable : l'agent prépare, l'humain \
valide chaque envoi. Matterhorn N'EST PAS un outil Peppol, d'e-facturation ou de production comptable \
(encodage, bilans, déclarations) : ces sujets adjacents sont du bruit.

Tu reçois un article (titre, source, thème pressenti, texte). Tu réponds UNIQUEMENT avec un objet JSON :
{
  "impact": 0 à 3,          // 0 = sans intérêt pour Matterhorn ; 1 = contexte utile ; 2 = impact concret
                            // sur Matterhorn ou sa verticale ; 3 = impact direct qui appelle une action
  "titre_fr": "...",        // le titre de l'article traduit fidèlement en français
                            // (recopié tel quel s'il est déjà en français)
  "resume": "...",          // exactement 2 phrases, en français, factuelles
  "pourquoi": "...",        // 1 ligne : pourquoi ça compte pour Matterhorn
  "tripwire": true/false,   // voir règles strictes ci-dessous
  "parking": true/false     // voir règle ci-dessous
}

Règles tripwire (STRICTES — la simple mention d'une marque ne suffit JAMAIS) :
a) Devizen ou Kanta annonce un support de la Belgique, de l'ITAA, ou de la lettre de mission belge.
b) L'écosystème Visma (Silverfin, AdminPulse, Accounton) annonce une fonctionnalité d'AVANT-VENTE :
   intake de prospect, proposition chiffrée, ou génération de lettre de mission.
Si aucune des deux conditions n'est remplie : tripwire = false.

Règle parking : true si l'article suggère une idée de fonctionnalité pour Matterhorn, ou décrit une
corvée administrative des cabinets HORS du périmètre avant-vente (une opportunité à garer, pas à
construire). Sinon false.

Tu ne complètes jamais avec des faits inventés sur Matterhorn, ses concurrents ou son marché."""


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {os.environ['KIMI_API_KEY']}"},
        timeout=120,
        transport=httpx.HTTPTransport(local_address="0.0.0.0", retries=1),
    )


def judge(client: httpx.Client, art) -> dict:
    user = (
        f"Thème pressenti : {art['theme']}\nSource : {art['source']}\nTitre : {art['title']}\n\n"
        f"Texte de l'article :\n{(art['content'] or '')[:6000]}\n\nRéponds avec l'objet JSON demandé."
    )
    last_err = None
    for attempt in range(3):
        try:
            r = client.post("/chat/completions", json={
                "model": MODEL,
                # le modèle « raisonne » d'abord : la réflexion consomme des tokens de sortie,
                # et sa longueur varie — on élargit le budget à chaque nouvel essai
                "max_tokens": (2000, 4000, 8000)[attempt],
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            })
            r.raise_for_status()
            out = json.loads(r.json()["choices"][0]["message"]["content"])
            return {
                "impact": max(0, min(3, int(out["impact"]))),
                "titre_fr": str(out.get("titre_fr") or "").strip(),
                "resume": str(out["resume"]).strip(),
                "pourquoi": str(out["pourquoi"]).strip(),
                "tripwire": bool(out["tripwire"]),
                "parking": bool(out["parking"]),
            }
        except (httpx.HTTPError, KeyError, ValueError) as e:
            last_err = e
    raise RuntimeError(f"Kimi en échec après 3 essais : {last_err}")


def run(limit: int | None = None) -> None:
    con = connect()
    q = "select * from articles where status='kept' order by tripwire desc, theme_score desc"
    rows = con.execute(q + (f" limit {int(limit)}" if limit else "")).fetchall()
    if not rows:
        print("rien à synthétiser (aucun article en statut 'kept')")
        return
    client = _client()
    for art in rows:
        try:
            v = judge(client, art)
        except RuntimeError as e:
            print(f"  ! {art['title'][:60]} : {e}", file=sys.stderr)
            continue
        con.execute(
            "update articles set impact=?, title_fr=?, summary=?, why=?, tripwire=?, parking=?, "
            "status='synthesized' where id=?",
            (v["impact"], v["titre_fr"] or None, v["resume"], v["pourquoi"],
             int(v["tripwire"]), int(v["parking"]), art["id"]),
        )
        con.commit()
        tag = "ALERTE " if v["tripwire"] else ("parking" if v["parking"] else f"impact {v['impact']}")
        print(f"  [{tag:8}] {(art['title'] or '')[:64]}")
        print(f"            {v['resume']}")
        print(f"            → {v['pourquoi']}")
