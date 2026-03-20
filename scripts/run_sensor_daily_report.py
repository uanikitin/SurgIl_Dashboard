"""
Ежесуточный расчёт отчётов по датчикам.

Запуск: venv/bin/python scripts/run_sensor_daily_report.py [YYYY-MM-DD]
Без аргумента — считает за вчера.

Предполагается запуск из cron/launchd ежедневно в 01:00 по местному.
"""
import sys
import os
import logging
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("sensor_daily_report")


def main():
    if len(sys.argv) > 1:
        target = date.fromisoformat(sys.argv[1])
    else:
        target = date.today() - timedelta(days=1)

    log.info("Computing daily sensor report for %s", target)

    import backend.models  # noqa: resolve all relationships
    import backend.documents.models  # noqa: Document model for relationships
    from backend.services.sensor_daily_report_service import compute_daily_reports
    results = compute_daily_reports(target)

    log.info("Done: %d sensor reports saved", len(results))

    # Print summary
    for r in results:
        grade = r.get("quality_grade", "?")
        well = r.get("well_name", "?")
        role = r.get("sensor_role", "?")
        uptime = r.get("uptime_pct", 0)
        flags = r.get("quality_flags", "")
        log.info(
            "  %s | %s %-5s | uptime=%.1f%% grade=%s %s",
            target, well, role, uptime, grade, flags or "",
        )


if __name__ == "__main__":
    main()
