"""
Report generator — v2.

Builds the two-level tabbed HTML report from:
  - project_data  (parse_excel output — v2 structure)
  - screenshots   (capture_all_pages output — v2 structure)
  - links         (list of {project_name, url, page_type, ...})

Key concepts from REPORT_FORMAT.md:
  - Depth-adjusted click rate = element.users / bucket_users(element.depth)
  - bucket_users: linear interpolation between scroll curve points
  - Verdicts per-page median of reach and adjusted rate
  - Degraded mode when no scroll data
"""

import re
import os
import statistics
from datetime import datetime
from jinja2 import Environment, FileSystemLoader

from src.classifier import classify, classify_items

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')

MAX_CARDS = 15  # max CTAs shown per page


# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def clean_label(raw: str) -> str:
    """Make raw click IDs more readable: location-map → Location Map"""
    s = re.sub(r'[-_]+', ' ', raw)
    return s.title()


# ── Attention model ────────────────────────────────────────────────────────────

def bucket_users(depth: float | None, scroll: dict) -> int | None:
    """
    Return the estimated number of users who scrolled at least `depth` far.

    scroll = {'page_view_users': int, 'buckets': [(depth_float, users_int), ...]}

    Returns None when depth is None (fixed/sticky → use page_view_users directly).
    Returns page_view_users for depth=None (persistent elements).

    Linear interpolation between bracketing buckets per §5.3.
    """
    if scroll is None:
        return None

    pv = scroll['page_view_users']
    buckets = scroll['buckets']  # sorted asc by depth

    if depth is None:
        # Persistent (fixed/sticky) — visible to all page-view users
        return pv

    if not buckets:
        return pv

    first_depth, first_users = buckets[0]
    last_depth, last_users = buckets[-1]

    if depth <= first_depth:
        # Interpolate between page_view_users and first bucket
        if first_depth == 0:
            return first_users
        t = depth / first_depth
        return int(pv + t * (first_users - pv))

    if depth >= last_depth:
        return last_users

    # Find bracketing buckets
    for i in range(len(buckets) - 1):
        d0, u0 = buckets[i]
        d1, u1 = buckets[i + 1]
        if d0 <= depth <= d1:
            if d1 == d0:
                return u0
            t = (depth - d0) / (d1 - d0)
            return int(u0 + t * (u1 - u0))

    return last_users


def adjusted_rate(element_users: int, bu: int | None) -> float | None:
    """
    depth_adjusted_rate = element.users / bucket_users(element.depth)

    Returns None when bucket_users < 30 (suppress noisy percentages).
    """
    if bu is None or bu < 30 or element_users == 0:
        return None
    return element_users / bu


def _cliff_info(scroll: dict) -> dict:
    """Return cliff_depth, cliff_loss from a scroll dict."""
    if not scroll or not scroll['buckets']:
        return {'cliff_depth': None, 'cliff_loss': None}

    pv = scroll['page_view_users']
    if pv == 0:
        return {'cliff_depth': None, 'cliff_loss': None}

    buckets = scroll['buckets']
    prev_users = pv
    max_drop = 0
    cliff_depth = None

    for depth, users in buckets:
        drop = prev_users - users
        if drop > max_drop:
            max_drop = drop
            cliff_depth = depth
        prev_users = users

    return {
        'cliff_depth': cliff_depth,
        'cliff_loss': max_drop / pv if pv else None,
    }


def _deep_retention(scroll: dict) -> float | None:
    """reach(100%) / reach(40%) — requires both buckets to be present."""
    if not scroll or not scroll['buckets']:
        return None
    pv = scroll['page_view_users']
    if pv == 0:
        return None

    depths = dict(scroll['buckets'])
    if 0.40 not in depths or 1.00 not in depths:
        return None
    r40 = depths[0.40] / pv
    r100 = depths[1.00] / pv
    if r40 == 0:
        return None
    return r100 / r40


def _reach_at(depth: float, scroll: dict) -> float | None:
    """reach at a specific depth (fraction 0–1)."""
    if not scroll:
        return None
    pv = scroll['page_view_users']
    if pv == 0:
        return None
    bu = bucket_users(depth, scroll)
    if bu is None:
        return None
    return bu / pv


def _build_attention_curve_data(scroll: dict) -> dict:
    """Build the data needed to draw the inline SVG attention curve."""
    if not scroll or not scroll['buckets']:
        return {}

    pv = scroll['page_view_users']
    if pv == 0:
        return {}

    # Anchor at 0% depth = 100% reach
    points = [(0.0, 1.0)]
    for depth, users in scroll['buckets']:
        points.append((depth, users / pv))

    cliff = _cliff_info(scroll)

    return {
        'points': points,
        'cliff_depth': cliff['cliff_depth'],
        'cliff_loss': cliff['cliff_loss'],
        'page_view_users': pv,
    }


