"""Scheduler: encola trabajos segun las expresiones cron de settings.yml."""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Config
from .queue import JobQueue

logger = logging.getLogger("geopulse.scheduler")

_CRON_JOBS = {
    "collect_instagram_cron": "collect_instagram",
    "collect_facebook_cron": "collect_facebook",
    "discover_cron": "discover",
    "enrich_cron": "enrich",
    "trends_cron": "trends",
    "report_cron": "report",
}


def build_scheduler(config: Config) -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="America/Santo_Domingo")
    queue = JobQueue(config.queue_path)

    for attr, job_type in _CRON_JOBS.items():
        cron = getattr(config.settings.scheduler, attr)
        if not cron:
            continue
        scheduler.add_job(
            lambda jt=job_type: queue.enqueue(jt),
            CronTrigger.from_crontab(cron),
            id=job_type,
            replace_existing=True,
        )
        logger.info("Programado %s con cron '%s'", job_type, cron)

    return scheduler


def run_scheduler(config: Config) -> None:
    scheduler = build_scheduler(config)
    logger.info("Scheduler iniciado. Ctrl+C para salir.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler detenido.")
