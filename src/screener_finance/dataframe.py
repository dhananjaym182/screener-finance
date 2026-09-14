"""Dict -> pandas DataFrame helpers."""
from __future__ import annotations

from typing import Any

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover
    raise ImportError("pandas is required: pip install pandas") from exc


def table_to_df(section: dict) -> pd.DataFrame:
    """{"headers": [...], "rows": [{"label", "values": [...]}]} -> DataFrame.

    Index = row labels, columns = periods. Values numeric where possible.
    """
    headers = section.get("headers") or []
    rows = section.get("rows") or []
    if not headers or not rows:
        return pd.DataFrame()
    data = {}
    for row in rows:
        label = row.get("label") or ""
        values = row.get("values") or []
        data[label] = values[:len(headers)]
    df = pd.DataFrame.from_dict(data, orient="index", columns=headers)
    return df


def dict_to_series(d: dict[str, Any], name: str) -> pd.Series:
    return pd.Series(d, name=name)
