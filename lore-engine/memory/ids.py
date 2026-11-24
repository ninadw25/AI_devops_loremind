from ulid import ULID

def new_ulid() -> str:
    """Generate a new ULID string (sortable by creation time)."""
    return str(ULID())
