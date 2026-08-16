"""
CTA classifier for SEO event tracker v2.

Normalises click IDs (lowercase, collapse -/_/spaces to _),
then assigns each to exactly one category using first-match logic.

Categories (in order):
  1. cross_project  — _view_property suffix, project name not in the id
  2. noise          — nav chrome, sliders, section anchors, chat widgets, …
  3. form_field     — form / banner inputs, bare name/email/phone
  4. nav_projects   — our_projects_*, home_about_us, other_projects*
  5. primary_cta    — enquire, book, whatsapp, brochure, …
  6. secondary_cta  — floor plan, gallery, 360, walkthrough, …
  7. unclassified   — everything else
"""

import re

# ── Noise patterns ────────────────────────────────────────────────────────────
# Items are plain strings (exact match after normalise) or compiled re patterns.

_NOISE_EXACT: frozenset[str] = frozenset({
    'header', 'hamburger', 'mobile_menu', 'ubermenu',
    'about', 'amenities', 'location', 'plans', 'highlights', 'gallery',
    'faq', 'map', 'customer_reviews', 'project_content', 'dslc_content',
    'chat_now_btn', 'load_more', 'agree', 'message', 'origin_input',
    'name', 'email', 'phone',  # bare field names (also caught by form_field below)
})

_NOISE_PREFIX: tuple[str, ...] = (
    'header_', 'hamburger_', 'mobile_menu_', 'ubermenu_', 'kenyt',
    'footer_widget', 'ui_id_',
)

_NOISE_SUFFIX: tuple[str, ...] = (
    '_error', '_menu', '_left_click', '_right_click',
)

_NOISE_CONTAINS: tuple[str, ...] = (
    'next', 'prev', 'fa_angle', 'unmute',
)

_NOISE_MENU_ITEM_RE = re.compile(r'^menu_item_\d+$')

# ── Primary CTA keywords ──────────────────────────────────────────────────────
_PRIMARY_KEYWORDS: tuple[str, ...] = (
    'enquire', 'enquiry', 'enqiure',      # include known typo
    'call_btn', 'call_me', 'callme',
    'request_detials', 'request_details',
    'send_details', 'send_detials',        # include known typo
    'schedule_visit', 'site_visit',
    'book', 'whatsapp', 'submit',
    'banner_form', 'sticky_form', 'brochure',
)

# ── Secondary CTA keywords ────────────────────────────────────────────────────
_SECONDARY_KEYWORDS: tuple[str, ...] = (
    'unit_plan', 'floor_plan', 'master_plan',
    'gallery_', '360', 'virtual', 'video',
    'view_property', 'location_map',
    '5gardens', 'walkthrough',
)


def normalise(click_id: str) -> str:
    """Lowercase and collapse -, _ and spaces to a single underscore."""
    s = click_id.lower()
    s = re.sub(r'[-_\s]+', '_', s)
    return s.strip('_')


def classify(click_id: str, project_name: str = '') -> str:
    """
    Classify a click ID into one of the seven categories.

    Parameters
    ----------
    click_id     : raw click ID string from the GA4 export
    project_name : display name of the current project (used for cross_project detection)

    Returns
    -------
    One of: 'cross_project', 'noise', 'form_field', 'nav_projects',
            'primary_cta', 'secondary_cta', 'unclassified'
    """
    norm = normalise(click_id)
    proj_norm = normalise(project_name) if project_name else ''

    # 1. cross_project — ends with _view_property and project name not in the id
    if norm.endswith('_view_property'):
        if not proj_norm or proj_norm not in norm:
            return 'cross_project'

    # 2. noise
    if _is_noise(norm):
        return 'noise'

    # 3. form_field
    if norm.startswith('form_input_') or norm.startswith('banner_input_'):
        return 'form_field'
    if norm in {'name', 'email', 'phone'}:
        return 'form_field'

    # 4. nav_projects
    if norm.startswith('our_projects_') or norm.startswith('other_projects'):
        return 'nav_projects'
    if norm == 'home_about_us':
        return 'nav_projects'

    # 5. primary_cta
    for kw in _PRIMARY_KEYWORDS:
        if kw in norm:
            return 'primary_cta'

    # 6. secondary_cta
    for kw in _SECONDARY_KEYWORDS:
        if kw in norm:
            return 'secondary_cta'

    # 7. unclassified
    return 'unclassified'


def _is_noise(norm: str) -> bool:
    """Return True if the normalised id matches any noise pattern."""
    # Exact match
    if norm in _NOISE_EXACT:
        return True

    # menu_item_N
    if _NOISE_MENU_ITEM_RE.match(norm):
        return True

    # Prefix
    for pfx in _NOISE_PREFIX:
        if norm.startswith(pfx):
            return True

    # Suffix
    for sfx in _NOISE_SUFFIX:
        if norm.endswith(sfx):
            return True

    # Contains
    for sub in _NOISE_CONTAINS:
        if sub in norm:
            return True

    return False


# ── Convenience: classify a whole list ───────────────────────────────────────

def classify_items(items: list[dict], project_name: str = '') -> list[dict]:
    """
    Add a 'category' field to each item dict (in-place) and return the list.

    Each item must have at least {'click_id': str}.
    """
    for item in items:
        item['category'] = classify(item['click_id'], project_name)
    return items
