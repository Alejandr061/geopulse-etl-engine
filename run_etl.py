"""Lanza el pipeline GeoPulse desde la terminal y resume la corrida."""

from __future__ import annotations

import sys

from src.config import setup_logging
from src.etl.extract import ExtractError
from src.etl.pipeline import PipelineReport, run_pipeline_sync


def main() -> int:
    setup_logging()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        report = run_pipeline_sync()
    except ExtractError as exc:
        print()
        print("GeoPulse ETL no pudo completar la extracción.")
        print(str(exc))
        return 1
    except Exception as exc:  # noqa: BLE001 - la CLI debe informar cualquier fallo de corrida
        print()
        print(f"GeoPulse ETL falló: {exc.__class__.__name__}: {exc}")
        return 1

    _print_report(report)
    return 0


def _print_report(report: PipelineReport) -> None:
    width = 72
    print()
    print("=" * width)
    print("  GeoPulse ETL  ·  Motor agroclimático")
    print("=" * width)
    print(f"  Fuente             {report.source}")
    print(f"  Zonas              {report.zones}")
    print(f"  Observaciones      {report.rows_received} recibidas · {report.rows_received - report.rows_discarded} válidas")
    print(f"  Descartadas        {report.rows_discarded}")
    print(f"  Celdas imputadas   {report.rows_imputed}")
    print(f"  Insertadas         {report.rows_inserted}")
    print(f"  Actualizadas       {report.rows_updated}")
    print(f"  Anomalías          {report.anomalies}")
    print(f"  Duración           {report.elapsed_ms:.2f} ms")
    print("-" * width)
    if not report.zones_latest:
        print("  Sin lecturas actuales.")
    else:
        print(f"  {'Zona':<24} {'Riesgo':<12} {'HSI':>6} {'VPD':>7} {'Temp':>7}")
        for zone in report.zones_latest:
            print(
                f"  {zone.name:<24} {zone.risk_level:<12} "
                f"{zone.hydric_stress_index:6.1f} {zone.vpd_kpa:6.2f} {zone.temp_c:6.1f}°C"
            )
    print("=" * width)
    print()


if __name__ == "__main__":
    raise SystemExit(main())
