import unicodedata

# Latin, Cyrillic and Greek letters that look the same in a brand name.
_LOOKALIKES = str.maketrans({
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x',
    'ё': 'e', 'і': 'i', 'ї': 'i', 'ј': 'j', 'ѕ': 's', 'һ': 'h',
    '⍺': 'a', 'ο': 'o', 'ρ': 'p', 'υ': 'y', 'χ': 'x', 'τ': 't', 'κ': 'k', 'ν': 'v',
})


def brand_name_key(name):
    """Fold case, spaces and lookalike letters so copied brand names collide."""
    text = unicodedata.normalize('NFKC', name or '').casefold().translate(_LOOKALIKES)
    return ' '.join(text.split())
