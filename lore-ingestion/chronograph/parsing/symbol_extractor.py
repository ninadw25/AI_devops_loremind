"""
Symbol extractor — walk a Tree-sitter AST and extract functions, classes, imports.

Supports multiple languages with language-specific node type mappings.
Computes complexity metrics (cyclomatic, cognitive, nesting depth).
"""

import hashlib
import logging
from typing import Optional

import tree_sitter

from chronograph.entities import Symbol, ComplexityMetrics
from chronograph.parsing.languages import get_symbol_query, detect_language

log = logging.getLogger("chronograph.symbol_extractor")

# Branching nodes that increase cyclomatic complexity
# Only decision points: if/elif, loops, exception handlers, switch/match cases, ternary
# Note: else_clause is NOT a decision point (no condition). Boolean operators (and/or)
# are handled separately below since we need to check the operator text.
BRANCH_NODES = {
    "if_statement", "elif_clause",
    "for_statement", "for_in_statement", "while_statement",
    "except_clause", "catch_clause",
    "case_clause", "switch_statement", "match_statement",
    "conditional_expression", "ternary_expression",
}

# Boolean operators add to cyclomatic complexity (each is a separate path)
BOOLEAN_OPERATOR_NODES = {"boolean_operator", "binary_expression"}

# Nodes that increase cognitive complexity (nesting-sensitive)
COGNITIVE_NODES = {
    "if_statement", "for_statement", "for_in_statement",
    "while_statement", "try_statement", "catch_clause",
    "switch_statement", "match_statement",
    "conditional_expression", "ternary_expression",
}

NESTING_NODES = {
    "if_statement", "for_statement", "for_in_statement",
    "while_statement", "try_statement", "function_definition",
    "class_definition", "method_definition",
}


def extract_symbols(
    tree: tree_sitter.Tree,
    source: bytes,
    file_path: str,
    language: Optional[str] = None,
) -> list[Symbol]:
    """Extract all symbols (functions, classes, methods, imports) from an AST."""
    if language is None:
        language = detect_language(file_path)
    if language is None:
        return []

    query_config = get_symbol_query(language)
    if query_config is None:
        return []

    symbols = []
    root = tree.root_node

    # Extract top-level and nested symbols
    _walk_node(root, source, file_path, language, query_config, symbols, context_name="")

    return symbols


def _walk_node(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    language: str,
    query_config: dict,
    symbols: list[Symbol],
    context_name: str,
):
    """Recursively walk AST nodes, extracting symbols."""
    node_type = node.type

    # Check if this node is a function/method
    if node_type in query_config.get("function", []):
        sym = _extract_function(node, source, file_path, language, query_config, context_name)
        if sym:
            symbols.append(sym)
            # Look for nested definitions inside this function
            body = node.child_by_field_name(query_config.get("body_field", "body"))
            if body:
                new_context = sym.qualified_name
                for child in body.children:
                    _walk_node(child, source, file_path, language, query_config, symbols, new_context)
        return

    # Check if this node is a class
    if node_type in query_config.get("class", []):
        sym = _extract_class(node, source, file_path, language, query_config, context_name)
        if sym:
            symbols.append(sym)
            # Extract methods inside the class
            body = node.child_by_field_name(query_config.get("body_field", "body"))
            if body:
                new_context = sym.qualified_name
                for child in body.children:
                    _walk_node(child, source, file_path, language, query_config, symbols, new_context)
        return

    # Check if this is an import
    if node_type in query_config.get("import", []):
        sym = _extract_import(node, source, file_path, language)
        if sym:
            symbols.append(sym)
        return

    # Not a recognized symbol node — recurse into children to find symbols deeper in the tree
    for child in node.children:
        _walk_node(child, source, file_path, language, query_config, symbols, context_name)


def _extract_function(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    language: str,
    query_config: dict,
    context_name: str,
) -> Optional[Symbol]:
    """Extract a function/method symbol from an AST node."""
    name_node = node.child_by_field_name(query_config.get("name_field", "name"))
    if name_node is None:
        return None

    # C/C++: function_definition -> declarator (function_declarator) -> declarator (identifier)
    # Unwrap nested declarators to get the actual name
    while name_node.type in ("function_declarator", "pointer_declarator"):
        inner = name_node.child_by_field_name("declarator")
        if inner:
            name_node = inner
        else:
            break

    name = _node_text(name_node, source)
    if not name:
        return None

    qualified_name = f"{context_name}.{name}" if context_name else name
    kind = "method" if context_name else "function"

    # Extract signature (first line or params)
    params_node = node.child_by_field_name(query_config.get("params_field", "parameters"))
    params_text = _node_text(params_node, source) if params_node else "()"
    signature = f"{name}{params_text}"

    # Extract body text
    body_node = node.child_by_field_name(query_config.get("body_field", "body"))
    body_text = _node_text(body_node, source) if body_node else ""

    # Extract decorators
    decorators = _extract_decorators(node, source, query_config)

    # Extract param names
    params = _extract_param_names(params_node, source) if params_node else []

    # Extract function calls within body
    calls = _extract_calls(body_node, source) if body_node else []

    # Compute complexity
    complexity = _compute_complexity(body_node) if body_node else ComplexityMetrics()
    complexity.lines = node.end_point[0] - node.start_point[0] + 1
    complexity.params = len(params)

    body_hash = hashlib.sha256(body_text.encode()).hexdigest() if body_text else ""

    return Symbol(
        name=name,
        kind=kind,
        qualified_name=qualified_name,
        file_path=file_path,
        language=language,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        signature=signature,
        body_text=body_text,
        body_hash=body_hash,
        decorators=decorators,
        params=params,
        calls=calls,
        complexity=complexity,
    )


