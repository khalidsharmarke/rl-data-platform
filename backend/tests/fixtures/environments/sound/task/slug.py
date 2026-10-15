def slugify(text):
    """Lowercase the words in text and join them with hyphens."""
    words = text.split()
    return words[0].lower() + "".join("-" + w.lower() for w in words[1:])
