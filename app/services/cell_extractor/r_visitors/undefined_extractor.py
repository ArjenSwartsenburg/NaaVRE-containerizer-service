from app.services.cell_extractor.parseR.RParser import RParser
from app.services.cell_extractor.r_visitors.r_visitor import RVisitor

DEFAULT_BUILT_IN_FUNCTION_NAMES = [
    'T', 'F', 'TRUE', 'FALSE',
    'NULL', 'NA', 'NaN', 'Inf',
    'pi',
    'sum', 'mean', 'min', 'max', 'median', 'sd', 'var',
    'length', 'nrow', 'ncol',
    'round', 'abs',
    'is.numeric', 'is.character', 'is.logical',
    'as.numeric', 'as.character', 'as.logical',
    'c', 'list', 'data.frame',
    'mu'
]


class UndefinedExtractor(RVisitor):
    def __init__(self, defined_notebook_vars=None, scoped=None,
                 built_in_function_names=None, cell_vars=None):
        self.undefined = set()
        self.defined_notebook_vars = defined_notebook_vars
        self.scoped = scoped
        self.built_in = set(DEFAULT_BUILT_IN_FUNCTION_NAMES)
        if built_in_function_names:
            self.built_in.update(built_in_function_names)
        updated_function_names = set()
        for cell_var in cell_vars:
            for function_name in self.built_in:
                if cell_var != function_name:
                    updated_function_names.add(function_name)
        self.built_in = updated_function_names

    def visitProg(self, ctx: RParser.ProgContext):
        self.visitChildren(ctx)
        return self.undefined

    def visitAssign(self, ctx: RParser.AssignContext):
        self.visitChildren(ctx)

    def visitCall(self, ctx: RParser.CallContext):
        # If expr startswith 'function', all sub variables are scoped.
        if ctx.expr().getText().startswith('function'):
            for sub in ctx.sublist().sub():
                self.scoped.add(sub.getText())
        else:
            if isinstance(ctx.expr(), RParser.UseropContext):
                self.visit(ctx.expr())
            self.visit(ctx.sublist())

    # TEST
    def visitUserop(self, ctx: RParser.UseropContext):
        if isinstance(ctx.expr(0), RParser.CallContext):
            self.visit(ctx.expr(0))
        if isinstance(ctx.expr(1), RParser.CallContext):
            self.visit(ctx.expr(1))

    def visitSublist(self, ctx: RParser.SublistContext):
        self.visitChildren(ctx)

    def visitExtract(self, ctx: RParser.ExtractContext):
        self.visit(ctx.expr(0))

    def visitSub(self, ctx: RParser.SubContext):
        if ctx.expr():
            self.visit(ctx.expr())
        return None

    def visitFunction(self, ctx: RParser.FunctionContext):
        form_list_ctx = ctx.formlist()
        if form_list_ctx is not None:
            self.visit(form_list_ctx)
        self.visit(ctx.expr())

    def visitFormlist(self, ctx: RParser.FormlistContext):
        self.visitChildren(ctx)

    def visitForm(self, ctx: RParser.FormContext):
        ctx_id = ctx.ID()
        if ctx_id:
            self.scoped.add(ctx_id.getText())
        elif self.isEllipsis(ctx):
            self.scoped.add("...")
            return None
        if ctx.expr():
            return self.visit(ctx.expr())
        return None

    def visitFor(self, ctx: RParser.ForContext):
        # Iterator variable is scoped
        self.scoped.add(ctx.ID().getText())
        self.visit(ctx.expr(0))
        self.visit(ctx.expr(1))
        self.scoped.remove(ctx.ID().getText())

    def visitId(self, ctx: RParser.IdContext):
        node_id = ctx.ID().getText()
        if (node_id not in self.defined_notebook_vars
                and node_id not in self.scoped and node_id
                not in self.built_in):
            self.undefined.add(ctx.getText())

    def isEllipsis(self, ctx):
        return ctx.getText() == "..."
