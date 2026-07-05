import ast
import logging
import re
from functools import lru_cache
from typing import Literal

from pyflakes import reporter as pyflakes_reporter, api as pyflakes_api
from pytype import config as pytype_config
from pytype.tools.annotate_ast import annotate_ast

from .extractor import Extractor
from .py_conf_assignment_transformer import PyConfAssignmentTransformer
from .py_visitors.visitor import Visitor
from ..repositories.github_service import logger
from ...models.notebook_data import NotebookData


def scrub_ipython_magics(source: str) -> str:
    """Neutralise IPython line/cell magics and shell escapes so the source is
    parseable as plain Python.

    Notebook cells routinely contain ``%matplotlib inline``, ``%timeit ...``,
    ``!pip install`` and ``foo?`` help syntax, none of which is valid Python.
    ``ast.parse`` (and pytype) raise ``SyntaxError`` on them, which previously
    lost the whole notebook's imports. Offending lines are blanked (line count
    preserved so error line numbers stay meaningful); a cell magic (``%%...``)
    blanks the remainder of the cell, whose body is not necessarily Python.
    """
    out = []
    in_cell_magic = False
    for line in source.splitlines():
        stripped = line.lstrip()
        if in_cell_magic:
            out.append('')
            continue
        if stripped.startswith('%%'):
            in_cell_magic = True
            out.append('')
        elif stripped.startswith(('%', '!')):
            out.append('')
        elif stripped.rstrip().endswith('?') and not stripped.startswith('#'):
            out.append('')
        else:
            out.append(line)
    return '\n'.join(out)


def extract_notebook_imports(notebook) -> list[dict]:
    """Extract the complete set of top-level imports from a notebook.

    Parses each code cell independently so one unparseable cell (a malformed
    magic, non-Python content) only costs that cell, never the whole
    notebook's import list. Uses a plain AST walk — imports are pure syntax
    and need no type inference — and returns dependency records in the shape
    the containerizer expects (``{'name', 'asname', 'module'}``).
    """
    imports: dict = {}
    for cell in notebook.cells:
        if cell.cell_type != 'code':
            continue
        source = cell.source
        if isinstance(source, list):
            source = '\n'.join(source)
        source = scrub_ipython_magics(source)
        try:
            visitor = Visitor()
            visitor.visit(ast.parse(source))
            imports.update(visitor.imports)
        except SyntaxError as e:
            logger.warning('skipping unparseable cell during import '
                           'extraction: %s', e)
    return list(imports.values())


