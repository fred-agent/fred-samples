# Notes — audit fred-samples vs fred-sdk (2026-07-24)

But : vérifier que les agents de `fred-samples` sont représentatifs du SDK
actuel (`fred-sdk` local 3.4.0), corriger ce qui est cassé/obsolète, et garder
une trace des points d'amélioration trouvés en chemin — même ceux non corrigés.

Ce fichier n'est pas committé automatiquement (pas de tracking git prévu pour
lui) — c'est un journal de travail pour Dimitri, à supprimer ou déplacer une
fois digéré.

---

## Fait ce tour-ci

### Serveurs MCP (`servers/mcp/python/*`)
- Les 5 serveurs (`bank_core`, `risk_guard`, `postal-service`, `iot-tracking`,
  `minimal`) étaient installés via `pip install "mcp[fastapi]"` **non
  versionné** dans chaque Makefile — aucun pin, dérive silencieuse possible.
- **Découverte** : l'extra `mcp[fastapi]` n'existe plus depuis un moment sur
  PyPI (1.28.1) — `starlette`/`uvicorn` sont maintenant des dépendances de
  base du paquet `mcp`, pas un extra. L'ancien pip install produisait un
  warning silencieux ignoré à chaque install.
- **Fix** : les 5 Makefiles pinnent maintenant `mcp==1.28.1`, testé de bout en
  bout (vrai handshake MCP `initialize` + `tools/list`/`tools/call`, pas
  juste un import). Aucun fix de code nécessaire — l'API FastMCP utilisée
  (`@server.tool()`, `server.streamable_http_app()`) est stable sur 1.28.1.
- **Bug non lié trouvé et corrigé** : `minimal-mcp-server` (censé être le
  template vide) s'identifiait dans son handshake MCP comme
  `serverInfo.name: "postal-mcp"` — copier-coller resté de
  `postal-service-mcp-server`. Corrigé en `"minimal-mcp"`.

### `hello_graph` et `team_of_3_agents_sample`
- `classify_step` (hello_graph) et les deux coordinateurs `TeamAgent`
  (`team_of_3`, mais le fix est en fait dans **fred-sdk lui-même**,
  `team_api.py` — voir `AGENT-THINKING-API-RFC.md` Amendment C) n'exposaient
  leur décision que via `context.emit_status(...)`, qui n'est **jamais**
  affiché dans l'UI ni persisté (signal fire-and-forget, visible seulement en
  `console.debug` navigateur). Ajout de `context.thinking("planning", ...)`
  en plus — visible dans le panneau "Thought…" du chat, testé en live.
- `team_of_3`'s trois enfants avaient chacun un marqueur texte
  `[ROUTED:...]` codé en dur dans leur prompt/sortie, uniquement pour
  permettre à un testeur de savoir qui avait répondu — ça fuitait dans la
  réponse visible. Retiré, remplacé par la conclusion du thought du
  coordinateur (`AgentSpec.name`, ex. "Routing to Math Conversion
  Specialist.").
- `cvem_watch` supprimé entièrement (agent, registre, catalogue MCP, README)
  à la demande de Dimitri.

---

## ⚠️ Point bloquant à trancher — floor de version `fred-sdk`

`agents/pyproject.toml` déclare `fred-sdk>=3.3.5`. Le comportement
`team_of_3` compte maintenant sur `context.thinking()` appelé **à
l'intérieur du coordinateur** — ça n'existe que depuis `fred-sdk` 3.4.0
(commit local `7633ca5a` dans `fred`, **pas encore publié**).

- Sur 3.3.5 (ce que `pip`/`uv` résoudrait normalement aujourd'hui), rien ne
  casse — mais la trace de routage redevient invisible, silencieusement,
  sans erreur. Le README de `team_of_3` documente maintenant un comportement
  qui n'existe que si on a `make dev-local` + fred-sdk 3.4.0 local.
- Je n'ai **pas** monté le floor à `>=3.4.0` dans `pyproject.toml` : tant que
  3.4.0 n'est pas publié, ça casserait `uv sync`/`pip install` pour quiconque
  n'a pas `../../fred` en checkout sibling (résolution de dépendance
  impossible).
- **Décision à prendre** : soit publier `fred-sdk` 3.4.0 (alors bump le floor
  ici), soit documenter clairement dans `agents/pyproject.toml` /
  `agents/AGENTS.md` que `team_of_3` nécessite `make dev-local` tant que ce
  n'est pas publié.

## ⚠️ Bug d'infra déjà signalé — `uv.lock` écrase l'install locale

`agents/uv.lock` pointe `fred-sdk` sur la source PyPI (3.3.5). `make run`
appelle `uv run` (sans `--no-sync`), qui resynchronise silencieusement le
venv sur ce lock à chaque lancement — donc `make dev-local` ne "tient" pas
au démarrage suivant. Contournement actuel : lancer avec `uv run --no-sync`
directement. Pas encore corrigé dans le Makefile (proposé, pas encore fait
— voir conversation).

---

## Audits terminés

- [x] `bank_transfer` — deux noeuds avaient le même trou que hello_graph
      avant fix : `analyze_intent_step` (classification transfert vs
      conversationnel) et `evaluate_risk_step` (score de risque, branche
      `low_risk` totalement muette — aucune trace nulle part, pas même dans
      `final_text`). Les deux enveloppés dans `context.thinking("planning",
      ...)`, `emit_status` conservé. HITL gates (`confirm_risk_step`,
      `confirm_transfer_step`) volontairement laissées sans thinking — ce
      sont des pauses humaines, pas du raisonnement agent, comme pour
      hello_graph/team_of_3.
      **Point ouvert laissé par l'agent** : `check_kyc_step` (branche
      `valid`/`blocked`, décision de conformité) est un candidat plus fort
      que `load_account_step` (vérification technique d'existence de
      compte) pour le même traitement — décision de goût, pas fait
      automatiquement.
