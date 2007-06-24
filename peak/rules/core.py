__all__ = [
    'Rule', 'RuleSet', 'predicate_signatures', 'rules_for',
    'Method', 'Around', 'Before', 'After', 'MethodList',
    'DispatchError', 'AmbiguousMethods', 'NoApplicableMethods',
    'abstract', 'when', 'before', 'after', 'around',
    'implies', 'dominant_signatures', 'combine_actions',
    'always_overrides', 'merge_by_default', 'Aspect', 'intersect', 'disjuncts'
]
from peak.util.decorators import decorate_assignment, decorate, struct
from peak.util.assembler import Code, Const, Call, Local
import inspect, new

try:
    set, frozenset
except NameError:
    from sets import Set as set
    from sets import ImmutableSet as frozenset
empty = frozenset()

struct()
def Rule(body, predicate=(), actiontype=None):
    return body, predicate, actiontype

struct()
def ActionDef(actiontype, body, signature, sequence):
    return actiontype, body, signature, sequence

def predicate_signatures(predicate):
    yield predicate # XXX

_core_rules = None      # the core rules aren't loaded yet

def parse_rule(ruleset, body, predicate, actiontype, localdict, globaldict):
    """Hook for pre-processing predicates, e.g. parsing string expressions"""
    return Rule(body, predicate, actiontype)

def clone_function(f):
    return new.function(
      f.func_code, f.func_globals, f.func_name, f.func_defaults, f.func_closure
    )

class Method(object):
    """A simple method w/optional chaining"""

    def __init__(self, body, signature=(), precedence=0, tail=None):
        self.body = body
        self.signature = signature
        self.precedence = precedence
        self.tail = tail
        self.can_tail = False
        try:
            args = inspect.getargspec(body)[0]
        except TypeError:
            pass
        else:
            if args and args[0]=='next_method':
                if getattr(body, 'im_self', None) is None:
                    self.can_tail = True

    decorate(classmethod)
    def make(cls, body, signature=(), precedence=0):
        return cls(body, signature, precedence)

    def __repr__(self):
        data = (self.body, self.signature, self.precedence, self.tail)
        return self.__class__.__name__+repr(data)

    def __call__(self, *args, **kw):
        if self.can_tail:
            return self.body(self.tail, *args, **kw)
        return self.body(*args, **kw)

    def override(self, other):
        if not self.can_tail:
            return self
        return self.tail_with(combine_actions(self.tail, other))

    def tail_with(self, tail):
        return self.__class__(self.body, self.signature, self.precedence, tail)



    def merge(self, other):
        #if self.__class__ is other.__class__ and self.body is other.body:
        #    XXX precedence should also match; need to merge signatures
        #    return self.__class__(
        #        self.body, ???, ???, combine_actions(self.tail, other.tail)
        #    )
        return AmbiguousMethods([self,other])

    decorate(classmethod)
    def make_decorator(cls, name, doc=None):
        if doc is None:
            doc = "Extend a generic function with a method of type ``%s``" \
                  % cls.__name__

        if cls is Method:
            maker = None   # allow gf's to use something else instead of Method
        else:
            maker = cls.make

        def decorate(f, pred=()):
            rules = rules_for(f)
            def callback(frame, name, func, old_locals):
                rule = parse_rule(
                    rules, func, pred, maker, frame.f_locals, frame.f_globals
                )
                rules.add(rule)
                if old_locals.get(name) in (f, rules):
                    return f    # prevent overwriting if name is the same
                return func
            return decorate_assignment(callback)

        try:
            decorate.__name__ = name
        except TypeError:
            decorate = new.function(
                decorate.func_code, decorate.func_globals, name,
                decorate.func_defaults, decorate.func_closure
            )
        decorate.__doc__ = doc
        return decorate

def aspects_for(ob):
    #try:
    return ob.__dict__
    #except (AttributeError, TypeError):
    #           

class Aspect(object):
    """Attach extra state to an object"""

    __slots__ = ()

    class __metaclass__(type):
        def __call__(cls, ob, *key):
            a = aspects_for(ob)
            try:
                return a[cls, key]
            except KeyError:
                # Use setdefault() to prevent race conditions
                ob = a.setdefault((cls, key), type.__call__(cls, ob, *key))
                return ob

    decorate(classmethod)
    def exists_for(cls, ob, *key):
        """Does an aspect of this type for the given key exist?""" 
        return (cls, key) in aspects_for(ob)

    decorate(classmethod)
    def delete(cls, ob, *key):
        """Ensure an aspect of this type for the given key does not exist"""
        a = aspects_for(ob)
        try:
            del a[cls, key]
        except KeyError:
            pass

    def __init__(self, owner):
        pass




