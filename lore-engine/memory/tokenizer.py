def count_tokens(text: str) -> int:
    """Approximate token count: 1 token ≈ 4 chars."""
    return max(1, len(text) // 4)
