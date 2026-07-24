import os
import tempfile
from typing import Literal

import rpy2.robjects as robjects
import rpy2.robjects.packages as rpackages
from rpy2.robjects.packages import importr

from .extractor import Extractor
from .parseR.parsing import parse_text
from .r_visitors.config_extractor import ConfigExtractor
from .r_visitors.defined_extractor import DefinedExtractor
from .r_visitors.names_extractor import NamesExtractor
from .r_visitors.prefixed_var_extractor import PrefixedVarExtractor
from .r_visitors.undefined_extractor import UndefinedExtractor
from ...models.notebook_data import NotebookData

# Create an R environment
r_env = robjects.globalenv

# This R code is used to obtain all assignment variables
# (source https://adv-r.hadley.nz/expressions.html)
r_env["result"] = robjects.r("""
library(rlang)
library(lobstr)
library(purrr)

expr_type <- function(x) {
  if (rlang::is_syntactic_literal(x)) {
    "constant"
  } else if (is.symbol(x)) {
    "symbol"
  } else if (is.call(x)) {
    "call"
  } else if (is.pairlist(x)) {
    "pairlist"
  } else {
    typeof(x)
  }
}

switch_expr <- function(x, ...) {
  switch(expr_type(x),
    ...,
    stop("Don't know how to handle type ", typeof(x), call. = FALSE)
  )
}

recurse_call <- function(x) {
  switch_expr(x,
    # Base cases
    symbol = ,
    constant = ,

    # Recursive cases
    call = ,
    pairlist =
  )
}

logical_abbr_rec <- function(x) {
  switch_expr(x,
    constant = FALSE,
    symbol = as_string(x) %in% c("F", "T")
  )
}

logical_abbr <- function(x) {
  logical_abbr_rec(enexpr(x))
}

find_assign_rec <- function(x) {
  switch_expr(x,
    constant = ,
    symbol = character()
  )
}
find_assign <- function(x) unique(find_assign_rec(enexpr(x)))

flat_map_chr <- function(.x, .f, ...) {
  purrr::flatten_chr(purrr::map(.x, .f, ...))
}

find_assign_rec <- function(x) {
  switch_expr(x,
    # Base cases
    constant = ,
    symbol = character(),

    # Recursive cases
    pairlist = flat_map_chr(as.list(x), find_assign_rec),
    call = {
      # TODO: also added is_call(x, "=") here
      if (is_call(x, "<-") || is_call(x, "=")) {
        if (typeof(x[[2]]) == "symbol"){ # TODO: added the type check here
            as_string(x[[2]])
        }
      } else {
        flat_map_chr(as.list(x), find_assign_rec)
      }
    }
  )
}
""")

# Load the base R package for parsing and evaluation
base = importr('base')


