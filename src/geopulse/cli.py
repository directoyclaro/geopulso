"""Interfaz de linea de comandos de GeoPulse."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from .config import get_config
from .keywords import add_trend_keywords, suggest_from_trends
from .llm.ollama_client import OllamaClient
from .locations import seed_locations
from .topics import seed_topics
from .orchestrator.jobs import run_job
from .orchestrator.queue import JobQueue
from .pipeline import (
    build_store,
    collect_and_process,
    collect_comments,
    discover_places,
    discover_profiles,
    regeo_all,
    run_discovery,
    run_enrichment,
    run_report,
    run_trends,
    scan_profile,
    search_comments,
    search_content,
    search_keywords,
)


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )


def cmd_init_db(args: argparse.Namespace) -> None:
    from .collectors.discovery import load_seed_sources

    config = get_config()
    store = build_store(config)
    count = load_seed_sources(config, store)
    locations = seed_locations(config, store)
    topics = seed_topics(config, store)
    print(
        f"Esquema inicializado. Fuentes: {count} | Ubicaciones: {locations} | Temas: {topics}"
    )
    store.close()


def cmd_discover(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    total = run_discovery(config, store)
    print(f"Descubrimiento completado. Fuentes en base: {total}")
    store.close()


def cmd_collect(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = collect_and_process(config, store, args.platform, amount=args.amount)
    print(f"Recoleccion {args.platform}: {result}")
    store.close()


def cmd_check_accounts(args: argparse.Namespace) -> None:
    from .collectors.base import AccountManager

    config = get_config()
    manager = AccountManager(config)
    problems = manager.validate()
    if not manager.accounts:
        print("No hay config/secrets/accounts.yml. Copia accounts.example.yml y rellena tus cuentas.")
        return
    print("Cuentas cargadas:")
    for platform, items in manager.accounts.items():
        print(f"  - {platform}: {len(items)} cuenta(s)")
    if problems:
        print("\nProblemas detectados:")
        for platform, issues in problems.items():
            for issue in issues:
                print(f"  - {platform}: {issue}")
    else:
        print("\nEstructura de accounts.yml correcta.")


def cmd_resolve_locations(args: argparse.Namespace) -> None:
    from .collectors.discovery import DiscoveryAgent

    config = get_config()
    store = build_store(config)
    result = DiscoveryAgent(config, store).resolve_locations()
    print(f"Locations resueltas: {result}")
    store.close()


def cmd_clean_posts(args: argparse.Namespace) -> None:
    from .utils.text import clean_social_text, simhash64

    config = get_config()
    store = build_store(config)
    rows = store.query("SELECT raw_id, text FROM posts")
    cleaned_count = 0
    for raw_id, text in rows:
        cleaned = clean_social_text(text or "")
        if cleaned != text:
            store.conn.execute(
                "UPDATE posts SET text = ?, simhash = ? WHERE raw_id = ?",
                [cleaned, simhash64(cleaned), raw_id],
            )
            cleaned_count += 1
    print(f"Posts revisados: {len(rows)}, limpiados: {cleaned_count}")
    store.close()


def cmd_enrich(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    if getattr(args, "force", False):
        store.conn.execute("DELETE FROM posts_enriched")
        print("posts_enriched limpiado (--force).")
    result = run_enrichment(config, store, limit=args.limit)
    print(f"Enriquecimiento: {result}")
    store.close()


def cmd_manual_login(args: argparse.Namespace) -> None:
    from .collectors.base import AccountManager

    config = get_config()
    manager = AccountManager(config)
    account = manager.current(args.platform)
    if not account:
        print(f"No hay cuenta configurada para {args.platform} en accounts.yml.")
        return
    session_path = manager.session_path(args.platform, account)
    if args.platform == "instagram":
        from .collectors.instagram import ig_manual_login

        sessionid = ig_manual_login(config, account, session_path, timeout=args.timeout)
        print("Login de Instagram completado." if sessionid else "Login no completado (timeout).")
    else:
        from .collectors.facebook import login_facebook

        try:
            login_facebook(config, account, session_path, headless=False)
            print("Login de Facebook completado.")
        except Exception as exc:  # noqa: BLE001
            print(f"Login de Facebook no completado: {exc}")


def cmd_locations(args: argparse.Namespace) -> None:
    from .utils.text import slugify

    config = get_config()
    store = build_store(config)
    if args.action == "seed":
        print(f"Ubicaciones sembradas/actualizadas: {seed_locations(config, store)}")
    elif args.action == "list":
        for loc in store.get_locations(active_only=False):
            estado = "activa" if loc["active"] else "inactiva"
            print(f"- {loc['location_id']}: {loc['name']} ({loc['level']}, mun={loc['municipio']}) {estado}")
    elif args.action == "add":
        if not args.name:
            print("Falta --name")
        else:
            lid = store.upsert_location(
                {
                    "name": args.name,
                    "level": args.level,
                    "municipio": args.municipio,
                    "lat": args.lat,
                    "lon": args.lon,
                    "aliases": [a.strip() for a in args.aliases.split(",")] if args.aliases else [],
                }
            )
            print(f"Ubicacion agregada: {lid}")
    else:
        if not args.name:
            print("Falta --name (id o nombre de la ubicacion)")
        else:
            lid = slugify(args.name)
            if args.action == "remove":
                print("Eliminada" if store.remove_location(lid) else "No existe", lid)
            else:
                store.set_location_active(lid, args.action == "enable")
                print(f"{args.action}: {lid}")
    store.close()


def cmd_topics(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    if args.action == "seed":
        print(f"Temas sembrados/actualizados: {seed_topics(config, store)}")
    elif args.action == "list":
        for topic in store.get_topics(active_only=False):
            print(f"- {topic['topic_id']}: {topic['label']} activo={topic['active']} seleccionado={topic['selected']}")
    elif args.action == "add":
        if not args.label:
            print("Falta --label")
        else:
            tid = store.upsert_topic(
                {
                    "label": args.label,
                    "taxonomy_id": args.taxonomy or args.label,
                    "keywords": [k.strip() for k in args.keywords.split(",")] if args.keywords else [],
                }
            )
            print(f"Tema agregado: {tid}")
    else:
        if not args.name:
            print("Falta --name (id o etiqueta del tema)")
        else:
            from .utils.text import slugify

            tid = slugify(args.name)
            if args.action == "remove":
                print("Eliminado" if store.remove_topic(tid) else "No existe", tid)
            elif args.action == "select":
                store.set_topic_selected(tid, True)
                print(f"seleccionado: {tid}")
            elif args.action == "deselect":
                store.set_topic_selected(tid, False)
                print(f"deseleccionado: {tid}")
            else:
                store.set_topic_active(tid, args.action == "enable")
                print(f"{args.action}: {tid}")
    store.close()


def cmd_comments(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = collect_comments(config, store, args.target, amount=args.amount)
    print(f"Comentarios: {result}")
    store.close()


def cmd_search_comments(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    rows = search_comments(config, store, args.term, limit=args.limit)
    if not rows:
        print(f"Sin comentarios que contengan '{args.term}'.")
    for row in rows:
        print(f"[{row['platform']}] {row['text'][:120]}")
    store.close()


def cmd_keywords(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    if args.action == "list":
        for k in store.get_keywords(active_only=False):
            estado = "activa" if k["active"] else "inactiva"
            print(f"- {k['term']}  [{k['source']}] {estado} peso={k['weight']}")
    elif args.action == "add":
        for term in args.terms:
            store.upsert_keyword(term, "manual")
            print(f"Anadida: {term}")
    elif args.action == "remove":
        for term in args.terms:
            print(f"{'Eliminada' if store.remove_keyword(term) else 'No existe'}: {term}")
    elif args.action == "suggest":
        terms = suggest_from_trends(config, store, limit=args.limit)
        print("Sugerencias (tendencias/temas/lugares):")
        for term in terms:
            print(f"- {term}")
        if args.add:
            n = add_trend_keywords(config, store, limit=args.limit)
            print(f"Registradas {n} keywords con source=trend.")
    elif args.action == "search":
        result = search_keywords(config, store, amount=args.amount, limit=args.limit)
        print(f"Busqueda por keywords: {result}")
    store.close()


def cmd_discover_places(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    platforms = ("instagram", "facebook") if args.platform == "both" else (args.platform,)
    result = discover_places(
        config, store, platforms=platforms, per_place=args.per_place, reset=args.reset
    )
    print(f"Descubrimiento por municipios/distritos: {result}")
    store.close()


def cmd_search(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = search_content(config, store, args.query, amount=args.amount)
    print(f"Busqueda '{args.query}': {result}")
    store.close()


def cmd_scan_profile(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
    result = scan_profile(config, store, args.handle, amount=args.amount, keywords=keywords)
    print(f"Perfil @{args.handle}: {result}")
    store.close()


def cmd_search_profiles(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = discover_profiles(config, store, args.term, amount=args.amount)
    print(f"Perfiles encontrados: {result['found']} (registrados {result['added']})")
    for user in result["users"]:
        print(f"- https://www.instagram.com/{user}/")
    store.close()


def cmd_regeo(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = regeo_all(config, store)
    print(f"Re-geolocalizacion: {result}")
    store.close()


def cmd_trends(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = run_trends(config, store)
    print(f"Tendencias: {result}")
    store.close()


def cmd_alerts(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    alerts = store.get_alerts(limit=args.limit)
    if not alerts:
        print("Sin alertas (bursts) por ahora.")
    for a in alerts:
        print(
            f"[{a['zona']}] {a['tema']}: vol={a['volumen']} esp={a['esperado']} "
            f"z={a['zscore']} ratio={a['ratio']}"
        )
    store.close()


def cmd_report(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    result = run_report(config, store)
    print(f"Informe generado: {result['report']}")
    store.close()


def cmd_ollama_check(args: argparse.Namespace) -> None:
    config = get_config()
    client = OllamaClient(
        base_url=config.llm.base_url,
        model=config.llm.model,
        timeout=config.llm.timeout_seconds,
    )
    if not client.is_available():
        print(f"Ollama NO responde en {config.llm.base_url}. Instala/levanta Ollama y reintenta.")
        return
    models = client.list_models()
    print(f"Ollama OK en {config.llm.base_url}")
    print(f"Modelo configurado: {config.llm.model}")
    print("Modelos disponibles:", ", ".join(models) or "(ninguno)")
    if config.llm.model not in models:
        print(f"AVISO: '{config.llm.model}' no esta descargado. Ejecuta: ollama pull {config.llm.model}")


def cmd_worker(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    queue = JobQueue(config.queue_path)
    print("Worker iniciado. Ctrl+C para salir.")
    try:
        while True:
            job = queue.dequeue()
            if job is None:
                if args.once:
                    break
                import time

                time.sleep(5)
                continue
            try:
                result = run_job(config, store, job["job_type"], job["payload"])
                queue.complete(job["id"])
                print(f"Job #{job['id']} {job['job_type']} -> {result}")
            except Exception as exc:  # noqa: BLE001
                queue.fail(job["id"], str(exc))
                print(f"Job #{job['id']} fallo: {exc}")
    except KeyboardInterrupt:
        print("Worker detenido.")
    finally:
        store.close()
        queue.close()


def cmd_schedule(args: argparse.Namespace) -> None:
    from .orchestrator.scheduler import run_scheduler

    run_scheduler(get_config())


def cmd_dashboard(args: argparse.Namespace) -> None:
    app = Path(__file__).resolve().parent / "dashboard" / "app.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(app)], check=False)


def cmd_stats(args: argparse.Namespace) -> None:
    config = get_config()
    store = build_store(config)
    for label, sql in {
        "fuentes": "SELECT COUNT(*) FROM sources",
        "posts": "SELECT COUNT(*) FROM posts",
        "posts_geo": "SELECT COUNT(*) FROM posts_geo",
    }.items():
        value = store.query(sql)[0][0]
        print(f"{label}: {value}")
    store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="geopulse", description="GeoPulse - scraping y analisis por zona")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Inicializa DuckDB y carga fuentes semilla").set_defaults(func=cmd_init_db)
    sub.add_parser("discover", help="Ejecuta el agente descubridor").set_defaults(func=cmd_discover)
    sub.add_parser("stats", help="Resumen de datos").set_defaults(func=cmd_stats)
    sub.add_parser("ollama-check", help="Comprueba conectividad con Ollama").set_defaults(func=cmd_ollama_check)
    sub.add_parser("check-accounts", help="Valida config/secrets/accounts.yml").set_defaults(func=cmd_check_accounts)
    sub.add_parser("clean-posts", help="Limpia ruido de UI en el texto de posts").set_defaults(func=cmd_clean_posts)
    sub.add_parser("regeo", help="Recalcula la geolocalizacion de los posts").set_defaults(func=cmd_regeo)
    sub.add_parser("trends", help="Calcula tendencias por zona").set_defaults(func=cmd_trends)
    sub.add_parser("report", help="Genera informe HTML").set_defaults(func=cmd_report)

    p_alerts = sub.add_parser("alerts", help="Lista alertas (bursts)")
    p_alerts.add_argument("--limit", type=int, default=50)
    p_alerts.set_defaults(func=cmd_alerts)

    p_manual = sub.add_parser("manual-login", help="Login manual asistido (Instagram con codigo por email)")
    p_manual.add_argument("platform", choices=["instagram", "facebook"])
    p_manual.add_argument("--timeout", type=int, default=300)
    p_manual.set_defaults(func=cmd_manual_login)

    p_kw = sub.add_parser("keywords", help="Gestiona palabras clave (manuales o por tendencias)")
    p_kw.add_argument("action", choices=["list", "add", "remove", "suggest", "search"])
    p_kw.add_argument("terms", nargs="*", help="Terminos para add/remove")
    p_kw.add_argument("--add", action="store_true", help="(suggest) registrar las sugerencias")
    p_kw.add_argument("--limit", type=int, default=20)
    p_kw.add_argument("--amount", type=int, default=10, help="(search) posts por keyword")
    p_kw.set_defaults(func=cmd_keywords)

    p_dp = sub.add_parser("discover-places", help="Descubre perfiles/paginas por municipio y distrito")
    p_dp.add_argument("--platform", choices=["instagram", "facebook", "both"], default="both")
    p_dp.add_argument("--per-place", type=int, default=8, dest="per_place")
    p_dp.add_argument("--reset", action="store_true", help="Borra descubrimientos previos antes de re-descubrir")
    p_dp.set_defaults(func=cmd_discover_places)

    p_loc = sub.add_parser("locations", help="Gestiona ubicaciones geograficas")
    p_loc.add_argument("action", choices=["seed", "list", "add", "remove", "enable", "disable"])
    p_loc.add_argument("--name")
    p_loc.add_argument("--level", default="custom")
    p_loc.add_argument("--municipio")
    p_loc.add_argument("--lat", type=float)
    p_loc.add_argument("--lon", type=float)
    p_loc.add_argument("--aliases", help="separados por coma")
    p_loc.set_defaults(func=cmd_locations)

    p_top = sub.add_parser("topics", help="Gestiona temas de conversacion")
    p_top.add_argument("action", choices=["seed", "list", "add", "remove", "select", "deselect", "enable", "disable"])
    p_top.add_argument("--name")
    p_top.add_argument("--label")
    p_top.add_argument("--taxonomy")
    p_top.add_argument("--keywords", help="separados por coma")
    p_top.set_defaults(func=cmd_topics)

    p_com = sub.add_parser("comments", help="Recolecta comentarios de una publicacion publica")
    p_com.add_argument("target", help="URL de Instagram o Facebook")
    p_com.add_argument("--amount", type=int, default=50)
    p_com.set_defaults(func=cmd_comments)

    p_sc = sub.add_parser("search-comments", help="Busca un termino en los comentarios")
    p_sc.add_argument("term")
    p_sc.add_argument("--limit", type=int, default=100)
    p_sc.set_defaults(func=cmd_search_comments)

    p_search = sub.add_parser("search", help="Busca contenido por palabra clave (Instagram)")
    p_search.add_argument("query")
    p_search.add_argument("--amount", type=int, default=20)
    p_search.set_defaults(func=cmd_search)

    p_scan = sub.add_parser("scan-profile", help="Recorre los posts de un perfil publico")
    p_scan.add_argument("handle")
    p_scan.add_argument("--amount", type=int, default=30)
    p_scan.add_argument("--keywords", default=None, help="Filtro separado por comas")
    p_scan.set_defaults(func=cmd_scan_profile)

    p_sp = sub.add_parser("search-profiles", help="Busca perfiles publicos por termino")
    p_sp.add_argument("term")
    p_sp.add_argument("--amount", type=int, default=20)
    p_sp.set_defaults(func=cmd_search_profiles)
    sub.add_parser("resolve-locations", help="Resuelve locations de Instagram por nombre").set_defaults(
        func=cmd_resolve_locations
    )

    p_enrich = sub.add_parser("enrich", help="Enriquece posts y recalcula preferencias")
    p_enrich.add_argument("--limit", type=int, default=500)
    p_enrich.add_argument("--force", action="store_true", help="Re-enriquece todo (borra resultados previos)")
    p_enrich.set_defaults(func=cmd_enrich)
    sub.add_parser("schedule", help="Inicia el scheduler de tareas").set_defaults(func=cmd_schedule)
    sub.add_parser("dashboard", help="Abre el dashboard Streamlit").set_defaults(func=cmd_dashboard)

    p_collect = sub.add_parser("collect", help="Recolecta y procesa una plataforma")
    p_collect.add_argument("platform", choices=["instagram", "facebook"])
    p_collect.add_argument("--amount", type=int, default=30)
    p_collect.set_defaults(func=cmd_collect)

    p_worker = sub.add_parser("worker", help="Procesa la cola de trabajos")
    p_worker.add_argument("--once", action="store_true", help="Procesa pendientes y sale")
    p_worker.set_defaults(func=cmd_worker)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
