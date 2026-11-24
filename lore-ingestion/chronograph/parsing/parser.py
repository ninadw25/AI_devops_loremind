"""
Tree-sitter parser facade — parse source code into ASTs.
"""

import logging
from typing import Optional

import tree_sitter

from chronograph.parsing.languages import get_language, detect_language

log = logging.getLogger("chronograph.parser")

# Cache parsers per language
_parser_cache: dict[str, tree_sitter.Parser] = {}


def get_parser(lang_name: str) -> Optional[tree_sitter.Parser]:
    """Get or create a parser for the given language."""
    if lang_name in _parser_cache:
        return _parser_cache[lang_name]

    language = get_language(lang_name)
    if language is None:
        return None

    parser = tree_sitter.Parser(language)
    _parser_cache[lang_name] = parser
    return parser


def parse_content(content: bytes, language: str) -> Optional[tree_sitter.Tree]:
    """Parse source content and return the AST tree."""
    parser = get_parser(language)
    if parser is None:
        return None

    try:
        tree = parser.parse(content)
        return tree
    except Exception as e:
        log.warning("[parser] Failed to parse %s content: %s", language, e)
        return None


def parse_file_content(content: bytes, file_path: str) -> Optional[tuple[tree_sitter.Tree, str]]:
    """Parse content, auto-detecting language from path. Returns (tree, language)."""
    lang = detect_language(file_path)
    if lang is None:
        return None

    tree = parse_content(content, lang)
    if tree is None:
        return None

    return tree, lang
