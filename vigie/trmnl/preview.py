"""Rendu local du template TRMNL avec les données réelles du jour.
Usage (depuis la racine du projet) :
  docker compose run --rm -v ./trmnl:/app/trmnl vigie python trmnl/preview.py
Produit trmnl/preview.html, à capturer ensuite avec un Chromium headless."""
import pathlib
import sys

from liquid import Template

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from vigie.db import connect          # noqa: E402
from vigie.trmnl import build_payload  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent

# pas de plugins.css : le template est autonome, et le CSS framework masque le contenu
# hors de son pipeline de rendu ; .layout est stylé à la main pour occuper l'écran
SHELL = """<!doctype html>
<html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0}} body{{width:100vw;height:100vh;overflow:hidden}}
.layout{{width:100%;height:100%}}</style>
</head><body><div style="width:100vw;height:100vh">
{content}
</div></body></html>"""

payload = build_payload(connect())
if payload is None:
    sys.exit("aucun digest livré : rien à prévisualiser")
rendered = Template((BASE / "markup.html").read_text()).render(**payload)
(BASE / "preview.html").write_text(SHELL.format(content=rendered))
print("trmnl/preview.html généré")
