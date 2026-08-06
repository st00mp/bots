#!/usr/bin/env python3
"""
clean_md.py - Prépare un texte Markdown pour lecture audio.

Pipeline :
  1. Suppression du front matter YAML (--- ... ---)
  2. Nettoyage de la syntaxe Markdown
  3. Post-traitement ponctuation pour fluidité TTS

Règles de nettoyage Markdown :
  - Front matter YAML → supprimé entièrement
  - Titres (#, ##, ###) → texte nu (numéro de section également supprimé)
  - Blockquotes (> text) → texte nu
  - Gras/italique → texte nu
  - Inline code → texte nu
  - Blocs de code → "[code omis]"
  - Tableaux → phrases "header : valeur"
  - Liens → texte nu
  - Images → "[image : alt]" ou supprimé
  - Schémas ASCII → supprimés
  - HR (---) → supprimés
  - HTML → supprimé
  - Listes → texte nu + ponctuation terminale ajoutée

Règles ponctuation audio :
  - Em-dash (-) → virgule (pause naturelle)
  - Flèche (→) → "à" (pour séquences/ranges)
  - Slash (/) entre mots → virgule
  - Underscores dans noms → espaces
  - Extensions de fichiers .md/.py → supprimées
  - Chemins de fichiers → simplifiés
  - Éléments de liste sans ponctuation finale → point ajouté
"""

import re
import sys


# ─── Reflow paragraphes ─────────────────────────────────────────────────────

BLOCK_START = re.compile(
    r'^#{1,6}\s'        # titres
    r'|^\s*[-*+]\s'     # listes non ord.
    r'|^\s*\d+\.\s'     # listes ord.
    r'|^\s*>\s*'         # blockquotes
    r'|^\s*```'          # code fence
    r'|^\s*\|'           # tableaux
    r'|^[-*_=]{3,}\s*$'  # HR
)

def reflow_paragraphs(text: str) -> str:
    """
    Joint les soft line breaks (\n simple à l'intérieur d'un paragraphe ou d'un item)
    pour que le parsing inline fonctionne sur des lignes entières.
    Préserve les blancs, les débuts de blocs (titres, listes, blockquotes, etc.).
    """
    lines = text.split('\n')
    out = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append('')
            continue
        if BLOCK_START.match(line):
            out.append(line)
        elif out and out[-1]:  # continuation → coller à la ligne précédente
            out[-1] = out[-1].rstrip() + ' ' + stripped
        else:
            out.append(line)
    return '\n'.join(out)


# ─── Front matter ────────────────────────────────────────────────────────────

def strip_front_matter(text: str) -> str:
    """
    Supprime le bloc front matter YAML en début de note (--- ... ---).
    Gère aussi le front matter avec === ou +++.
    """
    text = text.lstrip('\n')
    for marker in ('---', '+++', '==='):
        if text.startswith(marker):
            # Chercher la fermeture
            end = text.find('\n' + marker, len(marker))
            if end != -1:
                return text[end + len(marker) + 1:].lstrip('\n')
            break
    return text


# ─── Schémas ASCII ───────────────────────────────────────────────────────────

def is_ascii_diagram(line: str) -> bool:
    diagram_chars = set('┌┐└┘├┤┬┴┼─│╔╗╚╝╠╣╦╩╬═║')
    if any(c in diagram_chars for c in line):
        return True
    stripped = line.strip()
    if len(stripped) > 4:
        special = sum(1 for c in stripped if c in '+-|=<>')
        alpha = sum(1 for c in stripped if c.isalpha())
        if special > 3 and alpha < special * 0.3:
            return True
    return False


# ─── Tableaux ────────────────────────────────────────────────────────────────

def parse_table(lines: list[str]) -> str:
    rows = []
    for line in lines:
        line = line.strip()
        if not line or set(line.replace(' ', '').replace('|', '').replace('-', '').replace(':', '')) == set():
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        cells = [c for c in cells if c]
        rows.append(cells)

    if not rows:
        return ''

    headers = rows[0]
    phrases = []
    for row in rows[1:]:
        parts = []
        for i, cell in enumerate(row):
            header = headers[i] if i < len(headers) else f'colonne {i+1}'
            if cell:
                parts.append(f'{header} : {cell}')
        if parts:
            phrases.append(', '.join(parts) + '.')
    return ' '.join(phrases)


# ─── Ponctuation terminale ───────────────────────────────────────────────────

