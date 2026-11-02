"""Импортирует только Ice001 1K от ambientCG, сохраняя карты и их контрольные суммы."""
import hashlib
import io
import json
from pathlib import Path
from urllib.request import Request, urlopen
import zipfile

from PIL import Image


def main():
    asset = 'Ice001'
    archive_url = f'https://ambientcg.com/get?file={asset}_1K-JPG.zip'
    request = Request(archive_url, headers={'User-Agent': 'TTRPGSiteZone material importer'})
    with urlopen(request, timeout=60) as response:
        raw = response.read()
    folder = Path(__file__).resolve().parents[1] / 'app/static/materials/terrain/ice001'
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {'id': asset, 'sourceUrl': f'https://ambientcg.com/view?id={asset}',
                'license': 'CC0', 'licenseUrl': 'https://docs.ambientcg.com/license/',
                'resolution': '1k', 'normalFormat': 'OpenGL', 'archiveUrl': archive_url,
                'archiveSha256': hashlib.sha256(raw).hexdigest(),
                'packing': 'ARM: AO or neutral 1 / Roughness / metallic 0', 'maps': {}, 'sourceMaps': {}}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        def channel(suffix, optional=False):
            names = [name for name in archive.namelist() if name.endswith(f'_{suffix}.jpg')]
            if not names and optional:
                return None
            if len(names) != 1:
                raise ValueError(f'Неоднозначная карта {suffix}: {names}')
            content = archive.read(names[0])
            manifest['sourceMaps'][suffix] = {'entry': names[0], 'sha256': hashlib.sha256(content).hexdigest()}
            return content

        for key, suffix in [('color', 'Color'), ('normal', 'NormalGL')]:
            content = channel(suffix)
            image = Image.open(io.BytesIO(content))
            if image.size != (1024, 1024):
                raise ValueError('Ожидалась карта 1K')
            target = folder / f'{key}.jpg'
            target.write_bytes(content)
            manifest['maps'][key] = {'file': target.name, 'sha256': hashlib.sha256(content).hexdigest()}
        roughness = Image.open(io.BytesIO(channel('Roughness'))).convert('L')
        ao_bytes = channel('AmbientOcclusion', optional=True)
        ao = Image.open(io.BytesIO(ao_bytes)).convert('L') if ao_bytes else Image.new('L', roughness.size, 255)
        if roughness.size != (1024, 1024) or ao.size != roughness.size:
            raise ValueError('Размеры карт ARM не совпали')
        target = folder / 'arm.png'
        Image.merge('RGB', (ao, roughness, Image.new('L', roughness.size, 0))).save(target)
        manifest['maps']['arm'] = {'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (folder / 'material.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Импортирован Ice001 1K: Color / NormalGL / ARM; источник и SHA-256 записаны.')


if __name__ == '__main__':
    main()