class RuleSet(object):
    """An observable, stably-ordered collection of rules"""

    #default_action = NoApplicableMethods()
    default_actiontype = Method
    counter = 0

    def __init__(self):
        self.rules = []
        self.actiondefs = {}
        self.listeners = []

    def add(self, rule):
        sequence = self.counter
        self.counter += 1
        actiondefs = frozenset(self._actions_for(rule, sequence))
        self.rules.append( rule )
        self.actiondefs[rule] = sequence, actiondefs
        self.notify(added=actiondefs)

    def remove(self, rule):
        sequence, actiondefs = self.actiondefs.pop(rule)
        self.rules.remove(rule)
        self.notify(removed=actiondefs)

    #def changed(self, rule):
    #    sequence, actions = self.actions[rule]
    #    new_actions = frozenset(self._actions_for(rule, sequence))
    #    self.actions[rule] = sequence, new_actions
    #    self.notify(new_actions-actions, actions-new_actions)

    def notify(self, added=empty, removed=empty):
        for listener in self.listeners:
            listener.actions_changed(added, removed)

    def __iter__(self):
        for rule in self.rules:
            for actiondef in self.actiondefs[rule][1]:
                yield actiondef


    def _actions_for(self, (na, body, predicate, actiontype), sequence):
        actiontype = actiontype or self.default_actiontype
        for signature in predicate_signatures(predicate):
            yield ActionDef(actiontype, body, signature, sequence)

    def subscribe(self, listener):
        self.listeners.append(listener)
        if self.rules:
            listener.actions_changed(frozenset(self), empty)

    def unsubscribe(self, listener):
        self.listeners.remove(listener)


class Dispatching(Aspect):
    """Hold the dispatching attributes of a generic function"""

    def __init__(self, func):
        self.function = func
        self.snapshot()
        self.rules    = RuleSet()
        self.engine   = TypeEngine(self)

    def snapshot(self):
        self.clone = clone_function(self.function)

def rules_for(f):
    """Return the initialized ruleset for a generic function"""
    if not Dispatching.exists_for(f):
        d = Dispatching(f)
        d.rules.add(Rule(d.clone))
    return Dispatching(f).rules

def abstract():
    """Declare a function to be abstract"""
    def callback(frame, name, func, old_locals):
        Dispatching(func)   # Create empty RuleSet and default engine for func
        return func
    return decorate_assignment(callback)


class Engine(object):
    """Abstract base for dispatching engines"""

    def __init__(self, disp):
        self.func = disp.function
        self.ruleset = disp.rules
        disp.rules.subscribe(self)
    
    def actions_changed(self, added, removed):
        for r in removed:
            # force full regeneration if any rules removed
            return self.full_reset()
        for (na, atype, body, sig, seq) in added:
            self.add_method(sig, atype(body,sig,seq))

class TypeEngine(Engine):
    """Simple type-based dispatching"""

    def __init__(self, disp):
        self.registry = {}
        self.static_cache = {}
        self.cache = {}
        super(TypeEngine, self).__init__(disp)
        self.generate_code(disp)

    def full_reset(self):
        self.registry.clear()
        self.actions_changed(self.ruleset, ())

    def add_method(self, sig, action):
        registry = self.registry
        if sig in self.registry:
            registry[sig] = combine_actions(action, registry[sig])
        else:
            registry[sig] = action
        self.changed()
        
    def changed(self):
        if self.cache != self.static_cache:
            self.generate_code(Dispatching(self.func))

    def _snapshot_static(self):
        self.static_cache = self.registry.copy()
        self.changed()

    def generate_code(self, disp):
        self.cache = cache = self.static_cache.copy()

        def callback(*args):
            # XXX code similar to this could be generated directly...
            types = tuple([getattr(arg,'__class__',type(arg)) for arg in args])
            try:
                f = cache[types]
            except KeyError:
                action = self.ruleset.default_action
                for sig in self.registry:
                    if types==sig or implies(types, sig):
                        action = combine_actions(action, self.registry[sig])
                f = cache[types] = action
            return f(*args)

        c = Code.from_function(self.func, copy_lineno=True)
        args, star, dstar, defaults = inspect.getargspec(self.func)
        types = [
            Call(
                Const(getattr),
                (Local(name), Const('__class__'), Call(Const(type),(Local(name),)))
            ) for name in flatten(args)
        ]
        target = Call(Const(cache.get), (tuple(types), Const(callback)))
        c.return_(Call(target, map(tuplize,args), (), star, dstar))
        self.func.func_code = c.code()

