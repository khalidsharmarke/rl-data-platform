import string


def slugify(text):
    """Lowercase the words in text, drop punctuation, and join them with hyphens."""
    words = ("".join(ch for ch in w if ch not in string.punctuation) for w in text.split())
    return "-".join(w.lower() for w in words if w)
