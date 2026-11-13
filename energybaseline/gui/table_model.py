"""Large-data Qt table architecture: QAbstractTableModel + lazy fetchMore paging + QSortFilterProxyModel (Phase 5)."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

SORT_VALUE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


class LazyPageSource:
    """Abstract base for paged tabular data sources that never materialize full multi-million-row frames."""

    total_rows: int
    columns: tuple[str, ...]
    page_size: int
    max_cached_pages: int
    full_frame_materialized: bool

    def get_cell(self, row: int, col: int) -> Any:
        raise NotImplementedError

    @property
    def resident_row_count(self) -> int:
        raise NotImplementedError

    @property
    def peak_resident_rows(self) -> int:
        raise NotImplementedError

    @property
    def pages_fetched(self) -> int:
        raise NotImplementedError


class DataFramePagedSource(LazyPageSource):
    """Zero-truncation paged view over an existing pandas DataFrame (no 3,000-row ceiling)."""

    def __init__(self, df: pd.DataFrame | None, *, page_size: int = 512) -> None:
        self._df = df if df is not None else pd.DataFrame()
        self.total_rows = int(len(self._df))
        self.columns = tuple(str(c) for c in self._df.columns)
        self.page_size = max(16, int(page_size))
        self.max_cached_pages = 0
        self.full_frame_materialized = True
        self._col_arrays = [self._df.iloc[:, j].to_numpy() for j in range(len(self.columns))]

    def get_cell(self, row: int, col: int) -> Any:
        return self._col_arrays[col][row]

    @property
    def resident_row_count(self) -> int:
        return self.total_rows

    @property
    def peak_resident_rows(self) -> int:
        return self.total_rows

    @property
    def pages_fetched(self) -> int:
        return 0


class CallableChunkSource(LazyPageSource):
    """Bounded-LRU lazy chunked data source proving 1M+ row fetchMore without full-frame materialization."""

    def __init__(
        self,
        *,
        total_rows: int,
        columns: Sequence[str],
        chunk_loader: Callable[[int, int], pd.DataFrame | dict[str, np.ndarray]],
        page_size: int = 512,
        max_cached_pages: int = 8,
    ) -> None:
        self.total_rows = max(0, int(total_rows))
        self.columns = tuple(str(c) for c in columns)
        self.page_size = max(16, int(page_size))
        self.max_cached_pages = max(2, int(max_cached_pages))
        self.full_frame_materialized = False
        self._loader = chunk_loader
        self._cache: OrderedDict[int, tuple[np.ndarray, ...]] = OrderedDict()
        self._pages_fetched = 0
        self._peak_resident = 0

    def _load_page(self, page_id: int) -> tuple[np.ndarray, ...]:
        if page_id in self._cache:
            self._cache.move_to_end(page_id)
            return self._cache[page_id]
        start = page_id * self.page_size
        stop = min(self.total_rows, start + self.page_size)
        raw = self._loader(start, stop)
        if isinstance(raw, pd.DataFrame):
            cols_tup = tuple(raw[c].to_numpy() for c in self.columns)
        else:
            cols_tup = tuple(np.asarray(raw[c]) for c in self.columns)
        self._pages_fetched += 1
        self._cache[page_id] = cols_tup
        while len(self._cache) > self.max_cached_pages:
            self._cache.popitem(last=False)
        cur_res = sum(int(len(v[0])) if v else 0 for v in self._cache.values())
        if cur_res > self._peak_resident:
            self._peak_resident = cur_res
        return cols_tup

    def get_cell(self, row: int, col: int) -> Any:
        page_id = row // self.page_size
        offset = row - page_id * self.page_size
        page = self._load_page(page_id)
        return page[col][offset]

    @property
    def resident_row_count(self) -> int:
        return sum(int(len(v[0])) if v else 0 for v in self._cache.values())

    @property
    def peak_resident_rows(self) -> int:
        return self._peak_resident

    @property
    def pages_fetched(self) -> int:
        return self._pages_fetched


class ParquetPagedSource(CallableChunkSource):
    """Row-group-sliced PyArrow Parquet source that pages 1M+ rows from disk with O(page_size) RAM."""

    def __init__(
        self,
        parquet_path: str | Path,
        *,
        page_size: int = 512,
        max_cached_pages: int = 8,
    ) -> None:
        self.parquet_path = Path(parquet_path)
        with pq.ParquetFile(self.parquet_path) as pf:
            total = int(pf.metadata.num_rows)
            cols = [str(name) for name in pf.schema_arrow.names if not str(name).startswith("__index_level_")]
            bounds: list[tuple[int, int, int]] = []
            cursor = 0
            for rg in range(pf.metadata.num_row_groups):
                n_rg = int(pf.metadata.row_group(rg).num_rows)
                bounds.append((rg, cursor, cursor + n_rg))
                cursor += n_rg
        self._rg_bounds = bounds
        last_rg: dict[str, Any] = {"idx": -1, "tbl": None}

        def _get_rg_table(rg_idx: int) -> Any:
            if last_rg["idx"] == rg_idx and last_rg["tbl"] is not None:
                return last_rg["tbl"]
            with pq.ParquetFile(self.parquet_path) as pf_rg:
                tbl_rg = pf_rg.read_row_group(rg_idx, columns=cols)
            last_rg["idx"] = rg_idx
            last_rg["tbl"] = tbl_rg
            return tbl_rg

        def _read_slice(start: int, stop: int) -> dict[str, np.ndarray]:
            if stop <= start:
                return {c: np.empty(0) for c in cols}
            chunks: dict[str, list[np.ndarray]] = {c: [] for c in cols}
            for rg_idx, rg_start, rg_stop in self._rg_bounds:
                if rg_stop <= start:
                    continue
                if rg_start >= stop:
                    break
                tbl = _get_rg_table(rg_idx)
                loc_s = max(0, start - rg_start)
                loc_e = min(rg_stop - rg_start, stop - rg_start)
                sliced = tbl.slice(loc_s, loc_e - loc_s)
                for c in cols:
                    chunks[c].append(sliced.column(c).to_numpy(zero_copy_only=False))
            return {c: (np.concatenate(chunks[c]) if chunks[c] else np.empty(0)) for c in cols}

        self._clear_rg_cache = lambda: last_rg.update({"idx": -1, "tbl": None})
        super().__init__(
            total_rows=total,
            columns=cols,
            chunk_loader=_read_slice,
            page_size=page_size,
            max_cached_pages=max_cached_pages,
        )

    def close(self) -> None:
        """Release any cached Arrow buffers (file handles are already closed after each row-group read)."""
        self._clear_rg_cache()
        self._cache.clear()


def _format_cell_display(val: Any, decimals: int) -> tuple[str, Any]:
    if val is None:
        return "", ""
    if isinstance(val, (bool, np.bool_)):
        b = bool(val)
        return str(b), int(b)
    if isinstance(val, (int, np.integer)):
        iv = int(val)
        return str(iv), iv
    if isinstance(val, (float, np.floating)):
        fv = float(val)
        if np.isnan(fv):
            return "", float("-inf")
        if np.isinf(fv):
            return ("inf" if fv > 0 else "-inf"), fv
        return f"{round(fv, decimals):g}" if abs(fv) >= 1e6 else f"{fv:.{decimals}f}".rstrip("0").rstrip("."), fv
    if pd.isna(val):
        return "", ""
    s = str(val)
    return s, s.lower()


class PagedTableModel(QAbstractTableModel):
    """Qt table model supporting incremental ``fetchMore`` over 1M+ rows without full-frame materialization."""

    def __init__(
        self,
        source: LazyPageSource | None = None,
        *,
        decimals: int = 4,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._decimals = int(decimals)
        self._source: LazyPageSource = source if source is not None else DataFramePagedSource(None)
        self._fetched_rows: int = 0
        self._fetch_more_calls: int = 0

    @property
    def source(self) -> LazyPageSource:
        return self._source

    @property
    def total_source_rows(self) -> int:
        return self._source.total_rows

    @property
    def fetched_row_count(self) -> int:
        return self._fetched_rows

    @property
    def fetch_more_calls(self) -> int:
        return self._fetch_more_calls

    @property
    def peak_resident_rows(self) -> int:
        return self._source.peak_resident_rows

    @property
    def full_frame_materialized(self) -> bool:
        return self._source.full_frame_materialized

    def set_source(self, source: LazyPageSource, *, decimals: int | None = None, prefetch_first_page: bool = True) -> None:
        self.beginResetModel()
        if decimals is not None:
            self._decimals = int(decimals)
        self._source = source
        self._fetched_rows = 0
        self._fetch_more_calls = 0
        self.endResetModel()
        if prefetch_first_page and self.canFetchMore(QModelIndex()):
            self.fetchMore(QModelIndex())

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return self._fetched_rows

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._source.columns)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        if parent.isValid():
            return False
        return self._fetched_rows < self._source.total_rows

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        if parent.isValid():
            return
        remaining = self._source.total_rows - self._fetched_rows
        if remaining <= 0:
            return
        items_to_fetch = min(self._source.page_size, remaining)
        first = self._fetched_rows
        last = first + items_to_fetch - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._fetched_rows += items_to_fetch
        self._fetch_more_calls += 1
        # Touch first row of newly exposed page on lazy sources so resident LRU metrics reflect fetched page immediately
        if not self._source.full_frame_materialized and len(self._source.columns) > 0:
            _ = self._source.get_cell(first, 0)
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> Any:
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        if r < 0 or r >= self._fetched_rows or c < 0 or c >= len(self._source.columns):
            return None
        if role in (int(Qt.ItemDataRole.DisplayRole), SORT_VALUE_ROLE, int(Qt.ItemDataRole.ToolTipRole)):
            raw = self._source.get_cell(r, c)
            disp, sort_val = _format_cell_display(raw, self._decimals)
            if role == SORT_VALUE_ROLE:
                return sort_val
            return disp
        if role == int(Qt.ItemDataRole.TextAlignmentRole):
            raw = self._source.get_cell(r, c)
            if isinstance(raw, (int, float, np.integer, np.floating)) and not isinstance(raw, (bool, np.bool_)):
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if role != int(Qt.ItemDataRole.DisplayRole):
            return None
        if orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._source.columns):
                return self._source.columns[section]
            return None
        return str(section + 1)


class PagedSortFilterProxyModel(QSortFilterProxyModel):
    """Case-insensitive multi-column filter and type-aware sort proxy over ``PagedTableModel``."""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setSortRole(SORT_VALUE_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterKeyColumn(-1)

    def canFetchMore(self, parent: QModelIndex = QModelIndex()) -> bool:
        src = self.sourceModel()
        if src is not None and not parent.isValid():
            return bool(src.canFetchMore(QModelIndex()))
        return super().canFetchMore(parent)

    def fetchMore(self, parent: QModelIndex = QModelIndex()) -> None:
        src = self.sourceModel()
        if src is not None and not parent.isValid():
            src.fetchMore(QModelIndex())
            return
        super().fetchMore(parent)

