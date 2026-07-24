from app.services.cell_extractor.parseR.RParser import RParser
from app.services.cell_extractor.r_visitors.r_visitor import RVisitor


class DefinedExtractor(RVisitor):
    def __init__(self):
        self.defined_vars = set()
        self.scoped = set()
        self.scope = False

    def visitProg(self, ctx: RParser.ProgContext):
        self.visitChildren(ctx)
        return self.defined_vars, self.scoped

    def visitAssign(self, ctx: RParser.AssignContext):
        # Get the identifier and the assigned value of the expr and add to dict
        id = self.visit(ctx.expr(0))
        if id is None:
            return None
        if id not in self.scoped and not self.scope:
            self.defined_vars.add(id)
        elif self.scope:
            self.scoped.add(id)
        self.visit(ctx.expr(1))

    def visitCall(self, ctx: RParser.CallContext):
        self.visit(ctx.expr())
        self.visit(ctx.sublist())

    def visitSublist(self, ctx: RParser.SublistContext):
        self.visitChildren(ctx)

    def visitSub(self, ctx: RParser.SubContext):
        # Deal with nested subs
        reset = True
        if self.scope:
            reset = False
        self.scope = True
        if isinstance(ctx.expr(), RParser.IdContext):
            self.visit(ctx.expr())
        elif isinstance(ctx.expr(), RParser.AssignContext):
            self.scoped.add(ctx.expr().expr(0).getText())
            self.visit(ctx.expr().expr(1))
        elif isinstance(ctx.expr(), RParser.CallContext):
            self.visit(ctx.expr())
        elif isinstance(ctx.expr(), RParser.FunctionContext):
            self.visit(ctx.expr())
        if reset:
            self.scope = False

    def visitId(self, ctx: RParser.IdContext):
        return ctx.getText()
