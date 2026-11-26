import os
import re
import stat
from pathlib import PurePath
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile


_ASSET_PATH = r'/api/assets/(?P<asset>[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})'
_SLIDE_PATH = r'/slides/(?P<page>[1-9][0-9]{0,2})\.jpg'
_ASSET_URL = re.compile(r'(?<![\w/.:@-])' + _ASSET_PATH + r'(?:' + _SLIDE_PATH + r')?(?![\w/-])')
_SLIDE_IMAGE = re.compile(r'!\[(?P<alt>[^\]\n]*)\]\(' + _ASSET_PATH + _SLIDE_PATH + r'\)')
_UNSAFE_FILENAME = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_WINDOWS_DEVICE = re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', re.IGNORECASE)


def filename(value, default):
    clean = _UNSAFE_FILENAME.sub('_', value).strip(' .')
    if _WINDOWS_DEVICE.fullmatch(clean.partition('.')[0]):
        clean = '_' + clean
    if len(clean.encode()) > 230:
        extension = PurePath(clean).suffix
        if len(extension.encode()) > 10:
            extension = ''
        clean = clean.encode()[:230 - len(extension.encode())].decode(errors='ignore').rstrip(' .') + extension
    return clean or default


def localized_markdown(markdown, assets):
    def local_url(match):
        target = assets.get(match['asset'])
        if not target:
            return match[0]
        url = quote(target, safe='/')
        return url + '#page=' + match['page'] if match['page'] else url

    # A slide preview is served by LectureVault. In the archive it opens the original PDF page.
    markdown = _SLIDE_IMAGE.sub(lambda match: match[0][1:] if match['asset'] in assets else match[0], markdown)
    return _ASSET_URL.sub(local_url, markdown)


def open_asset_fd(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if stat.S_ISREG(os.fstat(fd).st_mode):
        return fd
    os.close(fd)
    raise OSError('Expected a regular file')


class _ZipOutput:
    """Let ZipFile write records sequentially without buffering the entire archive."""
    def __init__(self):
        self.chunks = []

    def write(self, data):
        if data:
            self.chunks.append(data)
        return len(data)

    def flush(self):
        pass

    def drain(self):
        chunks, self.chunks = self.chunks, []
        return chunks


def stream_archive(title, markdown, entries):
    output = _ZipOutput()
    with ZipFile(output, mode='w') as archive:
        archive.writestr(title + '.md', markdown, compress_type=ZIP_DEFLATED)
        yield from output.drain()
        for name, path in entries:
            with os.fdopen(open_asset_fd(path), 'rb') as source, archive.open(name, 'w') as member:
                while chunk := source.read(256 * 1024):
                    member.write(chunk)
                    yield from output.drain()
            yield from output.drain()
    yield from output.drain()