def flatten(v):
    if isinstance(v,basestring): yield v; return
    for i in v:
        for ii in flatten(i): yield ii

def tuplize(v):
    if isinstance(v,basestring): return Local(v)
    if isinstance(v,list): return tuple(map(tuplize,v))

when = Method.make_decorator(
    "when", "Extend a generic function with a new action"
)

def implies(s1,s2):
    """Is s2 always true if s1 is true?"""
    return s1==s2

when(implies, (tuple,tuple))
def tuple_implies(s1,s2):
    if type(s1) is not tuple or type(s2) is not tuple:
        return s1==s2
    if len(s2)>len(s1):
        return False    # shorter tuple can't imply longer tuple
    for t1,t2 in zip(s1,s2):
        if not implies(t1,t2):
            return False
    else:
        return True

from types import ClassType, InstanceType
when(implies, (type,      type)     )(issubclass)
when(implies, (ClassType, ClassType))(issubclass)
when(implies, (type,      ClassType))(issubclass)
when(implies, (ClassType, type))
def classic_implies_new(s1, s2):
    # A classic class only implies a new-style one if it's ``object``
    # or ``InstanceType``; this is an exception to the general rule that
    # isinstance(X,Y) implies issubclass(X.__class__,Y)
    return s2 is object or s2 is InstanceType

when(implies, (Method,Method))
def method_implies(a1, a2):
    if a1.__class__ is a2.__class__:
        return implies(a1.signature, a2.signature)
    raise TypeError("Incompatible action types", a1, a2)

def YES(s1,s2): return True
def NO(s1,s2):  return False


def always_overrides(a,b):
    """instances of `a` always imply `b`; `b` instances never imply `a`"""
    when(implies, (a, b))(YES)
    when(implies, (b, a))(NO)

def merge_by_default(t):
    """instances of `t` never imply other instances of `t`"""
    when(implies, (t, t))(NO)


def combine_actions(a1,a2):
    """Return a new action for the combination of a1 and a2"""
    if a1 is None:
        return a2
    elif a2 is None:
        return a1
    elif implies(a1,a2):
        if not implies(a2,a1):
            return a1.override(a2)
    elif implies(a2,a1):
        return a2.override(a1)
    return a1.merge(a2)


class Around(Method):
    """'Around' Method (takes precedence over regular methods)"""

around = Around.make_decorator('around')

always_overrides(Around, Method)

# XXX need to get rid of the need for this!
when(implies, (Around,Around))(method_implies)








class MethodList(Method):
    """A list of related methods"""
    def __init__(self, items=(), tail=None):
        self.items = list(items)
        self.tail = tail
        self.can_tail = True

    _sorted_items = None

    decorate(classmethod)
    def make(cls, body, signature=(), precedence=0):
        return cls( [(signature, precedence, body)] )

    def __repr__(self):
        data = self.items, self.tail
        return self.__class__.__name__+repr(data)

    def tail_with(self, tail):
        return self.__class__(self.items, tail)

    def merge(self, other):
        if other.__class__ is not self.__class__:
            raise TypeError("Incompatible action types for merge", self, other)
        return self.__class__(
            self.items+other.items, combine_actions(self.tail, other.tail)
        )















    def sorted(self):
        if self._sorted_items is not None:
            return self._sorted_items

        self.items.sort(lambda a,b: cmp(a[1],b[1]))
        rest = [(s,b) for (s,p,b) in self.items]

        self._sorted_items = items = []
        seen = set()
        while rest:
            best = dominant_signatures(rest)
            map(rest.remove, best)
            for s,b in best:
                if b not in seen:
                    seen.add(b)
                    items.append((s,b))
        return items

merge_by_default(MethodList)

def disjuncts(ob):
    """Return a *list* of the logical disjunctions of `ob`"""
    return [ob]

when(disjuncts, (bool,))(lambda ob: [ob][:ob])

