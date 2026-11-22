import re


def embed_slides(markdown, asset_id, pages, limit):
    """Resolve standalone model references, preserving literal examples and section order."""
    output = []
    used = set()
    deferred = []
    has_explanation = False
    fence = ''

    def flush():
        for page in deferred:
            output.extend(['', f'![Слайд {page}](/api/assets/{asset_id}/slides/{page}.jpg)', ''])
        deferred.clear()

    for line in markdown.splitlines(keepends=True):
        value = line.rstrip('\r\n')
        if fence:
            output.append(value)
            if re.fullmatch(r' {0,3}' + fence[0] + '{' + str(len(fence)) + r',}\s*', value):
                fence = ''
            continue
        opening = re.match(r' {0,3}(`{3,}|~{3,})', value)
        if opening:
            fence = opening[1]
            output.append(value)
            continue
        marker = re.fullmatch(r' {0,3}\[\[slide:(\d{1,3})\]\][ \t]*', value)
        if marker:
            page = int(marker[1])
            if page not in used and 1 <= page <= pages and (limit is None or len(used) < limit):
                used.add(page)
                deferred.append(page)
                if has_explanation:
                    flush()
            continue
        if re.match(r' {0,3}#{1,6}\s', value):
            # A section without explanatory text does not need an illustration.
            if has_explanation:
                flush()
            else:
                used.difference_update(deferred)
                deferred.clear()
            has_explanation = False
        elif value.strip():
            has_explanation = True
        elif has_explanation:
            flush()
        output.append(value)
    if has_explanation:
        flush()
    return '\n'.join(output) + ('\n' if markdown.endswith('\n') else '')
