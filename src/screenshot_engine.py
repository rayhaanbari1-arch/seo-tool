"""
Screenshot engine using Playwright.

Strategy per URL:
1. Navigate (headless Chromium, 1440×900)
2. Take one full-page screenshot
3. Deep-inspect the DOM: for each click_id, search ALL attributes of ALL elements
   for the click_id string. This catches GTM data-* attributes, onclick handlers,
   class names, IDs, aria-labels — anything that contains the tracking identifier.
4. Annotate found elements on the full-page screenshot with colored borders + labels
5. Return: one annotated screenshot per page + list of what was found/not found
"""

import asyncio
import base64
import io
import logging
import time
from PIL import Image, ImageDraw

from playwright.async_api import async_playwright

log = logging.getLogger('screenshot_engine')


HIGHLIGHT_BORDER = 4

# High-contrast colors that stand out against any site palette
PALETTE = [
    (0, 200, 255),    # cyan
    (255, 0, 128),    # hot pink
    (0, 230, 118),    # neon green
    (130, 80, 255),   # purple
    (255, 213, 0),    # yellow
    (0, 128, 255),    # blue
    (255, 100, 200),  # pink
    (0, 200, 170),    # teal
]

# Max page height to include in the overview screenshot (px, before scaling)
OVERVIEW_MAX_HEIGHT = 3000



def _to_b64(img: Image.Image, quality: int = 80) -> str:
    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='JPEG', quality=quality)
    return base64.b64encode(buf.getvalue()).decode('utf-8')


def _annotate_screenshot(full_png: bytes, found_elements: list[dict]) -> str:
    """
    Crop to a reasonable page height, scale down, THEN draw annotations
    so labels are readable at output size.
    """
    img = Image.open(io.BytesIO(full_png)).convert('RGBA')
    orig_w, orig_h = img.size

    # Crop to max height (only show the portion of the page where elements exist)
    if found_elements:
        max_elem_y = max(
            int(e['bbox']['y'] + e['bbox']['height']) for e in found_elements
        )
        crop_h = min(orig_h, max(max_elem_y + 200, OVERVIEW_MAX_HEIGHT))
    else:
        crop_h = min(orig_h, 900)  # just viewport if nothing found

    img = img.crop((0, 0, orig_w, crop_h))
    orig_h = crop_h

    # Scale down
    max_w = 1400
    ratio = min(1.0, max_w / orig_w)
    scaled_w = int(orig_w * ratio)
    scaled_h = int(orig_h * ratio)
    img = img.resize((scaled_w, scaled_h), Image.LANCZOS)

    if found_elements:
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        label_h = 24
        border_w = 5

        for i, elem in enumerate(found_elements):
            bbox = elem['bbox']
            x = int(bbox['x'] * ratio)
            y = int(bbox['y'] * ratio)
            bw = int(bbox['width'] * ratio)
            bh = int(bbox['height'] * ratio)

            # Skip elements that are outside the visible area
            if y > scaled_h or bw <= 0 or bh <= 0:
                continue

            color = PALETTE[i % len(PALETTE)]

            # White outline (wider) then colored outline (narrower) for contrast
            draw.rectangle([x - 2, y - 2, x + bw + 2, y + bh + 2],
                           outline=(255, 255, 255, 200), width=border_w + 2)
            draw.rectangle([x, y, x + bw, y + bh],
                           fill=color + (30,), outline=color + (255,), width=border_w)

            # Label pill above element
            cid_short = elem['click_id'][:25]
            label = f"  #{elem['rank']}  {cid_short}  "
            lx = max(0, x)
            ly = max(0, y - label_h - 6)
            lw = min(len(label) * 8 + 8, scaled_w - lx)
            if lw <= 0:
                continue
            # White shadow behind label for contrast
            draw.rectangle([lx - 1, ly - 1, lx + lw + 1, ly + label_h + 1],
                           fill=(255, 255, 255, 200))
            draw.rectangle([lx, ly, lx + lw, ly + label_h], fill=color + (240,))
            draw.text((lx + 6, ly + 4), label, fill=(255, 255, 255, 255))

        img = Image.alpha_composite(img, overlay)

    return _to_b64(img.convert('RGB'))