def _extract_class(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    language: str,
    query_config: dict,
    context_name: str,
) -> Optional[Symbol]:
    """Extract a class symbol from an AST node."""
    name_node = node.child_by_field_name(query_config.get("name_field", "name"))
    # Fallbacks for languages where class name field differs from function name field:
    # - Rust impl_item uses "type" field
    # - C/C++ class_specifier uses "name" even though functions use "declarator"
    if name_node is None:
        for fallback in ("name", "type"):
            name_node = node.child_by_field_name(fallback)
            if name_node:
                break
    if name_node is None:
        return None

    name = _node_text(name_node, source)
    if not name:
        return None

    qualified_name = f"{context_name}.{name}" if context_name else name
    body_node = node.child_by_field_name(query_config.get("body_field", "body"))
    body_text = _node_text(body_node, source) if body_node else ""
    body_hash = hashlib.sha256(body_text.encode()).hexdigest() if body_text else ""

    # Class signature: name + bases/interfaces
    signature = _node_text(node, source).split("\n")[0].rstrip(":")
    decorators = _extract_decorators(node, source, query_config)

    return Symbol(
        name=name,
        kind="class",
        qualified_name=qualified_name,
        file_path=file_path,
        language=language,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        signature=signature,
        body_text=body_text,
        body_hash=body_hash,
        decorators=decorators,
    )


def _extract_import(
    node: tree_sitter.Node,
    source: bytes,
    file_path: str,
    language: str,
) -> Optional[Symbol]:
    """Extract an import statement as a symbol."""
    text = _node_text(node, source)
    if not text:
        return None

    return Symbol(
        name=text.strip(),
        kind="module",
        qualified_name=text.strip(),
        file_path=file_path,
        language=language,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        imports=[text.strip()],
    )


def _extract_decorators(node: tree_sitter.Node, source: bytes, query_config: dict) -> list[str]:
    """Extract decorator names from a node."""
    dec_type = query_config.get("decorator_node")
    if not dec_type:
        return []

    decorators = []
    # Look at siblings before this node (decorators precede the definition)
    parent = node.parent
    if parent is None:
        return []

    found_node = False
    for child in parent.children:
        if child.id == node.id:
            found_node = True
            break
        if child.type == dec_type:
            decorators.append(_node_text(child, source))

    return decorators


def _extract_param_names(params_node: tree_sitter.Node, source: bytes) -> list[str]:
    """Extract parameter names from a parameter list node."""
    params = []
    for child in params_node.children:
        if child.type in ("identifier", "typed_parameter", "typed_default_parameter",
                          "default_parameter", "parameter_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node:
                params.append(_node_text(name_node, source))
            elif child.type == "identifier":
                text = _node_text(child, source)
                if text not in ("self", "cls", "(", ")", ","):
                    params.append(text)
    return params


def _extract_calls(node: tree_sitter.Node, source: bytes) -> list[str]:
    """Extract function call names from a body node."""
    calls = set()
    _find_calls(node, source, calls)
    return list(calls)


def _find_calls(node: tree_sitter.Node, source: bytes, calls: set):
    """Recursively find call expressions across languages."""
    # Python uses "call", JS/TS/Go/Java/Rust use "call_expression"
    if node.type in ("call", "call_expression"):
        func_node = node.child_by_field_name("function")
        if func_node:
            calls.add(_node_text(func_node, source))
    for child in node.children:
        _find_calls(child, source, calls)


def _compute_complexity(node: tree_sitter.Node) -> ComplexityMetrics:
    """Compute cyclomatic and cognitive complexity from AST."""
    cyclomatic = 1  # Base complexity
    cognitive = 0
    max_nesting = 0

    def _walk(n: tree_sitter.Node, nesting: int):
        nonlocal cyclomatic, cognitive, max_nesting

        if n.type in BRANCH_NODES:
            cyclomatic += 1

        # Boolean operators (and/or/&&/||) — only count the operator itself
        if n.type in BOOLEAN_OPERATOR_NODES:
            op_text = ""
            for child in n.children:
                if child.type in ("and", "or", "&&", "||"):
                    op_text = child.type
                    break
            if op_text in ("and", "or", "&&", "||"):
                cyclomatic += 1
                cognitive += 1

        if n.type in COGNITIVE_NODES:
            cognitive += 1 + nesting  # Nesting penalty

        current_nesting = nesting
        if n.type in NESTING_NODES:
            current_nesting = nesting + 1
            max_nesting = max(max_nesting, current_nesting)

        for child in n.children:
            _walk(child, current_nesting)

    _walk(node, 0)

    return ComplexityMetrics(
        cyclomatic=cyclomatic,
        cognitive=cognitive,
        max_nesting=max_nesting,
    )


def _node_text(node: tree_sitter.Node, source: bytes) -> str:
    """Extract the text content of a node."""
    try:
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return ""
