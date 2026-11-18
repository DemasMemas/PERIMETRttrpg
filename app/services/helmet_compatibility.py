"""Распознавание штатных открытых шлемов в каталоге и старых сохранениях."""

import re


_OPEN_HELMETS = re.compile(
    r'(?:(?:шлем\s+)?(?:витязь\s+4[вbv]76|ударник(?:[\s\-–‑]+м)?|первопроходец)'
    r'|советский\s+кот[её]лок\s+68г|(?:прочее\s+)?ушанка)',
    re.IGNORECASE,
)


def has_open_helmet_shell(item, template_name=None):
    attrs = item.get('attributes')
    catalog_name = attrs.get('catalog_name') if isinstance(attrs, dict) else None
    canonical_name = template_name or catalog_name
    names = (canonical_name,) if canonical_name else (item.get('model'), item.get('name'))
    return any(isinstance(name, str) and _OPEN_HELMETS.fullmatch(name.strip()) for name in names)
