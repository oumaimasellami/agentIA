# NRT Scope - Traçabilité Fonctions (Parser + Diff + Swagger)

## 1) Objectif
Cette documentation explique, avec preuve technique, comment le système NRT déduit:
- les lignes réellement modifiées dans un commit Azure DevOps,
- les fonctions impactées dans le code,
- la description métier affichée pour aider les testeurs de non-régression.

Le principe est **strict Azure/code source**, sans invention de données.

---

## 2) Sources de vérité (evidence)

## 2.1 Azure DevOps (Git)
Les données commit viennent de `01_EXTRACTION/step1_azure_extract.py`:
- Commit: `/commits/{commitId}`
- Fichiers modifiés: `/commits/{commitId}/changes`
- Contenu fichier au commit: `/items?versionDescriptor.versionType=commit&versionDescriptor.version={commitId}`
- Contenu fichier au parent: même endpoint avec `parent_commit_id`

Sortie stockée dans `01_EXTRACTION/commits.json`:
- `commit_id`, `parent_commit_id`, `fichiers_modifies[]`
- pour chaque fichier: `fichier`, `action`, `changed_lines[]` (si activé)

## 2.2 Parser fonctions (code source)
Les fonctions sont parsées depuis `source_mesx_*.json` par `01_EXTRACTION/populate_graph_data.py`.
Chaque fonction reçoit:
- `id` (ex: `mesx-production-api::down_time_ticket_controller::findAll`)
- `line_start`, `line_end`
- type/méthode/route HTTP quand détectable

Sortie stockée dans `01_EXTRACTION/graph_data.json`.

## 2.3 Enrichissement UI/API NRT
`05_WEB_INTERFACE/server.py` combine:
- `commits.json` (preuve commit/fichier/lignes),
- `graph_data.json` (preuve fonction et bornes de lignes),
- `source_mesx_*.json` (texte source pour description, Swagger decorators).

---

## 3) Comment les `changed_lines` sont calculées

Dans `step1_azure_extract.py`:

1. Récupérer le contenu du fichier au commit courant (`new_content`).
2. Récupérer le contenu du même fichier au parent commit (`old_content`).
3. Calculer un diff déterministe avec `difflib.SequenceMatcher`.
4. Conserver les lignes de la nouvelle version touchées:
   - `replace` / `insert` -> lignes `j1..j2`
   - `delete` -> ligne d’ancrage (pour garder une trace locale)
5. Dédupliquer + trier -> `changed_lines`.

Donc `changed_lines` n’est pas saisi manuellement: il est calculé à partir de 2 versions Git Azure réelles.

---

## 4) Comment on passe des lignes aux fonctions

Dans `server.py`:

1. Pour un fichier code touché, charger la liste des fonctions parsées de ce fichier (`line_start`, `line_end`).
2. Matcher chaque ligne `L` de `changed_lines`:
   - si `line_start <= L <= line_end`, la fonction est marquée **touchée prouvée**.
3. Si `changed_lines` est vide (`no_line_evidence`) mais fichier code touché:
   - la fonction est marquée **candidate** (impact potentiel), pas preuve ligne.

Classification affichée:
- **P1** = touchée prouvée (preuve ligne),
- **P2** = candidate liée au fichier commit (sans preuve ligne),
- **P3** = impact infra/config.

---

## 5) Exemple réel: commit `70224c2e...`

Commit:
- `commit_id`: `70224c2ed7aef8442a49d2b7370859f697f5d769`
- `microservice`: `mesx-production-api`
- `parent_commit_id`: `66de38c54fa99bc0172759c2a3d9febf0aeb0336`

Fichier touché:
- `/src/modules/down-time-ticket/down-time-ticket.controller.ts`

Lignes calculées:
- ex: `64, 67, 68, ..., 120` (49 lignes)

Fonctions du fichier (parser):
- `create` (54-66)
- `findAll` (70-72)
- `UseFilters` (92-100)
- `getNonClassifiedTicket` (104-125)
- etc.

Match ligne->fonction:
- ligne 64 -> `create`
- lignes 70-72 -> `findAll`
- lignes 92-100 -> `UseFilters`
- lignes 104-120 -> `getNonClassifiedTicket`

Conclusion:
- ces fonctions passent en **P1 (touchée prouvée)** pour ce commit.

---

## 6) Comment Swagger est utilisé pour la description métier

La description métier n’est pas inventée “LLM-only”.
Le serveur lit le code source local (`source_mesx_*.json`) et extrait:
- `@ApiOperation(summary: ..., description: ...)`
- décorateurs endpoint (`@Get`, `@Post`, etc.)
- route/path + méthode HTTP

Le texte NRT est construit à partir de ces éléments réels:
- nom fonction,
- endpoint HTTP,
- summary/description Swagger si présent,
- contexte fichier/commit.

Si Swagger absent:
- fallback sur nom de fonction + endpoint + contexte fichier
- avec mention explicite de l’absence de preuve ligne si nécessaire.

---

## 7) Garanties anti-heuristiques

Le système ne “devine” pas une fonction touchée sans preuve:
- pas de `changed_lines` => jamais classé P1.
- dans ce cas, classification P2 explicite (“candidate sans preuve ligne”).

Pour infra/config:
- pas de fonction code inventée.
- sortie orientée microservice cible + blocs infra touchés (P3).

---

## 8) Limites connues et lecture correcte

1. Si commit lié au WorkItem mais absent de `commits.json`, il faut backfill.
2. Si API Azure ne retourne pas de diff exploitable pour ce commit/fichier:
   - `line_match_mode = no_line_evidence`
   - classification P2 (candidate) au lieu de P1.
3. Un PR abandonné peut contenir des commits valides techniquement; ils restent traçables s’ils sont liés au WorkItem.

---

## 9) Commandes de vérification rapide

Vérifier qu’un commit est lié au WI:
```bat
findstr /N /I "70224c2e" 01_EXTRACTION\workitem_dev_links.json
```

Vérifier qu’il est enrichi côté commits:
```bat
findstr /N /I "70224c2e" 01_EXTRACTION\commits.json
```

Vérifier l’API NRT:
```bat
powershell -NoProfile -Command "(Invoke-WebRequest -UseBasicParsing 'http://localhost:5000/api/nrt-scope?workitem_id=90793').Content"
```

---

## 10) Résumé exécutif (à dire en réunion)

1. WorkItem -> PR/Commit via liens natifs Azure.
2. Commit -> fichiers touchés via Azure Git Changes.
3. Lignes modifiées calculées par diff réel entre commit et parent.
4. Fonctions impactées déterminées par intersection lignes diff vs bornes `line_start/line_end`.
5. Description métier enrichie avec Swagger du code source.
6. Sortie classée P1/P2/P3 pour transparence de preuve.

