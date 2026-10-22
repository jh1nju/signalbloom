"""Collectors: small functions that measure the network and return plain values."""


class CollectorError(Exception):
    """A collector could not run at all. The message is safe to show the user."""
