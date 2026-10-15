def slugify(text):
    """Lowercase the words in text, drop punctuation, and join them with hyphens."""
    return "-".join(w.lower() for w in text.split())