# ── Verdicts ───────────────────────────────────────────────────────────────────

VERDICT_WORKS = 'Works'
VERDICT_SEEN_IGNORED = 'Seen and ignored'
VERDICT_BURIED = 'Buried'
VERDICT_DEAD = 'Dead'


def assign_verdicts(elements: list[dict]) -> list[dict]:
    """
    Compute per-page median reach and adjusted_rate, then assign verdicts.

    Each element dict must have: 'reach' (float|None), 'adj_rate' (float|None).
    Elements with None in either metric get verdict = None.
    """
    valid_reach = [e['reach'] for e in elements if e.get('reach') is not None]
    valid_rate = [e['adj_rate'] for e in elements if e.get('adj_rate') is not None]

    med_reach = statistics.median(valid_reach) if len(valid_reach) >= 2 else None
    med_rate = statistics.median(valid_rate) if len(valid_rate) >= 2 else None

    for e in elements:
        r = e.get('reach')
        ar = e.get('adj_rate')
        if r is None or ar is None or med_reach is None or med_rate is None:
            e['verdict'] = None
            continue
        high_reach = r >= med_reach
        high_rate = ar >= med_rate
        if high_reach and high_rate:
            e['verdict'] = VERDICT_WORKS
        elif high_reach and not high_rate:
            e['verdict'] = VERDICT_SEEN_IGNORED
        elif not high_reach and high_rate:
            e['verdict'] = VERDICT_BURIED
        else:
            e['verdict'] = VERDICT_DEAD

    return elements


# ── SVG curve builder ──────────────────────────────────────────────────────────

def _build_svg(curve_data: dict, width: int = 300, height: int = 120) -> str:
    """
    Build an inline SVG line chart of the scroll attention curve.

    X = scroll depth (0 → 1)
    Y = reach (1 at top = 100%, 0 at bottom = 0%)
    Cliff marked with an orange vertical line.
    """
    if not curve_data or not curve_data.get('points'):
        return ''

    points = curve_data['points']
    pad_l, pad_r, pad_t, pad_b = 36, 12, 10, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def px(depth, reach):
        x = pad_l + depth * plot_w
        y = pad_t + (1.0 - reach) * plot_h
        return x, y

    # Polyline points
    pts_str = ' '.join(f'{px(d, r)[0]:.1f},{px(d, r)[1]:.1f}' for d, r in points)

    # Fill polygon (curve + bottom)
    last_x, _ = px(points[-1][0], points[-1][1])
    first_x, _ = px(points[0][0], points[0][1])
    bottom_y = pad_t + plot_h
    fill_pts = pts_str + f' {last_x:.1f},{bottom_y:.1f} {first_x:.1f},{bottom_y:.1f}'

    # Cliff marker
    cliff_svg = ''
    cliff_depth = curve_data.get('cliff_depth')
    if cliff_depth is not None:
        cx = pad_l + cliff_depth * plot_w
        cliff_svg = (
            f'<line x1="{cx:.1f}" y1="{pad_t}" x2="{cx:.1f}" y2="{pad_t + plot_h}" '
            f'stroke="#F26522" stroke-width="1.5" stroke-dasharray="3,2"/>'
            f'<text x="{cx + 3:.1f}" y="{pad_t + 9}" font-size="8" fill="#F26522" '
            f'font-family="DM Sans,sans-serif">{int(cliff_depth * 100)}%</text>'
        )

    # Y-axis labels
    y_labels = ''
    for pct in (100, 50, 0):
        reach_val = pct / 100
        _, yy = px(0, reach_val)
        y_labels += (
            f'<text x="{pad_l - 4}" y="{yy + 3:.1f}" text-anchor="end" font-size="8" '
            f'fill="#888" font-family="DM Sans,sans-serif">{pct}%</text>'
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + plot_w}" y2="{yy:.1f}" '
            f'stroke="#E8E8E8" stroke-width="0.5"/>'
        )

    # X-axis labels
    x_labels = ''
    for d_pct in (0, 40, 80, 100):
        d = d_pct / 100
        xx = pad_l + d * plot_w
        x_labels += (
            f'<text x="{xx:.1f}" y="{pad_t + plot_h + 14}" text-anchor="middle" font-size="8" '
            f'fill="#888" font-family="DM Sans,sans-serif">{d_pct}%</text>'
        )

    svg = (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
        f'{y_labels}{x_labels}'
        f'<polygon points="{fill_pts}" fill="#F26522" fill-opacity="0.08"/>'
        f'<polyline points="{pts_str}" fill="none" stroke="#F26522" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{cliff_svg}'
        f'</svg>'
    )
    return svg


