"""CLI : python -m vigie <commande>"""
import sys


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "fetch":
        from . import fetch
        fetch.run()
    elif cmd == "filter":
        from . import filter as filt
        filt.run(apply="--dry" not in sys.argv)
    elif cmd == "synth":
        from . import synthesize
        limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
        synthesize.run(limit)
    elif cmd == "deliver":
        from . import deliver
        deliver.run()
    elif cmd == "run":
        from . import deliver, fetch, synthesize
        from . import filter as filt
        fetch.run()
        filt.run()
        synthesize.run()
        deliver.run()
    elif cmd == "test-alert":
        # chemin ALERTE de bout en bout : article factice tripwire → filtre → Kimi → digest
        from . import deliver, synthesize
        from . import filter as filt
        from .db import connect
        from .fetch import insert_article
        con = connect()
        con.execute("delete from articles where url like 'vigie://test%'")
        con.commit()
        insert_article(
            con, "test-alerte", "vigie://test-tripwire",
            "Kanta annonce le support des lettres de mission ITAA en Belgique",
            "",
            "Kanta ouvre son offre au marché belge : génération automatique de lettres de mission "
            "conformes à la déontologie ITAA pour les experts-comptables belges, avec modèles "
            "BeExcellent intégrés. Les cabinets belges peuvent désormais produire leur proposition "
            "chiffrée et leur lettre de mission depuis la plateforme Kanta.",
        )
        filt.run()
        synthesize.run()
        deliver.run()
        con.execute("delete from articles where url like 'vigie://test%'")
        con.commit()
        print("test ALERTE terminé, article factice purgé")
    elif cmd == "stats":
        from .db import connect
        con = connect()
        rows = con.execute(
            "select source, status, count(*) c from articles group by source, status order by source"
        ).fetchall()
        for r in rows:
            print(f"{r['source']:24} {r['status']:14} {r['c']}")
        total = con.execute("select count(*) from articles").fetchone()[0]
        print(f"{'TOTAL':24} {'':14} {total}")
    else:
        print("commandes : fetch | filter | synth | deliver | run | test-alert | stats")
        sys.exit(2)


main()
