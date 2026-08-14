"""
Excel parser for GA4 event tracking reports.

Supports two sheet layouts:

Format A — Two-section (LP + Project Page):
  Row 2: Merged section titles — cols B:D = "LP name", cols F:H = "Project Page name"
  Row 3: Headers — "Click ID" in col B and col F
  Row 4: Totals row
  Row 5+: Data rows
  Column mapping: LP → B,C,D (idx 1,2,3); Project → F,G,H (idx 5,6,7)

Format B — Single-section LP-only (new GA4 export style):
  Optional comment rows at the top starting with "#"
  Header row: "Click ID" in col A (idx 0)
  Next row: Totals (click_id blank, grand total in col D)
  Following rows: Data — A=click_id, B=event_count, C=total_users
"""

import re
import pandas as pd

# GA4 standard events to filter out (not page elements)
GA4_STANDARD_EVENTS = {
    'page_view', 'session_start', 'first_visit', 'user_engagement',
    'scroll', 'click', 'file_download', 'form_submit', 'video_start',
    'video_complete', 'video_progress', 'view_search_results',
    'Total', 'total', 'Click ID', 'click_id',
}


def _is_ga4_event(value: str) -> bool:
    if not value:
        return True
    v = str(value).strip().lower()
    if v in {e.lower() for e in GA4_STANDARD_EVENTS}:
        return True
    # scroll depth events like "scroll_25", "scroll_40", etc.
    if re.match(r'^scroll', v):
        return True
    return False


def _clean_click_id(value) -> str | None:
    if value is None or str(value).strip() == '':
        return None
    s = str(value).strip()
    if s.lower() in {'total', 'click id', 'event name', 'nan', '(not set)'}:
        return None
    return s


def _to_int(value) -> int:
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _find_header_row(df) -> tuple[int | None, int | None]:
    """Return (row_idx, col_idx) of the first cell containing 'Click ID'."""
    for i in range(min(20, len(df))):
        for j in range(min(6, df.shape[1])):
            val = df.iloc[i, j]
            if val is not None and str(val).strip().lower() == 'click id':
                return i, j
    return None, None


def parse_excel(file_or_path) -> dict:
    """
    Parse the Excel file and return structured data per project.

    Returns:
        {
            "TVS Altura": {
                "lp_title": "TVS Emerald Altura LP",
                "project_title": "TVS Emerald Altura Project Page",
                "lp": [{"click_id": "location-map", "clicks": 2100, "users": 417}, ...],
                "project": [{"click_id": "project-content", "clicks": 825, "users": 250}, ...]
            },
            ...
        }
    """
    xl = pd.ExcelFile(file_or_path)
    result = {}

    for sheet_name in xl.sheet_names:
        if sheet_name.strip().lower() in {'index', 'summary', 'cover', 'readme'}:
            continue

        df = xl.parse(sheet_name, header=None)

        if df.empty or len(df) < 2:
            continue

        header_row_idx, click_id_col = _find_header_row(df)
        if header_row_idx is None:
            continue

        if click_id_col == 0:
            # Format B: single LP-only section, cols A=0, B=1, C=2
            # Row immediately after header is the grand total row
            totals_row = df.iloc[header_row_idx + 1]
            lp_total_clicks = _to_int(totals_row.iloc[1] if df.shape[1] > 1 else 0)
            lp_total_users  = _to_int(totals_row.iloc[2] if df.shape[1] > 2 else 0)

            lp_items = []
            for _, row in df.iloc[header_row_idx + 1:].iterrows():
                cid = _clean_click_id(row.iloc[0] if df.shape[1] > 0 else None)
                if cid and not _is_ga4_event(cid):
                    lp_items.append({
                        'click_id': cid,
                        'clicks': _to_int(row.iloc[1] if df.shape[1] > 1 else 0),
                        'users': _to_int(row.iloc[2] if df.shape[1] > 2 else 0),
                    })

            lp_items.sort(key=lambda x: x['clicks'], reverse=True)

            if lp_items:
                result[sheet_name] = {
                    'lp_title': f'{sheet_name} LP',
                    'project_title': '',
                    'lp': lp_items,
                    'project': [],
                    'lp_total_clicks': lp_total_clicks,
                    'lp_total_users':  lp_total_users,
                }

        elif click_id_col == 1:
            # Format A: two-section, LP in cols 1,2,3 and Project in cols 5,6,7
            lp_title = ''
            project_title = ''
            if header_row_idx > 0:
                title_row = df.iloc[header_row_idx - 1]
                lp_title = str(title_row.iloc[1]).strip() if df.shape[1] > 1 else ''
                project_title = str(title_row.iloc[5]).strip() if df.shape[1] > 5 else ''
            if lp_title in {'nan', 'None', ''}:
                lp_title = f'{sheet_name} LP'
            if project_title in {'nan', 'None', ''}:
                project_title = f'{sheet_name} Project Page'

            # Grand totals are in the row immediately after the header
            totals_row = df.iloc[header_row_idx + 1]
            lp_total_clicks      = _to_int(totals_row.iloc[2] if df.shape[1] > 2 else 0)
            lp_total_users       = _to_int(totals_row.iloc[3] if df.shape[1] > 3 else 0)
            project_total_clicks = _to_int(totals_row.iloc[6] if df.shape[1] > 6 else 0)
            project_total_users  = _to_int(totals_row.iloc[7] if df.shape[1] > 7 else 0)

            lp_items = []
            project_items = []

            # Skip header row + totals row → data starts at header_row_idx + 2
            for _, row in df.iloc[header_row_idx + 2:].iterrows():
                lp_id = _clean_click_id(row.iloc[1] if df.shape[1] > 1 else None)
                if lp_id and not _is_ga4_event(lp_id):
                    lp_items.append({
                        'click_id': lp_id,
                        'clicks': _to_int(row.iloc[2] if df.shape[1] > 2 else 0),
                        'users': _to_int(row.iloc[3] if df.shape[1] > 3 else 0),
                    })

                proj_id = _clean_click_id(row.iloc[5] if df.shape[1] > 5 else None)
                if proj_id and not _is_ga4_event(proj_id):
                    project_items.append({
                        'click_id': proj_id,
                        'clicks': _to_int(row.iloc[6] if df.shape[1] > 6 else 0),
                        'users': _to_int(row.iloc[7] if df.shape[1] > 7 else 0),
                    })

            lp_items.sort(key=lambda x: x['clicks'], reverse=True)
            project_items.sort(key=lambda x: x['clicks'], reverse=True)

            if lp_items or project_items:
                result[sheet_name] = {
                    'lp_title': lp_title,
                    'project_title': project_title,
                    'lp': lp_items,
                    'project': project_items,
                    'lp_total_clicks':      lp_total_clicks,
                    'lp_total_users':       lp_total_users,
                    'project_total_clicks': project_total_clicks,
                    'project_total_users':  project_total_users,
                }

    return result


def get_sheet_names(file_or_path) -> list:
    """Return list of non-index sheet names from the Excel file."""
    xl = pd.ExcelFile(file_or_path)
    skip = {'index', 'summary', 'cover', 'readme'}
    return [s for s in xl.sheet_names if s.strip().lower() not in skip]
