"""
Generates the self-contained HTML report from project data + screenshots.
"""

from jinja2 import Environment, FileSystemLoader
from datetime import datetime
import os
import re

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')

MAX_CARDS = 15


def slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def clean_click_id(raw: str) -> str:
    """Make raw click IDs more readable: location-map → Location Map"""
    s = raw.replace('-', ' ').replace('_', ' ')
    return s.title()


def generate_report(
    client_name: str,
    project_data: dict,
    screenshots: dict,
    links: list[dict],
) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    template = env.get_template('report_template.html')

    # URL lookup: {sheet_key: {page_type: url}}
    url_lookup = {}
    for link in links:
        key = link['project_name'].strip().lower()
        if key not in url_lookup:
            url_lookup[key] = {}
        url_lookup[key][link['page_type']] = link['url']

    configured_sheets = set(url_lookup.keys())

    projects = []
    total_clicks = 0
    total_users = 0

    for sheet_name, data in project_data.items():
        sheet_key = sheet_name.strip().lower()
        if sheet_key not in configured_sheets:
            continue

        sheet_screenshots = screenshots.get(sheet_name, {})
        urls = url_lookup.get(sheet_key, {})

        sections = []
        project_clicks = 0
        project_users = 0

        for page_type, title_key in [('lp', 'lp_title'), ('project', 'project_title')]:
            if page_type not in urls:
                continue

            items = data.get(page_type, [])
            items = [i for i in items if i['clicks'] > 0][:MAX_CARDS]
            if not items:
                continue

            # Use the grand total from the Excel sheet (accurate unique user count).
            # Summing per-element users over-counts because one user can click many elements.
            section_clicks = data.get(f'{page_type}_total_clicks') or sum(i['clicks'] for i in items)
            section_users  = data.get(f'{page_type}_total_users')  or sum(i['users']  for i in items)
            project_clicks += section_clicks
            project_users += section_users

            page_sc = sheet_screenshots.get(page_type, {})
            page_screenshot = page_sc.get('screenshot', '')
            element_crops = page_sc.get('element_crops', {})
            found_elements = page_sc.get('found_elements', [])
            found_ids = {e['click_id'] for e in found_elements}
            page_url = urls.get(page_type, '')

            cards = []
            for rank, item in enumerate(items, start=1):
                cid = item['click_id']
                crop = element_crops.get(cid, '')
                cards.append({
                    'rank': rank,
                    'click_id': cid,
                    'display_name': clean_click_id(cid),
                    'clicks': item['clicks'],
                    'users': item['users'],
                    'crop': crop,
                    'found': cid in found_ids,
                })

            sections.append({
                'title': data.get(title_key, f'{sheet_name} — {page_type.upper()}'),
                'page_type': page_type,
                'page_url': page_url,
                'screenshot': page_screenshot,
                'found_count': page_sc.get('found_count', 0),
                'total_ctas': len(items),
                'section_clicks': section_clicks,
                'section_users': section_users,
                'cards': cards,
            })

        if sections:
            total_clicks += project_clicks
            total_users += project_users
            projects.append({
                'name': sheet_name,
                'slug': slugify(sheet_name),
                'sections': sections,
                'project_clicks': project_clicks,
                'project_users': project_users,
                'top_cta': sections[0]['cards'][0]['display_name'] if sections[0]['cards'] else '',
                'top_cta_clicks': sections[0]['cards'][0]['clicks'] if sections[0]['cards'] else 0,
            })

    # Sort projects by total clicks descending for the summary
    projects_by_clicks = sorted(projects, key=lambda p: p['project_clicks'], reverse=True)
    top_performers = projects_by_clicks[:5]

    now = datetime.now()
    report_name = f"{client_name} Event report {now.day} ({now.strftime('%B')}) {now.year}"

    return template.render(
        client_name=client_name,
        report_name=report_name,
        projects=projects,
        total_clicks=total_clicks,
        total_users=total_users,
        top_performers=top_performers,
    )
