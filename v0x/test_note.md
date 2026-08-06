# Infra agentique — État du système

Note de référence pour le projet OpenClaw.

## Architecture générale

Le système repose sur deux nœuds distincts :
- **Orange Pi** : orchestrateur central, source de vérité
- **UM690** : worker IA, exécution des modèles

La communication entre les deux passe par _Tailscale_ et SSH.

| Nœud      | Rôle           | OS      |
|-----------|----------------|---------|
| Orange Pi | Orchestrateur  | Armbian |
| UM690     | Worker IA      | Debian  |

## Stack technique

Les outils principaux utilisés :

- `Docker` pour l'isolation des services
- `Ollama` pour l'inférence locale
- `OpenClaw` comme runtime d'agents

Exemple de configuration Docker :

```yaml
services:
  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
```

Plus de détails sur [la documentation officielle](https://docs.openclaw.ai).

## Feuille de route

L'objectif est de progresser niveau par niveau :

1. Agent simple (L1)
2. Agent avec outils (L2)
3. Connecté au homelab (L3)
4. Multi-agents (L4)

La règle absolue : **ne jamais sauter un niveau** sans livrable validé.
