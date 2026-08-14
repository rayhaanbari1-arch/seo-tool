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

import base64
import io
from PIL import Image, ImageDraw

from playwright.async_api import async_playwright


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


# JS function injected into the page to find elements by click_id
_FIND_ELEMENT_JS = '''
(clickId) => {
    const cid = clickId.toLowerCase();
    // Also try with hyphens ↔ underscores swapped
    const cidAlt = cid.includes('-') ? cid.replace(/-/g, '_')
                 : cid.includes('_') ? cid.replace(/_/g, '-')
                 : null;

    const matches = [];

    for (const el of document.querySelectorAll('*')) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) continue;

        let matched = false;
        let matchType = '';

        // 1. Check all attributes (id, class, data-*, onclick, href, aria-*, etc.)
        for (const attr of el.attributes) {
            const val = attr.value.toLowerCase();
            if (val.includes(cid) || (cidAlt && val.includes(cidAlt))) {
                matched = true;
                matchType = attr.name;
                break;
            }
        }

        if (matched) {
            matches.push({
                x: rect.x + window.scrollX,
                y: rect.y + window.scrollY,
                width: rect.width,
                height: rect.height,
                tag: el.tagName.toLowerCase(),
                matchType: matchType,
                area: rect.width * rect.height,
            });
        }
    }

    // Prefer the most specific (smallest) visible match
    // But skip tiny elements (< 20px area) — likely hidden tracking pixels
    const valid = matches.filter(m => m.area >= 100);
    valid.sort((a, b) => a.area - b.area);

    return valid.length > 0 ? valid[0] : null;
}
'''


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


async def _capture_page(browser, url: str, click_ids: list[str]) -> dict:
    """
    Capture a page and deep-inspect the DOM to find tracked elements.

    Returns {
        'screenshot': base64 (annotated full-page),
        'element_crops': {click_id: base64} (per-element cropped screenshots),
        'found_elements': [{'click_id', 'rank', 'match_type', 'tag'}],
        'found_count': int,
    }
    """
    context = await browser.new_context(
        viewport={'width': 1440, 'height': 900},
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

    try:
        await page.goto(url, wait_until='networkidle', timeout=45000)
        await page.wait_for_timeout(3000)

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
                    await page.wait_for_timeout(400)
            except Exception:
                pass

        # Scroll top-to-bottom to trigger lazy loading, maps, and scroll animations
        await page.evaluate('''async () => {
            const delay = ms => new Promise(r => setTimeout(r, ms));
            const height = document.body.scrollHeight;
            const step = window.innerHeight * 0.7;
            for (let y = 0; y < height; y += step) {
                window.scrollTo(0, y);
                await delay(300);
            }
            // Hit the very bottom
            window.scrollTo(0, height);
            await delay(500);
            // Scroll back to top
            window.scrollTo(0, 0);
            await delay(300);
        }''')

        # Wait for maps/lazy content to finish rendering after scroll
        await page.wait_for_timeout(3000)

        # Full-page screenshot
        full_png = await page.screenshot(full_page=True, type='png')

        # Deep DOM inspection for each click_id
        for rank, click_id in enumerate(click_ids, start=1):
            try:
                match = await page.evaluate(_FIND_ELEMENT_JS, click_id)
            except Exception as e:
                print(f'[screenshot_engine]   DOM inspect error for "{click_id}": {e}')
                match = None

            if match:
                bbox = {
                    'x': match['x'], 'y': match['y'],
                    'width': match['width'], 'height': match['height'],
                }
                found_elements.append({
                    'click_id': click_id,
                    'rank': rank,
                    'bbox': bbox,
                    'match_type': match['matchType'],
                    'tag': match['tag'],
                })
                # Crop this element from the full-page screenshot
                try:
                    element_crops[click_id] = _crop_element(full_png, bbox, color_idx=len(found_elements) - 1)
                except Exception as crop_err:
                    print(f'[screenshot_engine]   ⚠ Crop failed for "{click_id}": {crop_err}')
                print(f'[screenshot_engine]   ✓ #{rank} "{click_id}" → <{match["tag"]}> matched via [{match["matchType"]}]')

        # Annotate the full-page screenshot
        screenshot_b64 = _annotate_screenshot(full_png, found_elements)

    except Exception as e:
        print(f'[screenshot_engine] Error capturing {url}: {e}')

    finally:
        await context.close()

    return {
        'screenshot': screenshot_b64,
        'element_crops': element_crops,
        'found_elements': [
            {'click_id': e['click_id'], 'rank': e['rank'],
             'match_type': e.get('match_type', ''), 'tag': e.get('tag', '')}
            for e in found_elements
        ],
        'found_count': len(found_elements),
    }


async def capture_all_pages(links: list[dict], project_data: dict) -> dict:
    """
    Capture screenshots for all configured page links.

    Returns:
        {
            sheet_name: {
                'lp': {
                    'screenshot': b64,
                    'element_crops': {click_id: b64},
                    'found_count': int,
                    'found_elements': [...],
                },
                'project': { ... },
            }
        }
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        results = {}
        captured = set()

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

            capture_key = (matched_sheet, page_type)
            if capture_key in captured:
                print(f'[screenshot_engine] Already captured {matched_sheet}/{page_type} — skipping')
                continue

            sheet_data = project_data[matched_sheet]
            click_ids = [
                item['click_id'] for item in sheet_data.get(page_type, [])
                if item['clicks'] > 0
            ][:15]

            if not click_ids:
                print(f'[screenshot_engine] No click IDs for {project_name}/{page_type}')
                continue

            print(f'[screenshot_engine] Capturing {url} ({len(click_ids)} CTAs)...')
            page_result = await _capture_page(browser, url, click_ids)
            print(f'[screenshot_engine]   → {page_result["found_count"]}/{len(click_ids)} elements found via DOM inspection')

            if matched_sheet not in results:
                results[matched_sheet] = {}
            results[matched_sheet][page_type] = page_result
            captured.add(capture_key)

        await browser.close()

    return results
