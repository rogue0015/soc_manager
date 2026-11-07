"""Domain errors. The API layer maps these to HTTP status codes in one place (main.py)."""


class NotFound(Exception):
    pass


class Conflict(Exception):
    """The request is valid but clashes with the current state (e.g. editing a published post)."""


class InvalidInput(Exception):
    pass
