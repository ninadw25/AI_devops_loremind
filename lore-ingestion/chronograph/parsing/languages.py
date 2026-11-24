"""
Language configuration — maps file extensions to Tree-sitter languages and AST node types.
"""

import logging
from typing import Optional

log = logging.getLogger("chronograph.languages")

# Extension -> (tree-sitter module name, language name)
EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

# Tree-sitter node types for symbol extraction per language
SYMBOL_QUERIES: dict[str, dict] = {
    "python": {
        "function": ["function_definition"],
        "class": ["class_definition"],
        "method": ["function_definition"],  # methods are functions inside classes
        "import": ["import_statement", "import_from_statement"],
        "name_field": "name",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": "decorator",
    },
    "javascript": {
        "function": ["function_declaration", "arrow_function", "function", "method_definition"],
        "class": ["class_declaration"],
        "import": ["import_statement"],
        "name_field": "name",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": "decorator",
    },
    "typescript": {
        "function": ["function_declaration", "arrow_function", "method_definition"],
        "class": ["class_declaration", "interface_declaration"],
        "import": ["import_statement"],
        "name_field": "name",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": "decorator",
    },
    "go": {
        "function": ["function_declaration", "method_declaration"],
        "class": ["type_declaration"],  # structs/interfaces as classes
        "import": ["import_declaration"],
        "name_field": "name",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": None,
    },
    "rust": {
        "function": ["function_item"],
        "class": ["struct_item", "impl_item", "enum_item", "trait_item"],
        "import": ["use_declaration"],
        "name_field": "name",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": "attribute_item",
    },
    "java": {
        "function": ["method_declaration", "constructor_declaration"],
        "class": ["class_declaration", "interface_declaration", "enum_declaration"],
        "import": ["import_declaration"],
        "name_field": "name",
        "params_field": "formal_parameters",
        "body_field": "body",
        "decorator_node": "marker_annotation",
    },
    "c": {
        "function": ["function_definition"],
        "class": ["struct_specifier", "enum_specifier"],
        "import": ["preproc_include"],
        "name_field": "declarator",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": None,
    },
    "cpp": {
        "function": ["function_definition"],
        "class": ["class_specifier", "struct_specifier"],
        "import": ["preproc_include", "using_declaration"],
        "name_field": "declarator",
        "params_field": "parameters",
        "body_field": "body",
        "decorator_node": None,
    },
}

# Cache loaded languages
_language_cache: dict[str, object] = {}


def get_language(lang_name: str):
    """Load and cache a tree-sitter Language object."""
    if lang_name in _language_cache:
        return _language_cache[lang_name]

    try:
        import tree_sitter

        if lang_name == "python":
            import tree_sitter_python as tsp
            ptr = tsp.language()
        elif lang_name == "javascript":
            import tree_sitter_javascript as tsjs
            ptr = tsjs.language()
        elif lang_name == "typescript":
            import tree_sitter_typescript as tsts
            ptr = tsts.language_typescript()
        elif lang_name == "go":
            import tree_sitter_go as tsgo
            ptr = tsgo.language()
        elif lang_name == "rust":
            import tree_sitter_rust as tsrs
            ptr = tsrs.language()
        elif lang_name == "java":
            import tree_sitter_java as tsjava
            ptr = tsjava.language()
        elif lang_name == "c":
            import tree_sitter_c as tsc
            ptr = tsc.language()
        elif lang_name == "cpp":
            import tree_sitter_cpp as tscpp
            ptr = tscpp.language()
        else:
            log.warning("[languages] Unsupported language: %s", lang_name)
            return None

        # Wrap PyCapsule in tree_sitter.Language
        language = tree_sitter.Language(ptr)
        _language_cache[lang_name] = language
        return language
    except ImportError:
        log.warning("[languages] tree-sitter-%s not installed", lang_name)
        return None


def detect_language(file_path: str) -> Optional[str]:
    """Detect language from file extension."""
    for ext, lang in EXTENSION_MAP.items():
        if file_path.endswith(ext):
            return lang
    return None


def get_symbol_query(language: str) -> Optional[dict]:
    """Get the symbol extraction query config for a language."""
    return SYMBOL_QUERIES.get(language)
