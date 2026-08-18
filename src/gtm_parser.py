"""
GTM Container JSON parser.

Extracts click event_name → CSS selector mappings from a GTM container export.

Supported trigger patterns:
  {{Click ID}}      EQUALS / CONTAINS  → #id selector
  {{Click Classes}} CONTAINS           → .class selector
  {{Click URL}}     CONTAINS / EQUALS  → a[href*="..."] selector
  {{Click Element}} (CSS selector)     → passed through directly
"""

import logging
import re

log = logging.getLogger('gtm_parser')

_GA4_TAG_TYPES = {'gaawe', 'googtag', 'gaawc'}

_VAR_TO_TYPE = {
    '{{click id}}':      'id',
    '{{click classes}}': 'class',
    '{{click url}}':     'href',
    '{{click element}}': 'css',
    '{{click text}}':    'text',
}

_CLICK_TRIGGER_TYPES = {'click', 'link_click', 'element_visibility'}


def _get_param(params: list, key: str) -> str | None:
    for p in params:
        if p.get('key') == key:
            return p.get('value') or ''
    return None


def _build_css_selector(selector_type: str, value: str, condition: str) -> str | None:
    v = value.strip()
    if not v:
        return None

    if selector_type == 'id':
        safe = re.sub(r'[^\w-]', lambda m: '\\' + m.group(0), v)
        return f'#{safe}'

    elif selector_type == 'class':
        classes = v.split()
        if not classes:
            return None
        if condition == 'EQUALS':
            return f'[class="{v}"]'
        return ''.join(f'.{c}' for c in classes)

    elif selector_type == 'href':
        v_esc = v.replace('"', '\\"')
        if condition == 'EQUALS':
            return f'a[href="{v_esc}"]'
        elif condition == 'STARTS_WITH':
            return f'a[href^="{v_esc}"]'
        elif condition == 'ENDS_WITH':
            return f'a[href$="{v_esc}"]'
        else:
            return f'a[href*="{v_esc}"]'

    elif selector_type == 'css':
        return v

    return None


