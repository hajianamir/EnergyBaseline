"""Phase 5 Gate G5 1,000,000-row end-to-end performance & memory benchmark.

Benchmarks:
1. Streaming PyArrow Parquet generation & row-group inspection on 1,000,000 rows (20 x 50,000 row groups).
2. Lazy ``QAbstractTableModel`` + ``fetchMore`` + ``QSortFilterProxyModel`` paging across 1,000,000 rows
   with bounded LRU page cache (peak resident rows <= 4,096; zero full-frame materialization).
3. LTTB (1,000,000 -> 2,000 points) and M4 (1,000,000 -> <= 3,200 points) downsampling throughput & envelope bounds.
4. Streaming 1,000,000-row sufficient-statistics OLS / degree-day baseline recovery with planted-effect verification.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import sys
import tempfile
import time
import tracemalloc
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from PySide6.QtCore import QModelIndex, Qt  # noqa: E402

from energybaseline.gui.table_model import (  # noqa: E402
    PagedSortFilterProxyModel,
    PagedTableModel,
    ParquetPagedSource,
)
from energybaseline.mining.downsample import lttb_downsample, m4_downsample  # noqa: E402

PLANTED_BASE_LOAD = 120.0
PLANTED_HDD_SLOPE = 4.25
PLANTED_CDD_SLOPE = 6.10
PLANTED_OCC_SLOPE = 28.0
HEAT_BASE_C = 15.0
COOL_BASE_C = 19.0


def _write_1m_parquet(
    parquet_path: Path, *, total_rows: int = 1_000_000, batch_rows: int = 50_000, seed: int = 2026
) -> float:
    """Stream 1,000,000 synthetic meter rows into row-grouped Parquet without holding all rows in RAM."""
    t0 = time.perf_counter()
    rng = np.random.default_rng(seed)
    schema = pa.schema(
        [
            ("step_id", pa.int64()),
            ("temp_c", pa.float64()),
            ("occupancy", pa.float64()),
            ("energy_kwh", pa.float64()),
        ]
    )
    with pq.ParquetWriter(parquet_path, schema, compression="snappy") as writer:
        for start in range(0, total_rows, batch_rows):
            n_b = min(batch_rows, total_rows - start)
            steps = np.arange(start, start + n_b, dtype=np.int64)
            # Diurnal + seasonal outdoor temperature trajectory
            phase_day = 2.0 * np.pi * (steps.astype(float) % 96.0) / 96.0
            phase_yr = 2.0 * np.pi * steps.astype(float) / float(total_rows)
            temp_c = 16.5 - 11.0 * np.cos(phase_yr) + 4.5 * np.sin(phase_day) + rng.normal(0.0, 0.8, size=n_b)
            occ = np.where(((steps % 96) >= 28) & ((steps % 96) < 76), 1.0, 0.15) + rng.uniform(-0.03, 0.03, size=n_b)
            hdd = np.maximum(0.0, HEAT_BASE_C - temp_c)
            cdd = np.maximum(0.0, temp_c - COOL_BASE_C)
            energy = (
                PLANTED_BASE_LOAD
                + PLANTED_HDD_SLOPE * hdd
                + PLANTED_CDD_SLOPE * cdd
                + PLANTED_OCC_SLOPE * occ
                + rng.normal(0.0, 0.9, size=n_b)
            )
            batch = pa.RecordBatch.from_arrays(
                [pa.array(steps), pa.array(temp_c), pa.array(occ), pa.array(energy)],
                schema=schema,
            )
            writer.write_batch(batch, row_group_size=batch_rows)
    return (time.perf_counter() - t0) * 1000.0


def run_benchmark_1m(*, total_rows: int = 1_000_000, page_size: int = 512, max_cached_pages: int = 8) -> dict[str, Any]:
    """Execute the full 1,000,000-row Phase 5 benchmark and return structured E2 telemetry."""
    tracemalloc.start()
    wall_start = time.perf_counter()

    with tempfile.TemporaryDirectory(prefix="eb_bench_1m_") as tmp_dir:
        pq_path = Path(tmp_dir) / "meter_1m.parquet"
        write_ms = _write_1m_parquet(pq_path, total_rows=total_rows, batch_rows=50_000)
        file_mb = pq_path.stat().st_size / (1024.0 * 1024.0)

        # 1. Lazy QAbstractTableModel + fetchMore + QSortFilterProxyModel over 1,000,000 rows
        t_tbl0 = time.perf_counter()
        source = ParquetPagedSource(pq_path, page_size=page_size, max_cached_pages=max_cached_pages)
        model = PagedTableModel(decimals=4)
        proxy = PagedSortFilterProxyModel()
        proxy.setSourceModel(model)
        model.set_source(source, prefetch_first_page=True)

        assert model.total_source_rows == total_rows
        assert model.full_frame_materialized is False
        assert model.rowCount() == page_size

        # Page through 20 additional chunks via proxy.fetchMore() and read cells across pages
        sampled_cells: list[str] = []
        for _ in range(20):
            assert proxy.canFetchMore(QModelIndex()) is True
            proxy.fetchMore(QModelIndex())
            last_row = proxy.rowCount() - 1
            sampled_cells.append(str(proxy.data(proxy.index(last_row, 3), Qt.ItemDataRole.DisplayRole)))

        max_allowed_resident = max_cached_pages * page_size
        assert model.peak_resident_rows <= max_allowed_resident, (
            f"LRU page bound exceeded: {model.peak_resident_rows} > {max_allowed_resident}"
        )
        assert model.fetched_row_count == 21 * page_size
        table_paging_ms = (time.perf_counter() - t_tbl0) * 1000.0

        # 2. Streaming sufficient-statistics OLS baseline recovery across all 1,000,000 rows (row-group by row-group)
        t_ols0 = time.perf_counter()
        pf = pq.ParquetFile(pq_path)
        xtx = np.zeros((4, 4), dtype=float)
        xty = np.zeros(4, dtype=float)
        y_Preview_chunks: list[np.ndarray] = []
        n_seen = 0

        for rg in range(pf.metadata.num_row_groups):
            rg_tbl = pf.read_row_group(rg, columns=["temp_c", "occupancy", "energy_kwh"])
            tc = rg_tbl.column("temp_c").to_numpy(zero_copy_only=False)
            oc = rg_tbl.column("occupancy").to_numpy(zero_copy_only=False)
            yv = rg_tbl.column("energy_kwh").to_numpy(zero_copy_only=False)
            y_Preview_chunks.append(yv)
            D_rg = np.column_stack(
                [
                    np.ones(tc.size, dtype=float),
                    np.maximum(0.0, HEAT_BASE_C - tc),
                    np.maximum(0.0, tc - COOL_BASE_C),
                    oc,
                ]
            )
            xtx += D_rg.T @ D_rg
            xty += D_rg.T @ yv
            n_seen += int(tc.size)

        assert n_seen == total_rows
        beta_hat = np.linalg.solve(xtx, xty)
        streaming_ols_ms = (time.perf_counter() - t_ols0) * 1000.0

        # Verify planted-effect recovery on 1M rows
        assert abs(float(beta_hat[0]) - PLANTED_BASE_LOAD) < 0.15
        assert abs(float(beta_hat[1]) - PLANTED_HDD_SLOPE) < 0.05
        assert abs(float(beta_hat[2]) - PLANTED_CDD_SLOPE) < 0.05
        assert abs(float(beta_hat[3]) - PLANTED_OCC_SLOPE) < 0.15

        # 3. 1,000,000-row LTTB and M4 chart downsampling
        y_1m = np.concatenate(y_Preview_chunks)
        del y_Preview_chunks
        x_1m = np.arange(total_rows, dtype=float)
        lttb_res = lttb_downsample(x_1m, y_1m, threshold=2000)
        m4_res = m4_downsample(x_1m, y_1m, pixel_width=800)
        assert lttb_res.n_output == 2000
        assert m4_res.n_output <= 3200
        assert m4_res.envelope_min_error == 0.0
        assert m4_res.envelope_max_error == 0.0

    current_bytes, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    total_wall_s = time.perf_counter() - wall_start

    return {
        "benchmark": "energybaseline.phase5.bench_1m",
        "downsampling": {
            "lttb_2000": lttb_res.to_dict(),
            "m4_800px": m4_res.to_dict(),
        },
        "environment": {
            "note": "CI / sandbox non-normative per DECISIONS.md D2; normative hardware is Win11 23H2 8-core 16GB NVMe.",
            "platform": platform.platform(),
            "python": sys.version.split()[0],
        },
        "memory": {
            "current_traced_mib": round(current_bytes / (1024.0 * 1024.0), 3),
            "peak_traced_mib": round(peak_bytes / (1024.0 * 1024.0), 3),
        },
        "ok": True,
        "parquet": {
            "file_size_mib": round(file_mb, 3),
            "row_groups": 20,
            "total_rows": total_rows,
            "write_elapsed_ms": round(write_ms, 2),
        },
        "streaming_ols_1m": {
            "elapsed_ms": round(streaming_ols_ms, 2),
            "planted": {
                "base_load": PLANTED_BASE_LOAD,
                "cdd_slope": PLANTED_CDD_SLOPE,
                "hdd_slope": PLANTED_HDD_SLOPE,
                "occ_slope": PLANTED_OCC_SLOPE,
            },
            "recovered": {
                "base_load": round(float(beta_hat[0]), 5),
                "cdd_slope": round(float(beta_hat[2]), 5),
                "hdd_slope": round(float(beta_hat[1]), 5),
                "occ_slope": round(float(beta_hat[3]), 5),
            },
        },
        "table_paging": {
            "elapsed_ms": round(table_paging_ms, 2),
            "fetch_more_calls": model.fetch_more_calls,
            "fetched_rows": model.fetched_row_count,
            "full_frame_materialized": model.full_frame_materialized,
            "max_cached_pages": max_cached_pages,
            "page_size": page_size,
            "pages_read_from_disk": source.pages_fetched,
            "peak_resident_rows": model.peak_resident_rows,
            "sample_display_values": sampled_cells[:3],
            "total_source_rows": model.total_source_rows,
        },
        "total_wall_seconds": round(total_wall_s, 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run 1,000,000-row Phase 5 Gate G5 performance benchmark.")
    ap.add_argument("--rows", type=int, default=1_000_000, help="Total synthetic rows (default: 1,000,000).")
    ap.add_argument("--out", default=None, help="Optional JSON output file path.")
    args = ap.parse_args(argv)

    payload = run_benchmark_1m(total_rows=int(args.rows))
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