class RExtractor(Extractor):
    imports: dict
    notebook_configurations: dict
    notebook_params: list[dict]
    notebook_secrets: list[dict]
    undefined: dict

    def __init__(self, notebook_data: NotebookData, base_image_tags_url: str,
                 built_in_function_names: list[str]):
        notebook = notebook_data.notebook
        sources = [nbcell.source for nbcell in notebook.cells if
                   nbcell.cell_type == 'code' and len(nbcell.source) > 0]
        self.cell_vars = self.__extract_cell_names(
            '\n'.join(sources)
        )
        self.built_in_function_names = built_in_function_names
        self.imports = self.__extract_imports(sources)
        self.notebook_configurations = (
            self.__extract_configurations(sources))
        self.notebook_params = self.__extract_prefixed_var(sources,
                                                           'param')
        self.notebook_secrets = self.__extract_prefixed_var(sources,
                                                            'secret')
        self.undefined = dict()
        for source in sources:
            try:
                self.undefined.update(self.__extract_cell_undefined(source))
            except Exception as e:
                print(f"Error extracting undefined variables: {e}")
        super().__init__(notebook_data, base_image_tags_url)

    def __extract_imports(self, sources: list[str]) -> dict:
        imports = {}
        for s in sources:
            ''' Approach 1: Simple regex.
                this matches the following cases: require(pkg), library(pkg),
                library("pkg"), library(package=pkg), library(package="pkg")
            '''
            # packages = re.findall(r'(?:library|require)\((?:package=)?(?:")?
            # (\w+)(?:")?\)', s)

            ''' Approach 2: Static analysis using 'renv' package.
                this approach is more safe as it covers more cases and checks
                comments
            '''
            with tempfile.NamedTemporaryFile(delete=False,
                                             suffix='.R') as tmp_file:
                tmp_file.write(s.encode())
                tmp_file.flush()
                renv = rpackages.importr('renv')
                function_list = renv.dependencies(tmp_file.name)

                # transpose renv dependencies to readable dependencies
                transposed_list = list(map(list, zip(*function_list)))
                packages = [row[1] for row in transposed_list]
                tmp_file.close()
                os.remove(tmp_file.name)

            # format the packages
            for package in packages:
                imports[package] = {
                    # asname and module are specific to Python packages.
                    # So you can probably leave them out here
                    'name': package,
                    'asname': '',
                    'module': ''
                }

            '''Approach 3: AST parsing'''
            # tree = parse_text(s)
            # visitor = ExtractImports()
            # output = visitor.visit(tree)

            # for o in output:
            #     imports[o] = {
            #         'name': o,
            #         'asname': '',
            #         'module': ''
            #     }

        return imports

    def __extract_configurations(self, sources):
        configurations = {}
        for s in sources:
            tree = parse_text(s)
            visitor = ConfigExtractor()
            output = visitor.visit(tree)
            for o in output:
                configurations[o] = output[o]

        return self.__resolve_configurations(configurations)

    # check source https://adv-r.hadley.nz/expressions.html)
    def __extract_prefixed_var(self, sources,
                               prefix:
                               Literal['input', 'output', 'param', 'secret']) \
            -> dict:
        extracted_vars = dict()
        for s in sources:
            tree = parse_text(s)
            visitor = PrefixedVarExtractor(prefix)
            variable_names = visitor.visit(tree)
            for variable_name in variable_names:
                if variable_name.startswith(prefix + '_'):
                    variable_type = None
                    variable_value = None
                    if variable_name in self.cell_vars:
                        variable_type = (
                            self.cell_vars)[variable_name]['type']
                    if prefix != 'secret':
                        variable_value = variable_names[variable_name]['value']
                    extracted_var = {
                        'name': variable_name,
                        'type': variable_type,
                        'value': variable_value
                    }
                    if variable_name in extracted_vars:
                        extracted_var = (
                            self.__get_best_variable(
                                extracted_vars[variable_name], extracted_var))

                    extracted_vars[variable_name] = extracted_var
        return extracted_vars

    def get_cell_outputs(self) -> list[dict]:
        cell_names = self.__extract_cell_names(self.cell_source)
        cell_undef = self.__extract_cell_undefined(self.cell_source)
        cell_outputs = []
        for var_name, properties in cell_names.items():
            if (var_name not in cell_undef and
                    var_name not in self.imports and
                    var_name in self.undefined and
                    self.not_reserved(var_name)):
                cell_outputs.append(properties)
        return cell_outputs

    def get_cell_inputs(self) -> list[dict]:
        cell_undefined = self.__extract_cell_undefined(self.cell_source)
        cell_inputs = []
        for var_name, properties in cell_undefined.items():
            if var_name not in self.imports and self.not_reserved(var_name):
                cell_inputs.append(properties)
        return cell_inputs

    def get_cell_dependencies(self, confs) -> list[dict]:
        # we probably like to only use dependencies that are necessary to
        # execute the cell however this is challenging in R as functions are
        # non-scoped
        dependencies = []
        for name in self.imports:
            dependencies.append(self.imports.get(name))
        return dependencies

    def __extract_cell_names(self, source) -> dict:
        tree = parse_text(source)
        visitor = NamesExtractor()
        vars_r = visitor.visit(tree)
        return vars_r

    def __extract_cell_undefined(self, cell_source) -> dict:
        tree = parse_text(cell_source)
        visitor = DefinedExtractor()
        function_names = self.built_in_function_names
        defined_cell_vars, scoped = visitor.visit(tree)
        visitor = UndefinedExtractor(
            defined_notebook_vars=defined_cell_vars,
            scoped=scoped,
            built_in_function_names=function_names,
            cell_vars=self.cell_vars
        )
        undefined_notebook_vars = visitor.visit(tree)
        # Build dictionary of undefined variables with their types
        undefined_cell_vars = {}
        for name in undefined_notebook_vars:
            var_type = None
            if name in self.cell_vars:
                var_type = self.cell_vars[name]['type']

            undefined_cell_vars[name] = {
                'name': name,
                'type': var_type,
            }

        return undefined_cell_vars

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

    def __resolve_configurations(self, configurations) -> dict:
        resolved_configurations = {}
        max_depth = 50
        for k, assignment in configurations.items():
            assignment_symbol = '='
            if '<-' in assignment:
                assignment_symbol = '<-'
            while 'conf_' in assignment.split(assignment_symbol)[1]:
                max_depth -= 1
                for conf_name, replacing_assignment in configurations.items():
                    if conf_name in assignment.split('=')[1]:
                        assignment = assignment.replace(
                            conf_name,
                            replacing_assignment.split('=')[1],
                        )
                resolved_configurations[k] = assignment
                if max_depth <= 0:
                    raise RuntimeError('maximum depth exceeded while '
                                       'resolving configuration')
        configurations.update(resolved_configurations)
        return configurations

    def __get_best_variable(self, existing_variable: dict,
                            candidate_variable: dict) -> dict:
        # If existing variable has type and value, keep it
        if (existing_variable['type'] is not None and
                existing_variable['value'] is not None):
            return existing_variable
        # If candidate variable has type and value, use it
        if (candidate_variable['type'] is not None and
                candidate_variable['value'] is not None):
            return candidate_variable
        # If existing variable has type, keep it
        if existing_variable['type'] is not None:
            return existing_variable
        # If candidate variable has type, use it
        if candidate_variable['type'] is not None:
            return candidate_variable
        # Otherwise, keep existing variable
        return existing_variable


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