def ensure_terminal_punct(line: str) -> str:
    """Ajoute un point si la ligne se termine sans ponctuation."""
    stripped = line.rstrip()
    if not stripped:
        return line
    # Regarder le dernier caractère significatif (ignorer ) et » en fin)
    check = stripped.rstrip(')\u00bb ')
    last = check[-1] if check else stripped[-1]
    if last in '.!?:;,»"\'':
        return line
    if stripped[-1] in ');':
        return line  # ligne se termine par ) ou ; → pas de point
    return stripped + '.'


# ─── Post-processing ponctuation audio ───────────────────────────────────────

# Caractères spéciaux utilisés
EM_DASH  = '—'   # U+2014 tiret cadratin
EN_DASH  = '–'   # U+2013 tiret demi-cadratin
ARROW    = '→'   # U+2192 flèche droite


def audio_polish(text: str) -> str:
    """
    Post-traitement après nettoyage Markdown pour améliorer la narration TTS.
    Opère sur le texte complet (multi-lignes).
    Les tirets ordinaires (U+002D) ne sont PAS touchés (mots composés français).
    """
    # Em-dash — (U+2014) → virgule
    text = text.replace(EM_DASH, ',')
    text = re.sub(r' ,', ',', text)

    # En-dash – (U+2013) entre alphanumériques → range "à" (AP1–AP14, 34–39)
    text = re.sub(r'(\w)\s*' + EN_DASH + r'\s*(\w)', r'\1 à \2', text)
    # En-dash – restant → virgule
    text = text.replace(EN_DASH, ',')

    # Flèche → (U+2192) → " à "
    text = re.sub(r'\s*' + ARROW + r'\s*', ' à ', text)

    # Slash entre mots → virgule
    text = re.sub(r'(?<=\w)\s*/\s*(?=\w)', ', ', text)

    # Underscores dans identifiants → espaces (Ai_Engineer_1 → Ai Engineer 1)
    text = re.sub(r'(?<=\w)_(?=\w)', ' ', text)

    # Extensions de fichiers → supprimer (.md, .py, etc.)
    text = re.sub(r'\b([\w][\w-]*)\.(md|py|json|yaml|yml|txt|js|ts|sh)\b', r'\1', text)

    # Références tickets (#34 à #34→39)
    text = re.sub(r'#(\d+)\s+à\s+(\d+)', r'numéros \1 à \2', text)
    text = re.sub(r'#(\d+)', r'numéro \1', text)

    # Guillemets droits ASCII "..." → guillemets typographiques
    text = re.sub(r'"([^"\n]{1,200})"', '« ' + r'\1' + ' »', text)

    # Astérisques résiduels multi-lignes (bold/italic non fermés)
    text = re.sub(r'\*{3}([^*]+)\*{3}', r'\1', text)
    text = re.sub(r'\*{2}([^*]+)\*{2}', r'\1', text)
    text = re.sub(r'(?<![\w])\*([^*\n]+)\*(?![\w])', r'\1', text)
    text = re.sub(r'\*{2,}', '', text)
    text = re.sub(r'(?<![\w])\*(?![\w*])', '', text)

    # Virgules doublées ou trùplées
    text = re.sub(r',\s*,+', ',', text)

    # Espaces multiples
    text = re.sub(r'  +', ' ', text)

    return text


# ─── Nettoyage principal ─────────────────────────────────────────────────────