abstract()
def intersect(c1, c2):
    """Return the logical intersection of two conditions"""

around(intersect, (object, object))
def intersect_if_implies(next_method, c1, c2):
    if implies(c1,c2):      return c1
    elif implies(c2, c1):   return c2
    return next_method(c1, c2)

when(implies, (bool, bool))(lambda c1, c2: c2 or not c1)
when(implies, (bool, object))(lambda c1, c2: not c1)
when(implies, (object, bool))(lambda c1, c2: c2)


class Before(MethodList):
    """Method(s) to be called before the primary method(s)"""

    def __call__(self, *args, **kw):
        for sig, body in self.sorted():
            body(*args, **kw)
        return self.tail(*args, **kw)

before = Before.make_decorator('before')
merge_by_default(Before)


class After(MethodList):
    """Method(s) to be called after the primary method(s)"""

    def sorted(self):
        # Reverse the sorting for after methods
        if self._sorted_items is not None:
            return self._sorted_items
        items = super(After,self).sorted()
        items.reverse()
        return items

    def __call__(self, *args, **kw):
        retval = self.tail(*args, **kw)
        for sig, body in self.sorted():
            body(*args, **kw)

after  = After.make_decorator('after')
merge_by_default(After)

always_overrides(Around, Before)
always_overrides(Around, After)
always_overrides(Before, After)
always_overrides(Before, Method)
always_overrides(After, Method)





class DispatchError(Exception):
    """A dispatch error has occurred"""

    def __call__(self,*args,**kw):
        raise self.__class__(*self.args+(args,kw))  # XXX

    def __repr__(self):
        # This method is needed so doctests for 2.3/2.4 match 2.5
        return self.__class__.__name__+repr(self.args)

merge_by_default(DispatchError)


class NoApplicableMethods(DispatchError):
    """No applicable action has been defined for the given arguments"""

    def merge(self, other):
        return AmbiguousMethods([self,other])

always_overrides(Method, NoApplicableMethods)

RuleSet.default_action = NoApplicableMethods()



















class AmbiguousMethods(DispatchError):
    """More than one choice of action is possible"""

    def __init__(self, methods, *args):
        DispatchError.__init__(self, methods, *args)
        mine = self.methods = []
        for m in methods:
            if isinstance(m, AmbiguousMethods):
                mine.extend(m.methods)
            else:
                mine.append(m)

    def merge(self, other):
        return AmbiguousMethods(self.methods+[other])

    def override(self, other):
        return self
    def __repr__(self): return "AmbiguousMethods(%s)" % self.methods


when(implies, (AmbiguousMethods, Method))
def ambiguous_overrides(a1, a2):
    for m in a1.methods:
        if implies(m, a2):
            # if any ambiguous method overrides a2, we can toss it
            return True
    return False

when(implies, (Method, AmbiguousMethods))
def override_ambiguous(a1, a2):
    for m in a2.methods:
        if not implies(a1, m):
            return False
    return True     # can only override if it overrides all the ambiguity

# needed to disambiguate the above two methods if combining a pair of AM's:
merge_by_default(AmbiguousMethods)

# the core is finished loading, snapshot the core rules for implies()
Dispatching(implies).engine._snapshot_static()

def dominant_signatures(cases):
    """Return the most-specific ``(signature,body)`` pairs from `cases`

    `cases` is a sequence of ``(signature,body)`` pairs.  This routine checks
    the ``implies()`` relationships between pairs of signatures, and then
    returns a list of ``(signature,method)`` pairs such that no signature
    remaining in the original list implies a signature in the new list.
    The relative order of cases in the new list is preserved.
    """

    if len(cases)==1:
        # Shortcut for common case
        return list(cases)

    best, rest = list(cases[:1]), list(cases[1:])

    for new_sig, new_meth in rest:

        for old_sig, old_meth in best[:]:   # copy so we can modify inplace

            new_implies_old = implies(new_sig, old_sig)
            old_implies_new = implies(old_sig, new_sig)

            if new_implies_old:

                if not old_implies_new:
                    # better, remove the old one
                    best.remove((old_sig, old_meth))

            elif old_implies_new:
                # worse, skip adding the new one
                break
        else:
            # new_sig has passed the gauntlet, as it has not been implied
            # by any of the current "best" items
            best.append((new_sig,new_meth))

    return best