def _crop_element(full_png: bytes, bbox: dict, color_idx: int = 0) -> str:
    """
    Crop a contextual region around one element with a high-contrast highlight.
    Uses white outer border + colored inner border for maximum visibility.
    """
    img = Image.open(io.BytesIO(full_png)).convert('RGBA')
    w, h = img.size

    ex, ey = int(bbox['x']), int(bbox['y'])
    ew, eh = int(bbox['width']), int(bbox['height'])

    # Generous padding — show surrounding context
    # Minimum crop: 600×300 so even tiny buttons have context
    pad = 100
    min_crop_w, min_crop_h = 600, 300
    max_crop_w, max_crop_h = 1000, 500

    crop_w = max(min_crop_w, min(ew + pad * 2, max_crop_w))
    crop_h = max(min_crop_h, min(eh + pad * 2, max_crop_h))

    # Center crop on the element
    cx = ex + ew // 2
    cy = ey + eh // 2

    cx1 = max(0, cx - crop_w // 2)
    cy1 = max(0, cy - crop_h // 2)
    cx2 = min(w, cx1 + crop_w)
    cy2 = min(h, cy1 + crop_h)

    # Re-adjust if clamped at edges
    if cx2 - cx1 < crop_w:
        cx1 = max(0, cx2 - crop_w)
    if cy2 - cy1 < crop_h:
        cy1 = max(0, cy2 - crop_h)

    cropped = img.crop((cx1, cy1, cx2, cy2)).copy()

    # Draw highlight with high contrast
    draw = ImageDraw.Draw(cropped, 'RGBA')
    rx1 = max(0, ex - cx1)
    ry1 = max(0, ey - cy1)
    rx2 = min(rx1 + ew, cropped.width)
    ry2 = min(ry1 + eh, cropped.height)

    # Skip if element is outside the crop area
    if rx2 <= rx1 or ry2 <= ry1:
        return _to_b64(cropped.convert('RGB'))

    color = PALETTE[color_idx % len(PALETTE)]

    # White outer border → colored inner border → transparent fill
    draw.rectangle([rx1 - 3, ry1 - 3, rx2 + 3, ry2 + 3],
                   outline=(255, 255, 255, 220), width=4)
    draw.rectangle([rx1, ry1, rx2, ry2],
                   fill=color + (25,), outline=color + (255,), width=4)

    return _to_b64(cropped.convert('RGB'))


VIEWPORT_HEIGHT = 900
VIEWPORT_WIDTH  = 1440
MOBILE_WIDTH    = 390
MOBILE_HEIGHT   = 844

# Batch JS: finds all click IDs in two passes.
# Pass 1: GTM-mapped IDs — use the exact CSS selector from the GTM container JSON.
# Pass 2: Fuzzy fallback for anything not in the GTM map — DOM attribute walk.
#   Short IDs (<8 chars) only match [id] exact; long IDs may fuzzy-match any attr.
_FIND_ALL_ELEMENTS_JS = '''
({clickIds, gtmMap}) => {
    const results = {};
    for (const cid of clickIds) results[cid] = null;

    const gtmErrors = {};  // {click_id: reason} for GTM selector failures

    // Pass 1: GTM-mapped selectors (exact, O(1) per element)
    for (const cid of clickIds) {
        const selector = gtmMap[cid];
        if (!selector) continue;
        try {
            const el = document.querySelector(selector);
            if (!el) {
                gtmErrors[cid] = `selector "${selector}" matched no element`;
                continue;
            }
            const rect = el.getBoundingClientRect();
            if (rect.width < 5 || rect.height < 5) {
                gtmErrors[cid] = `selector "${selector}" element has zero/tiny size (${Math.round(rect.width)}x${Math.round(rect.height)}) — may be hidden`;
                continue;
            }
            const pos = window.getComputedStyle(el).position;
            results[cid] = {
                x: rect.x + window.scrollX,
                y: rect.y + window.scrollY,
                width: rect.width,
                height: rect.height,
                tag: el.tagName.toLowerCase(),
                matchType: 'gtm',
                area: rect.width * rect.height,
                position: pos,
            };
        } catch(e) {
            gtmErrors[cid] = `querySelector("${selector}") threw: ${e.message}`;
        }
    }

    // Pass 2: fuzzy fallback for IDs not resolved by GTM map
    const SHORT_ID_LIMIT = 8;
    const lookup = {};
    for (const cid of clickIds) {
        if (results[cid] !== null) continue; // already found via GTM
        const norm = cid.toLowerCase();
        const alt = norm.includes('-') ? norm.replace(/-/g, '_')
                  : norm.includes('_') ? norm.replace(/_/g, '-')
                  : null;
        lookup[norm] = {original: cid, alt, short: cid.length < SHORT_ID_LIMIT};
    }

    if (Object.keys(lookup).length === 0) return results;

    for (const el of document.querySelectorAll('*')) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) continue;

        for (const attr of el.attributes) {
            const val = attr.value.toLowerCase();
            for (const [norm, info] of Object.entries(lookup)) {
                if (results[info.original] !== null) continue;

                let matched = false;
                if (attr.name === 'id') {
                    matched = val === norm || (info.alt && val === info.alt);
                } else if (!info.short) {
                    matched = val.includes(norm) || (info.alt && val.includes(info.alt));
                }

                if (matched) {
                    const area = rect.width * rect.height;
                    if (area < 100) continue;
                    const pos = window.getComputedStyle(el).position;
                    results[info.original] = {
                        x: rect.x + window.scrollX,
                        y: rect.y + window.scrollY,
                        width: rect.width,
                        height: rect.height,
                        tag: el.tagName.toLowerCase(),
                        matchType: attr.name,
                        area,
                        position: pos,
                    };
                }
            }
        }
    }
    return {matches: results, gtmErrors};
}
'''


def _compute_depth(element_top: float, page_height: int,
                   viewport_height: int = VIEWPORT_HEIGHT) -> float | None:
    """
    Compute normalised element depth using the viewport-correction formula from §2.3.

    depth = max(0, element_top - viewport_height) / (page_height - viewport_height)

    Returns None when page_height <= viewport_height (degenerate page).
    """
    denom = page_height - viewport_height
    if denom <= 0:
        return 0.0
    seen_at_scroll = max(0.0, element_top - viewport_height)
    return min(1.0, seen_at_scroll / denom)


async def _capture_page(browser, url: str, click_ids: list[str],
                        gtm_mappings: dict | None = None) -> dict:
    """
    Capture a page and deep-inspect the DOM to find tracked elements.

    Returns {
        'screenshot': base64 (annotated full-page),
        'element_crops': {click_id: base64} (per-element cropped screenshots),
        'found_elements': [{'click_id', 'rank', 'match_type', 'tag', 'bbox'}],
        'found_count': int,
        'page_height': int,
        'viewport_height': int,
        'depths': {click_id: float | None},   # None = fixed/sticky
    }
    """
    context = await browser.new_context(
        viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
        user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        )
    )
    page = await context.new_page()

    found_elements = []
    element_crops = {}
    screenshot_b64 = ''
    mobile_screenshot_b64 = ''
    mobile_png_bytes = b''
    page_height = 0
    depths: dict[str, float | None] = {}

    t0 = time.time()
    log.info('capturing %s  (%d click IDs)', url, len(click_ids))
    try:
        # domcontentloaded is much faster than networkidle on heavy marketing pages
        await page.goto(url, wait_until='domcontentloaded', timeout=45000)
        log.debug('page loaded in %.1fs', time.time() - t0)
        await page.wait_for_timeout(1000)

        # Dismiss cookie / popup banners
        for sel in [
            '[class*="cookie"] button', '[id*="cookie"] button',
            'button[class*="accept"]', '[class*="consent"] button',
            '[class*="popup"] [class*="close"]',
            '[class*="modal"] [class*="close"]',
        ]:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=1000)
                    await page.wait_for_timeout(300)
            except Exception:
                pass

        # Fix 3: scroll top-to-bottom FIRST to trigger all lazy-loaded elements,
        # then run DOM inspection while still at the bottom so those elements are
        # in the DOM. Only scroll back to top afterwards for the screenshot.
        page_height = await page.evaluate('''async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const step = window.innerHeight * 0.7;
            let h = document.body.scrollHeight;
            for (let y = 0; y < h; y += step) {
                window.scrollTo(0, y);
                await delay(150);
                h = document.body.scrollHeight;
            }
            window.scrollTo(0, h);
            await delay(200);
            return document.body.scrollHeight;
            // NOTE: intentionally stays at bottom — DOM inspection runs next
        }''')

        log.debug('scroll complete (at bottom), page_height=%dpx  elapsed=%.1fs',
                  page_height, time.time() - t0)

        # Single batched DOM inspection while at page bottom (all lazy elements loaded)
        # Pass GTM mappings as pass-1 exact selectors; fuzzy fallback for the rest.
        try:
            raw = await page.evaluate(
                _FIND_ALL_ELEMENTS_JS,
                {'clickIds': click_ids, 'gtmMap': gtm_mappings or {}}
            )
            all_matches  = raw.get('matches', {})
            gtm_errors   = raw.get('gtmErrors', {})
        except Exception as e:
            log.error('batch DOM inspect failed for %s: %s', url, e)
            all_matches = {}
            gtm_errors  = {}

        gtm_hits   = sum(1 for v in all_matches.values() if v and v.get('matchType') == 'gtm')
        fuzzy_hits = sum(1 for v in all_matches.values() if v and v.get('matchType') not in ('gtm', None))

        # Log GTM selector failures so the SEO team knows which mappings are broken
        for cid, reason in gtm_errors.items():
            log.warning('GTM selector failed  click_id=%r  reason=%s', cid, reason)

        # Mobile retry: for any still-unmatched click IDs, resize to mobile viewport
        # and re-run the JS. Elements only visible on mobile (hidden via CSS at 1440px)
        # will be found here and flagged as match_type='mobile'.
        unmatched_after_desktop = [cid for cid in click_ids if not all_matches.get(cid)]
        mobile_hits = 0
        if unmatched_after_desktop:
            try:
                await page.set_viewport_size({'width': MOBILE_WIDTH, 'height': MOBILE_HEIGHT})
                await page.wait_for_timeout(300)
                mobile_raw = await page.evaluate(
                    _FIND_ALL_ELEMENTS_JS,
                    {'clickIds': unmatched_after_desktop, 'gtmMap': gtm_mappings or {}}
                )
                mobile_matches = mobile_raw.get('matches', {})
                for cid, m in mobile_matches.items():
                    if m:
                        m['matchType'] = 'mobile'
                        all_matches[cid] = m
                        mobile_hits += 1
                # Take mobile full-page screenshot while still at mobile viewport
                await page.evaluate('window.scrollTo(0, 0)')
                await page.wait_for_timeout(400)
                mobile_png_bytes = await page.screenshot(full_page=True, type='png')
                # Restore desktop viewport for the main screenshot
                await page.set_viewport_size({'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT})
                await page.wait_for_timeout(300)
                log.debug('mobile retry for %s: %d/%d newly matched',
                          url, mobile_hits, len(unmatched_after_desktop))
            except Exception as mob_err:
                log.error('mobile viewport retry failed for %s: %s', url, mob_err)
                await page.set_viewport_size({'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT})

        found_count_pre = sum(1 for v in all_matches.values() if v is not None)
        log.info('DOM inspection %s: %d/%d matched  (%d via GTM, %d via fuzzy, %d mobile-only)',
                 url, found_count_pre, len(click_ids), gtm_hits, fuzzy_hits, mobile_hits)

        # Log every unmatched click ID so it's visible without opening the report
        unmatched = [cid for cid in click_ids if not all_matches.get(cid)]
        if unmatched:
            log.warning('Unmatched click IDs on %s (%d): %s',
                        url, len(unmatched), ', '.join(unmatched))

        # Scroll back to top and wait for screenshot
        await page.evaluate('window.scrollTo(0, 0)')
        await page.wait_for_timeout(800)

        # Full-page screenshot
        full_png = await page.screenshot(full_page=True, type='png')
        log.debug('screenshot taken (%.1f KB)  elapsed=%.1fs',
                  len(full_png) / 1024, time.time() - t0)

        desktop_elements = []  # elements found at desktop viewport
        mobile_elements  = []  # elements found only at mobile viewport

        for rank, click_id in enumerate(click_ids, start=1):
            match = all_matches.get(click_id)
            if match:
                bbox = {
                    'x': match['x'], 'y': match['y'],
                    'width': match['width'], 'height': match['height'],
                }
                is_mobile = match.get('matchType') == 'mobile'

                position = match.get('position', '')
                if position in ('fixed', 'sticky'):
                    depths[click_id] = 0.0
                elif is_mobile:
                    # Mobile-only: depth is not meaningful at desktop resolution
                    depths[click_id] = None
                else:
                    depths[click_id] = _compute_depth(match['y'], page_height, VIEWPORT_HEIGHT)

                elem = {
                    'click_id': click_id,
                    'rank': rank,
                    'bbox': bbox,
                    'match_type': match['matchType'],
                    'tag': match['tag'],
                }
                found_elements.append(elem)

                if is_mobile:
                    mobile_elements.append(elem)
                    # Crop from the mobile PNG using mobile-layout coordinates
                    if mobile_png_bytes:
                        try:
                            element_crops[click_id] = _crop_element(
                                mobile_png_bytes, bbox, color_idx=len(mobile_elements) - 1)
                        except Exception as crop_err:
                            log.error('mobile crop failed  click_id=%r  url=%s  error=%s',
                                      click_id, url, crop_err)
                else:
                    desktop_elements.append(elem)
                    try:
                        element_crops[click_id] = _crop_element(
                            full_png, bbox, color_idx=len(desktop_elements) - 1)
                    except Exception as crop_err:
                        log.error('crop failed  click_id=%r  url=%s  error=%s',
                                  click_id, url, crop_err)

                log.debug('  #%d %r -> <%s> via [%s]  depth=%s',
                          rank, click_id, match['tag'], match['matchType'], depths[click_id])

        # Annotate desktop screenshot with desktop-only elements (mobile bboxes are
        # in mobile-layout coordinates and would be misplaced on the 1440px screenshot)
        screenshot_b64 = _annotate_screenshot(full_png, desktop_elements)

        # Annotate mobile screenshot with mobile-only elements
        if mobile_png_bytes and mobile_elements:
            mobile_screenshot_b64 = _annotate_screenshot(mobile_png_bytes, mobile_elements)
        elif mobile_png_bytes:
            mobile_screenshot_b64 = _to_b64(Image.open(io.BytesIO(mobile_png_bytes)))

    except Exception as e:
        log.error('error capturing %s: %s', url, e)

    finally:
        await context.close()
        log.info('done with %s  found=%d/%d  elapsed=%.1fs',
                 url, len(found_elements), len(click_ids), time.time() - t0)

    return {
        'screenshot': screenshot_b64,
        'mobile_screenshot': mobile_screenshot_b64,
        'element_crops': element_crops,
        'found_elements': [
            {'click_id': e['click_id'], 'rank': e['rank'],
             'match_type': e.get('match_type', ''), 'tag': e.get('tag', ''),
             'bbox': e.get('bbox', {})}
            for e in found_elements
        ],
        'found_count': len(found_elements),
        'page_height': page_height,
        'viewport_height': VIEWPORT_HEIGHT,
        'depths': depths,
    }



async def capture_all_pages(links: list[dict], project_data: dict,
                           gtm_mappings: dict | None = None) -> dict:
    """
    Capture screenshots for all configured page links.

    project_data uses the v2 structure:
        {sheet_name: {'pages': {'lp': {...}, 'project': {...}}, 'flags': [...]}}

    Returns:
        {
            sheet_name: {
                'lp': {
                    'screenshot': b64,
                    'element_crops': {click_id: b64},
                    'found_count': int,
                    'found_elements': [...],
                    'page_height': int,
                    'viewport_height': int,
                    'depths': {click_id: float | None},
                },
                'project': { ... },
            }
        }
    """
    # Build the list of unique (sheet, page_type, url, click_ids) tasks first
    tasks = []
    seen = set()
    for link in links:
        project_name = link['project_name']
        url = link['url']
        page_type = link['page_type']

        matched_sheet = next(
            (s for s in project_data if s.strip().lower() == project_name.strip().lower()),
            None
        )
        if not matched_sheet:
            print(f'[screenshot_engine] No Excel sheet matched "{project_name}" — skipping')
            continue

        key = (matched_sheet, page_type)
        if key in seen:
            continue
        seen.add(key)

        page_info = project_data[matched_sheet].get('pages', {}).get(page_type, {})
        click_ids = [
            item['click_id']
            for item in page_info.get('items', [])
            if item.get('clicks', 0) > 0
        ][:15]

        if not click_ids:
            print(f'[screenshot_engine] No click IDs for {project_name}/{page_type}')
            continue

        tasks.append((matched_sheet, page_type, url, click_ids))

    results: dict = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        async def _run(sheet, page_type, url, click_ids):
            print(f'[screenshot_engine] Capturing {url} ({len(click_ids)} CTAs)...')
            result = await _capture_page(browser, url, click_ids, gtm_mappings)
            print(
                f'[screenshot_engine]   -> {result["found_count"]}/{len(click_ids)} '
                f'found  page_height={result["page_height"]}px'
            )
            return sheet, page_type, result

        # Capture all pages in parallel
        captures = await asyncio.gather(
            *[_run(s, pt, u, ids) for s, pt, u, ids in tasks],
            return_exceptions=True
        )

        for capture in captures:
            if isinstance(capture, Exception):
                print(f'[screenshot_engine] Capture failed: {capture}')
                continue
            sheet, page_type, page_result = capture
            if sheet not in results:
                results[sheet] = {}
            results[sheet][page_type] = page_result

        await browser.close()

    return results