class PyExtractor(Extractor):
    # notebook_sources: list
    notebook_variables: dict
    notebook_imports: dict
    notebook_configurations: dict
    notebook_params: dict
    notebook_secrets: dict
    undefined: dict

    def __init__(self, notebook_data: NotebookData, base_image_tags_url: str):
        notebook = notebook_data.notebook
        # Strip IPython magics in place so both the notebook-wide extraction
        # below and the base class's per-cell parsing (super().__init__) see
        # valid Python. Idempotent, so re-running on the same notebook is safe.
        for nb_cell in notebook.cells:
            if nb_cell.cell_type == 'code':
                if isinstance(nb_cell.source, list):
                    nb_cell.source = '\n'.join(nb_cell.source)
                nb_cell.source = scrub_ipython_magics(nb_cell.source)
        notebook_sources = []
        for nb_cell in notebook.cells:
            if nb_cell.cell_type == 'code' and len(nb_cell.source) > 0 and \
                    nb_cell.source[0] != '!':
                notebook_sources.append(nb_cell.source)
        self.notebook_variables = self.__extract_variables(
            '\n'.join(notebook_sources),
            infer_types=True,
        )
        # Imports are pure syntax — a plain AST walk finds them all. Don't
        # route this through pytype: type inference is not needed here and
        # its failures must never cost us the notebook's import list.
        notebook_visitor = (
            self.__parse_code('\n'.join(notebook_sources),
                              infer_types=False,
                              ))
        self.notebook_imports = notebook_visitor.imports
        self.notebook_configurations = self.__extract_configurations(
            notebook_sources)
        self.notebook_params = self.__extract_prefixed_var(
            notebook_sources, 'param')
        self.notebook_secrets = self.__extract_prefixed_var(
            notebook_sources, 'secret')
        self.undefined = dict()
        for source in notebook_sources:
            self.undefined.update(self.__extract_cell_undefined(source))
        super().__init__(notebook_data, base_image_tags_url)

    def __parse_code(self, source_code, infer_types=False):
        if infer_types:
            tree = self.__get_annotated_ast(source_code)
        else:
            tree = ast.parse(source_code)
        visitor = Visitor()
        visitor.visit(tree)
        return visitor

    def __is_in_imports(self, var_name) -> bool:
        if var_name in self.notebook_imports:
            return True
        for v in self.notebook_imports.values():
            if v.get('asname') == var_name:
                return True
        return False

    def __extract_configurations(self, sources):
        configurations = {}
        for s in sources:
            lines = s.splitlines()
            tree = ast.parse(s)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign):
                    target = node.targets[0]
                    if hasattr(target, 'id'):
                        name = node.targets[0].id
                        prefix = name.split('_')[0]
                        if prefix == 'conf' and name not in configurations:
                            conf_line = ''
                            for line in lines[node.lineno - 1:node.end_lineno]:
                                conf_line += line.strip()
                            configurations[name] = conf_line
        return self.__resolve_configurations(configurations)

    def __extract_prefixed_var(
            self,
            sources,
            prefix: Literal['input', 'output', 'param', 'secret', 'conf']) \
            -> dict:
        extracted_vars = dict()
        for s in sources:
            lines = s.splitlines()
            tree = ast.parse(s)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and hasattr(node.targets[0],
                                                            'id'):
                    name = node.targets[0].id
                    node_prefix = name.split('_')[0]
                    if node_prefix == prefix:
                        var_line = ''
                        for line in lines[node.lineno - 1:node.end_lineno]:
                            var_line += line.strip()
                        var_value = ast.unparse(node.value)
                        try:
                            # remove quotes around strings
                            var_value = str(ast.literal_eval(var_value))
                            # Cast according to
                            # self.notebook_names[name]['type']
                            if self.notebook_variables[name]['type'] == 'int':
                                var_value = int(var_value)
                            elif (self.notebook_variables[name]['type']
                                  == 'float'):
                                var_value = float(var_value)
                            elif (self.notebook_variables[name]['type']
                                  == 'list'):
                                var_value = list(ast.literal_eval(var_value))
                            elif (self.notebook_variables[name]['type'] ==
                                  'str'):
                                var_value = str(var_value)
                        except ValueError:
                            # when var_value can't safely be parsed,
                            pass
                        extracted_vars[name] = {
                            'name': name,
                            'type': self.notebook_variables[name]['type'],
                            'value': var_value,
                        }
                        if prefix == 'secret':
                            del extracted_vars[name]['value']
        return extracted_vars

    def get_cell_outputs(self) -> list[dict]:
        cell_variables = self.__extract_variables(self.cell_source)
        cell_outputs = []
        for var_name, properties in cell_variables.items():
            if (var_name not in self.__extract_cell_undefined(self.cell_source)
                    and not self.__is_in_imports(var_name)
                    and var_name in self.undefined
                    and self.not_reserved(var_name)):
                cell_outputs.append({'name': var_name,
                                     'type': properties['type']})
        return cell_outputs

    def get_cell_inputs(self) -> list[dict]:
        cell_undefined = self.__extract_cell_undefined(self.cell_source)
        cell_inputs = []
        for var_name, properties in cell_undefined.items():
            if var_name in self.reserved_prefixes:
                continue
            if (not self.__is_in_imports(var_name) and
                    self.not_reserved(var_name)):
                cell_inputs.append({'name': var_name,
                                    'type': properties['type']})
        return cell_inputs

    def get_cell_dependencies(self, confs):
        dependencies = []
        names = self.__extract_variables(self.cell_source)
        for ck in confs:
            conf_vars = self.__extract_variables(ck['assignation'])
            names.update(conf_vars)
        for name in names:
            if name in self.notebook_imports:
                dependencies.append(self.notebook_imports.get(name))
            for v in self.notebook_imports.values():
                if v.get('asname') == name:
                    dependencies.append(v)
                    break
        return dependencies

    @staticmethod
    @lru_cache
    def __get_annotated_ast(cell_source):
        try:
            return annotate_ast.annotate_source(
                cell_source, ast, pytype_config.Options.create())
        except Exception as e:
            # pytype crashes on some valid real-world code (e.g. internal
            # "Passing binding from different program" errors). Type
            # inference is best-effort: fall back to a plain AST, which
            # keeps imports and undefined-variable extraction working and
            # only loses type annotations (handled by the AttributeError
            # guard in __extract_variables).
            logging.getLogger(__name__).warning(
                'pytype annotation failed, falling back to plain AST '
                'without type inference: %s', e)
            return ast.parse(cell_source)

    def __convert_type_annotation(self, type_annotation):
        """ Convert type annotation to the ones supported for cell interfaces

        :param type_annotation: type annotation obtained by e.g. pytype
        :return: converted type: 'int', 'float', 'str', 'list', or None
        """
        if type_annotation is None:
            return None

        patterns = {
            'int': [
                re.compile(r'^int$'),
            ],
            'float': [
                re.compile(r'^float$'),
            ],
            'str': [
                re.compile(r'^str$'),
            ],
            'list': [
                re.compile(r'^List\['),
            ],
            None: [
                re.compile(r'^Any$'),
                re.compile(r'^Dict'),
                re.compile(r'^Callable'),
            ]
        }
        for type_name, regs in patterns.items():
            for reg in regs:
                if reg.match(type_annotation):
                    return type_name

        logging.getLogger(__name__).debug(f'Unmatched type: {type_annotation}')
        return None

    def __extract_variables(self, cell_source, infer_types=False):
        names = dict()
        if infer_types:
            tree = self.__get_annotated_ast(cell_source)
        else:
            tree = ast.parse(cell_source)
        for module in ast.walk(tree):
            var_type = None
            if isinstance(module, (ast.Name,)):
                var_name = module.id
                if infer_types:
                    try:
                        var_type = self.__convert_type_annotation(
                            module.resolved_annotation)
                    except AttributeError as e:
                        logger.debug(
                            'Could not resolve type for var_name: %s. '
                            'Setting var_type to None. Error: %s',
                            var_name, e)
                else:
                    var_type = self.notebook_variables[var_name]['type']
                names[module.id] = {
                    'name': var_name,
                    'type': var_type,
                }
        return names

    def __extract_cell_undefined(self, source) -> dict:
        flakes_stdout = StreamList()
        flakes_stderr = StreamList()
        rep = pyflakes_reporter.Reporter(
            flakes_stdout.reset(),
            flakes_stderr.reset())
        pyflakes_api.check(source, filename="temp", reporter=rep)
        if rep._stderr():
            raise SyntaxError("Flakes reported the following error:"
                              "\n{}".format('\t' + '\t'.join(rep._stderr())))
        p = r"'(.+?)'"
        out = rep._stdout()
        undef_vars = dict()

        for line in filter(lambda a: a != '\n' and 'undefined name' in a, out):
            var_search = re.search(p, line)
            var_name = var_search.group(1)
            undef_vars[var_name] = {
                'name': var_name,
                'type': self.notebook_variables[var_name]['type'],
            }
        return undef_vars

    def get_cell_params(self) -> list[dict]:
        param = {}
        cell_params = []
        cell_unds = self.__extract_cell_undefined(self.cell_source)
        param_unds = [und for und in cell_unds if und in self.notebook_params]
        for u in param_unds:
            if u not in param:
                param[u] = self.notebook_params[u]
                cell_params.append({'name': u, 'type': param[u]['type'],
                                    'default_value': param[u]['value']})
        return cell_params

    def get_cell_secrets(self) -> list[dict]:
        secret = {}
        cell_secrets = []
        cell_unds = self.__extract_cell_undefined(self.cell_source)
        secret_unds = [und for und in cell_unds if
                       und in self.notebook_secrets]
        for u in secret_unds:
            if u not in secret:
                secret[u] = self.notebook_secrets[u]
                cell_secrets.append({'name': u, 'type': secret[u]['type']})
        return cell_secrets

    def get_cell_confs(self) -> list[dict]:
        conf = {}
        cell_confs = []
        cell_unds = self.__extract_cell_undefined(self.cell_source)
        conf_unds = [und for und in cell_unds if
                     und in self.notebook_configurations]
        for u in conf_unds:
            if u not in conf:
                conf[u] = self.notebook_configurations[u]
                cell_confs.append({'name': u, 'assignation': conf[u]})
        return cell_confs

    def __resolve_configurations(self, configurations):
        assignment_transformer = PyConfAssignmentTransformer(configurations)
        resolved_configurations = {
            k: ast.unparse(assignment_transformer.visit(ast.parse(v)))
            for k, v in configurations.items()
        }
        configurations.update(resolved_configurations)
        return configurations


class StreamList:

    def __init__(self):
        self.out = list()

    def write(self, text):
        self.out.append(text)

    def reset(self):
        self.out = list()
        return self

    def __call__(self):
        return self.out