- [x] `postal_tracking` — même trou dans `analyze_intent_step` (classification
      + heuristiques de mots-clés + promotion conversational→track_request)
      et `load_tracking_step` (éligibilité au reroute — n'avait même pas
      d'`emit_status`). `analyze_intent_step` en phase `"planning"`,
      `load_tracking_step` en phase `"reflection"` (décision après résultat
      d'outil). Carte/GeoJSON confirmée conforme au pattern sanctionné pour
      un agent Graph (`GraphExecutionOutput.ui_parts`, pas
      `TOOL_REF_GEO_RENDER_POINTS` qui est pour les agents ReAct). HITL gate
      (`confirm_reroute_step`) confirmée conforme (`choice_step` actuel).
- [x] `general_assistant` — RAS niveau SDK. Fix : docstring d'exemple
      (`from fred_agents.general_assistant import ...`) copiée-collée depuis
      l'agent homonyme du repo `fred` principal (`apps/fred-agents/`, un
      agent différent et bien plus riche) — corrigée en
      `fred_samples_agents.general_assistant`. Vérifié par instantiation +
      `inspect_agent()`/`.policy()`/`.preview()` directs contre fred-sdk
      3.4.0 local, tout passe.

*(section complétée après retour des agents)*

---

## Trouvailles non corrigées (hors scope des fichiers audités, à trancher)

- **`README.md`** — `General Assistant` affiche `Agent ID: assistant` au lieu
  du vrai `fred.samples.assistant` (seul intrus, tous les autres samples du
  README utilisent le bon id complet).
- **`README.md`** — instruction `make chat`, mais `agents/Makefile` n'a pas
  de cible `chat` (c'est `make cli`). Le docstring interne de
  `general_assistant.py` a la bonne commande (`fred-agents-cli`) — l'erreur
  n'est que dans le README.
- **Aucun test** dans `fred-samples/agents` — pas de dossier `tests/` du
  tout. Rien n'exerce automatiquement `general_assistant` (ou les autres
  samples) au-delà de l'import dans `registry.py`.
- `general_assistant.py` se décrit en docstring comme "the reference example
  for the BOOTSTRAP guide" — aucun "BOOTSTRAP guide" identifiable dans
  `fred-samples` ni `fred-website`. Soit ça pointe vers le README lui-même,
  soit c'est une référence aspirationnelle à clarifier/retirer.
- **Gap SDK réel** : `postal_tracking` importe `GeoPart` et
  `BoundRuntimeContext` depuis `fred_sdk.contracts.context` — ni l'un ni
  l'autre n'est ré-exporté par `fred_sdk/__init__.py`. Ce ne sont pas des
  imports qui contournent une alternative publique par erreur : il n'y a
  simplement pas d'alternative publique. Puisqu'au moins un sample en a
  besoin pour le rendu carte, ça vaut la peine de se demander si
  `fred_sdk/__init__.py` devrait les exporter — question fred-sdk, pas
  fred-samples, donc hors du périmètre de cette tâche.
- **`__init__.py` incohérents** : `hello_graph/__init__.py` et
  `team_of_3_agents_sample/__init__.py` portent l'en-tête de licence Apache ;
  `postal_tracking/__init__.py` et `bank_transfer/__init__.py` sont vides
  (0 octet). Pas un bug fonctionnel, juste une incohérence de repo — à
  uniformiser si quelqu'un fait un passage cosmétique un jour.
- **Confirmation croisée du point bloquant plus haut** : les deux audits
  `bank_transfer` et `postal_tracking`, indépendamment, ont re-signalé que
  `agents/pyproject.toml`'s `fred-sdk>=3.3.5` est maintenant sous-évalué —
  après ce tour, **4 des 5 agents** (`hello_graph`, `team_of_3`,
  `bank_transfer`, `postal_tracking`) utilisent `context.thinking()`, qui
  ne s'exécute silencieusement pas sur 3.3.5. Deux audits distincts sont
  arrivés à la même conclusion sans se coordonner — assez fort signal que
  ce n'est pas un faux positif : la décision de publier 3.4.0 (ou de
  documenter la dépendance à `make dev-local`) devient plus urgente
  maintenant que la quasi-totalité des samples en dépend.