def parse_gtm_container(data: dict) -> list[dict]:
    """
    Parse a GTM container export (dict from JSON).

    Returns a list of mappings:
    [
        {
            'event_name':     str,
            'selector_type':  str,   # id | class | href | css
            'selector_value': str,
            'css_selector':   str,
            'trigger_name':   str,
            'tag_name':       str,
        },
        ...
    ]

    Raises ValueError if the file is not a recognisable GTM container export.
    Logs a detailed skip-reason breakdown at INFO level after parsing.
    """
    cv = (data.get('containerVersion')
          or data.get('resource')
          or data.get('container'))
    if not cv:
        raise ValueError(
            'Not a valid GTM container export — expected top-level key '
            '"containerVersion", "resource", or "container".'
        )

    tags     = cv.get('tag', [])
    triggers = cv.get('trigger', [])

    if not tags and not triggers:
        raise ValueError('GTM export contains no tags or triggers.')

    log.info('GTM container: %d tags, %d triggers', len(tags), len(triggers))

    trigger_map: dict[str, dict] = {
        str(t['triggerId']): t
        for t in triggers
        if 'triggerId' in t
    }

    # Counters for the summary log
    skips = {
        'not_ga4_tag':         0,
        'no_firing_trigger':   0,
        'trigger_not_found':   0,
        'trigger_wrong_type':  0,
        'filter_unknown_var':  0,
        'filter_text_match':   0,
        'unresolvable_name':   0,
        'no_selector_built':   0,
        'duplicate_event':     0,
    }

    mappings: list[dict] = []
    seen_events: set[str] = set()

    for tag in tags:
        tag_type = tag.get('type', '').lower()
        tag_name = tag.get('name', 'unnamed')

        if tag_type not in _GA4_TAG_TYPES:
            skips['not_ga4_tag'] += 1
            continue

        params         = tag.get('parameter', [])
        event_name_raw = _get_param(params, 'eventName') or ''
        firing_ids     = [str(tid) for tid in tag.get('firingTriggerId', [])]

        if not firing_ids:
            skips['no_firing_trigger'] += 1
            log.debug('SKIP tag %r — no firingTriggerId', tag_name)
            continue

        for tid in firing_ids:
            trigger = trigger_map.get(tid)
            if not trigger:
                skips['trigger_not_found'] += 1
                log.warning('SKIP tag %r trigger %s — trigger ID not found in export '
                            '(may be a built-in trigger like All Pages)', tag_name, tid)
                continue

            trigger_type = trigger.get('type', '').lower()
            trigger_name = trigger.get('name', tid)

            if trigger_type not in _CLICK_TRIGGER_TYPES:
                skips['trigger_wrong_type'] += 1
                log.debug('SKIP tag %r trigger %r — type %r is not a click trigger',
                          tag_name, trigger_name, trigger_type)
                continue

            filters = trigger.get('filter', [])
            if not filters:
                log.warning('SKIP tag %r trigger %r — click trigger has no filters '
                            '(fires on ALL clicks, cannot map to a specific element)',
                            tag_name, trigger_name)
                skips['filter_unknown_var'] += 1
                continue

            for f in filters:
                condition = f.get('type', '')
                fparams   = f.get('parameter', [])
                arg0 = (_get_param(fparams, 'arg0') or '').lower().strip()
                arg1 = (_get_param(fparams, 'arg1') or '').strip()

                selector_type = _VAR_TO_TYPE.get(arg0)

                if not selector_type:
                    skips['filter_unknown_var'] += 1
                    log.debug('SKIP tag %r trigger %r — filter variable %r not supported '
                              '(only Click ID/Classes/URL/Element are mappable)',
                              tag_name, trigger_name, arg0)
                    continue

                if selector_type == 'text':
                    skips['filter_text_match'] += 1
                    log.debug('SKIP tag %r trigger %r — Click Text filters cannot be '
                              'reliably mapped to a DOM element', tag_name, trigger_name)
                    continue

                # Resolve event name
                en_lower = event_name_raw.lower()
                if '{{click id}}' in en_lower and selector_type == 'id':
                    event_name = arg1
                elif event_name_raw and '{{' not in event_name_raw:
                    event_name = event_name_raw
                else:
                    skips['unresolvable_name'] += 1
                    log.warning('SKIP tag %r trigger %r — event name %r uses a GTM '
                                'variable that cannot be statically resolved',
                                tag_name, trigger_name, event_name_raw)
                    continue

                if not event_name:
                    skips['unresolvable_name'] += 1
                    log.warning('SKIP tag %r trigger %r — event name resolved to empty string',
                                tag_name, trigger_name)
                    continue

                if event_name in seen_events:
                    skips['duplicate_event'] += 1
                    log.debug('SKIP tag %r trigger %r — event %r already mapped '
                              '(first mapping wins)', tag_name, trigger_name, event_name)
                    continue

                css_sel = _build_css_selector(selector_type, arg1, condition)
                if not css_sel:
                    skips['no_selector_built'] += 1
                    log.warning('SKIP tag %r trigger %r — could not build CSS selector '
                                'from %r=%r condition=%r',
                                tag_name, trigger_name, selector_type, arg1, condition)
                    continue

                seen_events.add(event_name)
                mappings.append({
                    'event_name':     event_name,
                    'selector_type':  selector_type,
                    'selector_value': arg1,
                    'css_selector':   css_sel,
                    'trigger_name':   trigger_name,
                    'tag_name':       tag_name,
                })
                log.debug('MAPPED %r → %r  (trigger: %r)', event_name, css_sel, trigger_name)

    total_skipped = sum(skips.values())
    log.info(
        'GTM parse complete: %d mappings extracted, %d items skipped\n'
        '  not_ga4_tag=%d  no_firing_trigger=%d  trigger_not_found=%d\n'
        '  trigger_wrong_type=%d  filter_unknown_var=%d  filter_text_match=%d\n'
        '  unresolvable_name=%d  no_selector_built=%d  duplicate_event=%d',
        len(mappings), total_skipped,
        skips['not_ga4_tag'], skips['no_firing_trigger'], skips['trigger_not_found'],
        skips['trigger_wrong_type'], skips['filter_unknown_var'], skips['filter_text_match'],
        skips['unresolvable_name'], skips['no_selector_built'], skips['duplicate_event'],
    )

    if len(mappings) == 0:
        log.warning(
            'No mappings extracted — check that your GTM container has GA4 Event tags '
            'with Click ID / Click Classes / Click URL trigger filters. '
            'Skip breakdown above shows why each tag was excluded.'
        )

    return mappings
