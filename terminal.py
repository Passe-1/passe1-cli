"""Texto seguro para exibição no terminal, sem modificar dados originais."""
import unicodedata


def seguro(texto):
    return "".join(c.encode("unicode_escape").decode("ascii") if (unicodedata.category(c) in {"Cc", "Cf"} and c != "\n") else c for c in str(texto))