# ── Main generator ─────────────────────────────────────────────────────────────

def generate_report(
    client_name: str,
    project_data: dict,
    screenshots: dict,
    links: list[dict],
) -> str:
    """
    Build the self-contained HTML report.

    Signature unchanged from v1 so app.py needs no modification.
    """
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template('report_template.html')

    # URL lookup: {sheet_key_lower: {page_type: url}}
    url_lookup: dict[str, dict[str, str]] = {}
    for link in links:
        key = link['project_name'].strip().lower()
        if key not in url_lookup:
            url_lookup[key] = {}
        url_lookup[key][link['page_type']] = link['url']

    configured_sheets = set(url_lookup.keys())

    projects = []
    portfolio_flags: list[str] = []
    total_page_view_users = 0
    fully_analysed = 0
    degraded_count = 0

    for sheet_name, data in project_data.items():
        sheet_key = sheet_name.strip().lower()
        if sheet_key not in configured_sheets:
            # Still surface flags
            portfolio_flags.extend(data.get('flags', []))
            continue

        portfolio_flags.extend(data.get('flags', []))
        sheet_screenshots = screenshots.get(sheet_name, {})
        urls = url_lookup.get(sheet_key, {})

        page_panels = []
        proj_page_view_users = 0

        for page_type in ('lp', 'project'):
            if page_type not in urls:
                continue

            page_data = data.get('pages', {}).get(page_type)
            if page_data is None:
                continue

            items = page_data.get('items', [])
            scroll = page_data.get('scroll')
            mode = page_data.get('mode', 'clicks_only')
            page_sc = sheet_screenshots.get(page_type, {})
            depths = page_sc.get('depths', {})
            page_height = page_sc.get('page_height', 0)
            viewport_height = page_sc.get('viewport_height', 900)

            # Filter zero-click items for display, keep all for classification
            display_items = [i for i in items if i.get('clicks', 0) > 0][:MAX_CARDS]

            # Classify every item
            classify_items(display_items, sheet_name)

            # Build per-element enriched dicts
            elements = []
            for item in display_items:
                cid = item['click_id']
                depth_val = depths.get(cid)  # float | None (None = fixed/sticky or not found)
                bu = bucket_users(depth_val, scroll)
                ar = adjusted_rate(item['users'], bu)
                reach = (bu / scroll['page_view_users']) if (
                    bu is not None and scroll and scroll['page_view_users'] > 0
                ) else None

                elements.append({
                    'click_id': cid,
                    'display_name': clean_label(cid),
                    'category': item.get('category', 'unclassified'),
                    'clicks': item['clicks'],
                    'users': item['users'],
                    'depth': depth_val,
                    'bucket_users': bu,
                    'adj_rate': ar,
                    'reach': reach,
                    'verdict': None,
                    'crop': page_sc.get('element_crops', {}).get(cid, ''),
                    'found': cid in page_sc.get('depths', {}),
                })

            # Assign verdicts using per-page medians (classified CTAs only)
            classified = [e for e in elements if e['category'] in (
                'primary_cta', 'secondary_cta', 'unclassified'
            )]
            assign_verdicts(classified)
            # Copy verdicts back onto the full elements list
            verdict_map = {e['click_id']: e['verdict'] for e in classified}
            for e in elements:
                e['verdict'] = verdict_map.get(e['click_id'])

            # Sort by adjusted rate (desc), fall back to raw clicks
            def sort_key(e):
                ar = e['adj_rate']
                return (ar if ar is not None else -1, e['clicks'])

            sorted_elements = sorted(elements, key=sort_key, reverse=True)
            for rank, e in enumerate(sorted_elements, start=1):
                e['rank'] = rank

            # Attention curve
            curve_data = _build_attention_curve_data(scroll)
            svg = _build_svg(curve_data) if curve_data else ''
            cliff_info = _cliff_info(scroll) if scroll else {}

            # Top CTAs (C block): top 6 by adjusted rate, primary/secondary only
            top_ctas = [e for e in sorted_elements
                        if e['category'] in ('primary_cta', 'secondary_cta')][:6]
            if not top_ctas:
                top_ctas = sorted_elements[:6]

            # Problem CTAs (D block)
            problem_ctas = {
                VERDICT_SEEN_IGNORED: [],
                VERDICT_BURIED: [],
                VERDICT_DEAD: [],
            }
            for e in sorted_elements:
                if e['verdict'] in problem_ctas:
                    problem_ctas[e['verdict']].append(e)

            # Append blocks
            appendix_items = sorted_elements  # all, unfiltered

            # Page-level stats
            pv_users = scroll['page_view_users'] if scroll else 0
            proj_page_view_users += pv_users

            # Page-level cliff sentence
            cliff_sentence = ''
            if curve_data and cliff_info.get('cliff_depth') is not None:
                loss_pct = int((cliff_info['cliff_loss'] or 0) * 100)
                cliff_d_pct = int((cliff_info['cliff_depth'] or 0) * 100)
                cliff_sentence = (
                    f"{loss_pct}% of {pv_users:,} visitors left before "
                    f"{cliff_d_pct}% depth."
                )

            reach_40 = _reach_at(0.40, scroll) if scroll else None
            deep_ret = _deep_retention(scroll) if scroll else None

            top_cta_name = ''
            top_cta_adj = None
            if top_ctas:
                top_cta_name = top_ctas[0]['display_name']
                top_cta_adj = top_ctas[0]['adj_rate']

            if mode == 'full':
                fully_analysed += 1
            else:
                degraded_count += 1

            page_panels.append({
                'page_type': page_type,
                'title': page_data.get('title', f'{sheet_name} {page_type.upper()}'),
                'url': urls.get(page_type, ''),
                'mode': mode,
                'total_clicks': page_data.get('total_clicks', 0),
                'total_users': page_data.get('total_users', 0),
                'page_view_users': pv_users,
                'page_height': page_height,
                'viewport_height': viewport_height,
                'screenshot': page_sc.get('screenshot', ''),
                'found_count': page_sc.get('found_count', 0),
                # Attention
                'svg': svg,
                'cliff_sentence': cliff_sentence,
                'cliff_depth': cliff_info.get('cliff_depth'),
                'cliff_loss': cliff_info.get('cliff_loss'),
                'reach_40': reach_40,
                'deep_retention': deep_ret,
                # CTA blocks
                'elements': sorted_elements,
                'top_ctas': top_ctas,
                'problem_ctas': problem_ctas,
                'appendix_items': appendix_items,
                'top_cta_name': top_cta_name,
                'top_cta_adj': top_cta_adj,
                'has_enough_for_top': len(classified) >= 3,
                'has_enough_for_problems': len(classified) >= 8,
            })

        if page_panels:
            total_page_view_users += proj_page_view_users
            slug = slugify(sheet_name)
            projects.append({
                'name': sheet_name,
                'slug': slug,
                'page_panels': page_panels,
                'proj_page_view_users': proj_page_view_users,
                'single_page': len(page_panels) == 1,
            })

    # Sort projects by page_view_users desc (§8.1)
    projects.sort(key=lambda p: p['proj_page_view_users'], reverse=True)

    # Build the overview comparison table rows
    overview_rows = []
    for proj in projects:
        for pp in proj['page_panels']:
            overview_rows.append({
                'project': proj['name'],
                'slug': proj['slug'],
                'page_type': pp['page_type'],
                'page_view_users': pp['page_view_users'],
                'reach_40': pp.get('reach_40'),
                'cliff_loss': pp.get('cliff_loss'),
                'deep_retention': pp.get('deep_retention'),
                'top_cta': pp.get('top_cta_name', ''),
                'top_cta_adj': pp.get('top_cta_adj'),
                'mode': pp['mode'],
            })
    overview_rows.sort(key=lambda r: r['page_view_users'], reverse=True)

    now = datetime.now()
    report_name = f"{client_name} Event report {now.day} ({now.strftime('%B')}) {now.year}"

    return template.render(
        client_name=client_name,
        report_name=report_name,
        generated_at=now.isoformat(),
        projects=projects,
        overview_rows=overview_rows,
        total_page_view_users=total_page_view_users,
        fully_analysed=fully_analysed,
        degraded_count=degraded_count,
        portfolio_flags=[f for f in portfolio_flags if f],
        VERDICT_WORKS=VERDICT_WORKS,
        VERDICT_SEEN_IGNORED=VERDICT_SEEN_IGNORED,
        VERDICT_BURIED=VERDICT_BURIED,
        VERDICT_DEAD=VERDICT_DEAD,
    )
