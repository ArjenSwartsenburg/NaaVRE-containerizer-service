import ast


class Visitor(ast.NodeVisitor):
    def __init__(self):
        self.imports = {}
        self.variable_info = {}
        self.variables = []
        self.function_info = set()

    def visit_Assign(self, node):
        # Handle basic assignments
        if isinstance(node.targets[0], ast.Name):
            var_name = node.targets[0].id
            var_value = self.get_value(
                node.value)  # Get the actual assigned value
            var_type = self.get_type(
                node.value)  # Get the type of the assigned value

            # Create a formatted assignment string (e.g., "x = 5")
            assignation_str = f"{var_name} = {var_value}"

            # Store the assignment information
            assignment_info = {
                'name': var_name,
                'value': var_value,
                'type': var_type,
                'assignation': assignation_str
            }
            self.variables.append(assignment_info)

        # Continue traversal to other nodes
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        # Determine variable name
        var_name = None
        if isinstance(node.target, ast.Name):
            var_name = node.target.id
        elif isinstance(node.target, ast.Attribute):
            var_name = self.get_full_name(node.target)

        var_value = self.get_value(node.value)
        var_type = self.get_type(node.value)

        assignation_str = (f"{var_name} {self.get_operator(node.op)} = "
                           f"{var_value}")

        assignment_info = {
            'name': var_name,
            'value': var_value,
            'type': var_type,
            'assignation': assignation_str
        }
        self.variables.append(assignment_info)

        self.generic_visit(node)

    def get_value(self, value_node):
        """ Get the assigned value as a readable string. """
        if isinstance(value_node, ast.Constant):
            return repr(value_node.value)

        elif isinstance(value_node, ast.List):
            return "[" + ", ".join(
                self.get_value(el) for el in value_node.elts) + "]"
        elif isinstance(value_node, ast.Dict):
            keys = [self.get_value(k) for k in value_node.keys]
            values = [self.get_value(v) for v in value_node.values]
            return "{" + ", ".join(
                f"{k}: {v}" for k, v in zip(keys, values)) + "}"
        elif isinstance(value_node, ast.BinOp):
            left = self.get_value(value_node.left)
            op = self.get_operator(value_node.op)
            right = self.get_value(value_node.right)
            return f"({left} {op} {right})"
        elif isinstance(value_node, ast.Name):
            return value_node.id
        elif isinstance(value_node, ast.Attribute):
            return self.get_full_name(value_node)
        else:
            return "unknown"

    def get_type(self, value_node):
        """ Determine the type of the assigned value. """
        if isinstance(value_node, ast.Constant):
            return type(value_node.value).__name__
        elif isinstance(value_node, ast.List):
            return "list"
        elif isinstance(value_node, ast.Dict):
            return "dict"
        elif isinstance(value_node, ast.Tuple):
            return "tuple"
        elif isinstance(value_node, ast.BinOp):
            return "expression"
        elif isinstance(value_node, ast.Call):
            return "function_call"
        elif isinstance(value_node, ast.Name):
            return "variable_reference"
        else:
            return "unknown"

    def get_operator(self, op):
        """ Return the string representation of an operator. """
        operators = {
            ast.Add: "+",
            ast.Sub: "-",
            ast.Mult: "*",
            ast.Div: "/",
            ast.Mod: "%",
            ast.Pow: "**",
            ast.FloorDiv: "//",
        }
        return operators.get(type(op), "?")

    # Get all the variable reads
    def visit_Name(self, node):
        var_name = node.id
        if (var_name not in self.variable_info and
                var_name not in self.function_info):
            self.variable_info[var_name] = {
                'type': 'unknown',
                'value': None
            }
            self.variables.append({'name': var_name,
                                   'type': 'unknown',
                                   'value': None})
        self.generic_visit(node)

    # Get all the function calls
    def visit_Call(self, node):
        self.function_info.add(self.get_full_name(node.func))
        self.generic_visit(node)

    def visit_Import(self, node):
        # For regular imports (e.g., import os)
        for alias in node.names:
            code_import = {alias.name: {'module': '', 'asname': alias.asname,
                                        'name': alias.name}}
            self.imports.update(code_import)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # For from-imports (e.g., from os import path)
        if node.level:
            # Relative import (from . import x, from ..pkg import y): refers
            # to a module local to the notebook's repository, never an
            # installable package.
            self.generic_visit(node)
            return
        for alias in node.names:
            code_import = {
                alias.name: {'module': node.module, 'asname': alias.asname,
                             'name': alias.name}}
            self.imports.update(code_import)
        self.generic_visit(node)

    def get_full_name(self, node):
        # Recursively get the full name for nodes of type `ast.Attribute`
        if isinstance(node, ast.Name):  # Base case for the recursion
            return node.id
        elif isinstance(node, ast.Attribute):  # Recursive case
            return f"{self.get_full_name(node.value)}.{node.attr}"
        return None