def clean_markdown(text: str) -> str:
    # Reflow d'abord : joint les soft line breaks pour que le parsing inline soit complet
    text = reflow_paragraphs(text)
    lines = text.split('\n')
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- Blocs de code ---
        if line.strip().startswith('```'):
            lang = line.strip()[3:].strip()
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                i += 1
            result.append(f'Code {lang} omis.' if lang else 'Code omis.')
            i += 1
            continue

        # --- Tableaux ---
        if '|' in line and line.strip().startswith('|'):
            table_lines = []
            while i < len(lines) and '|' in lines[i] and lines[i].strip().startswith('|'):
                table_lines.append(lines[i])
                i += 1
            phrase = parse_table(table_lines)
            if phrase:
                result.append(phrase)
            continue

        # --- Schémas ASCII ---
        if is_ascii_diagram(line):
            i += 1
            continue

        # --- Lignes horizontales (---, ***, ===) ---
        if re.match(r'^[-*_=]{3,}\s*$', line.strip()):
            i += 1
            continue

        # --- HTML brut ---
        if re.match(r'^\s*<[^>]+>', line):
            i += 1
            continue

        # --- Blockquotes (> texte) ---
        if line.lstrip().startswith('>'):
            # Accumuler les lignes de blockquote consécutives
            bq_lines = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                bq_line = re.sub(r'^\s*>+\s?', '', lines[i])
                if bq_line.strip():
                    bq_lines.append(bq_line.strip())
                i += 1
            if bq_lines:
                # Traiter le contenu comme texte normal
                bq_text = ' '.join(bq_lines)
                # Nettoyage inline du blockquote
                bq_text = re.sub(r'\*{3}([^\*]+)\*{3}', r'\1', bq_text)
                bq_text = re.sub(r'\*{2}([^\*]+)\*{2}', r'\1', bq_text)
                bq_text = re.sub(r'__([^_]+)__', r'\1', bq_text)
                bq_text = re.sub(r'\*([^\*]+)\*', r'\1', bq_text)
                bq_text = re.sub(r'_([^_]+)_', r'\1', bq_text)
                bq_text = re.sub(r'`([^`]+)`', r'\1', bq_text)
                bq_text = ensure_terminal_punct(bq_text)
                result.append(bq_text)
            continue

        # --- Titres (supprime aussi le numéro de section "## 1. Titre" → "Titre") ---
        if re.match(r'^#{1,6}\s+', line):
            line = re.sub(r'^#{1,6}\s+', '', line)
            # Supprimer le numéro de section en tête (1., 2.1., etc.)
            line = re.sub(r'^\d+(?:\.\d+)*\.\s+', '', line)

        # --- Listes non ordonnées ---
        is_list_item = bool(re.match(r'^\s*[-*+]\s+', line))
        line = re.sub(r'^\s*[-*+]\s+', '', line)

        # --- Listes ordonnées ---
        if not is_list_item:
            is_list_item = bool(re.match(r'^\s*\d+\.\s+', line))
        line = re.sub(r'^\s*\d+\.\s+', '', line)

        # --- Images ---
        line = re.sub(r'!\[([^\]]*)\]\([^\)]*\)',
                      lambda m: f'image : {m.group(1)}.' if m.group(1) else '',
                      line)

        # --- Liens ---
        line = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', line)

        # --- Formatage inline ---
        line = re.sub(r'\*{3}([^\*]+)\*{3}', r'\1', line)
        line = re.sub(r'\*{2}([^\*]+)\*{2}', r'\1', line)
        line = re.sub(r'__([^_]+)__', r'\1', line)
        line = re.sub(r'\*([^\*]+)\*', r'\1', line)
        # Italique _text_ : seulement aux frontières de mots (ne pas avaler Ai_Engineer_1)
        line = re.sub(r'(?<![\w])_([^_]+)_(?![\w])', r'\1', line)
        line = re.sub(r'`([^`]+)`', r'\1', line)
        line = re.sub(r'~~([^~]+)~~', r'\1', line)

        # --- Ponctuation terminale sur les items de liste ---
        line = line.strip()
        if is_list_item and line:
            line = ensure_terminal_punct(line)

        if line:
            result.append(line)

        i += 1

    # Fusion + nettoyage espacement
    cleaned = '\n'.join(result)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)

    # Post-traitement ponctuation audio
    cleaned = audio_polish(cleaned)

    return cleaned.strip()


# ─── Découpage H2 ────────────────────────────────────────────────────────────

def split_by_h2(text: str) -> list[tuple[str, str]]:
    """
    Découpe le texte par sections H2 (## Titre), après suppression du front matter.
    Retourne une liste de (titre, contenu).
    Le contenu avant le premier ## va sous "Introduction".
    """
    # Suppression front matter AVANT découpage
    text = strip_front_matter(text)

    sections = []
    current_title = 'Introduction'
    current_lines = []

    for line in text.split('\n'):
        if line.startswith('## '):
            if current_lines:
                content = '\n'.join(current_lines).strip()
                if content:
                    sections.append((current_title, content))
            # Extraire titre propre (sans numéro de section)
            raw_title = line[3:].strip()
            raw_title = re.sub(r'^\d+(?:\.\d+)*\.\s+', '', raw_title)
            current_title = raw_title
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        content = '\n'.join(current_lines).strip()
        if content:
            sections.append((current_title, content))

    # Si rien trouvé (pas de H2), tout mettre dans "Note"
    if not sections:
        sections = [('Note', text.strip())]

    return sections


if __name__ == '__main__':
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            raw = f.read()
    else:
        raw = sys.stdin.read()

    cleaned = clean_markdown(strip_front_matter(raw))
    print(cleaned)
