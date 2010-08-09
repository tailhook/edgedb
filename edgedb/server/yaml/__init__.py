##
# Copyright (c) 2008-2010 Sprymix Inc.
# All rights reserved.
#
# See LICENSE for details.
##


import io

import importlib
import collections
import itertools
import decimal

from semantix.utils import lang
from semantix.utils.lang import yaml
from semantix.utils.nlang import morphology
from semantix.utils.algos.persistent_hash import persistent_hash
from semantix.utils.algos import topological

from semantix import caos
from semantix.caos import proto
from semantix.caos import backends
from semantix.caos import delta as base_delta
from semantix.caos import objects
from semantix.caos import query as caos_query
from semantix.caos.caosql import expr as caosql_expr
from semantix.caos.caosql import errors as caosql_exc

from . import delta
from .common import StructMeta


class MetaError(caos.MetaError):
    def __init__(self, error, context=None):
        super().__init__(error)
        self.context = context

    def __str__(self):
        result = super().__str__()
        if self.context and self.context.start:
            result += '\ncontext: %s, line %d, column %d' % \
                        (self.context.name, self.context.start.line, self.context.start.column)
        return result


class LangObjectMeta(type(yaml.Object), type(proto.Prototype)):
    def __init__(cls, name, bases, dct, *, adapts=None, ignore_aliases=False):
        type(yaml.Object).__init__(cls, name, bases, dct, adapts=adapts,
                                                          ignore_aliases=ignore_aliases)
        type(proto.Prototype).__init__(cls, name, bases, dct)


class LangObject(yaml.Object, metaclass=LangObjectMeta):
    @classmethod
    def get_canonical_class(cls):
        for base in cls.__bases__:
            if issubclass(base, caos.types.ProtoObject) and not issubclass(base, LangObject):
                return base

        return cls


class Bool(yaml.Object, adapts=objects.boolean.Bool, ignore_aliases=True):
    @classmethod
    def represent(cls, data):
        return bool(data)


class TimeDelta(yaml.Object, adapts=objects.datetime.TimeDelta, ignore_aliases=True):
    @classmethod
    def represent(cls, data):
        return str(data)


class Int(yaml.Object, adapts=objects.int.Int, ignore_aliases=True):
    @classmethod
    def represent(cls, data):
        return int(data)


class DecimalMeta(LangObjectMeta, type(objects.numeric.Decimal)):
    pass


class Decimal(yaml.Object, metaclass=DecimalMeta,
              adapts=objects.numeric.Decimal, ignore_aliases=True):
    @classmethod
    def represent(cls, data):
        return str(data)



class WordCombination(LangObject, adapts=morphology.WordCombination, ignore_aliases=True):
    def construct(self):
        if isinstance(self.data, str):
            morphology.WordCombination.__init__(self, self.data)
        else:
            word = morphology.WordCombination.from_dict(self.data)
            self.forms = word.forms
            self.value = self.forms.get('singular', next(iter(self.forms.values())))

    @classmethod
    def represent(cls, data):
        return data.as_dict()

    @classmethod
    def adapt(cls, obj):
        return cls.from_dict(obj)


class LinkMapping(LangObject, adapts=caos.types.LinkMapping, ignore_aliases=True):
    def __new__(cls, context, data):
        return caos.types.LinkMapping.__new__(cls, data)

    @classmethod
    def represent(cls, data):
        return str(data)


class LinkSearchWeight(LangObject, adapts=caos.types.LinkSearchWeight, ignore_aliases=True):
    def __new__(cls, context, data):
        return caos.types.LinkSearchWeight.__new__(cls, data)

    @classmethod
    def represent(cls, data):
        return str(data)


class PrototypeMeta(LangObjectMeta, StructMeta):
    pass


class Prototype(LangObject, adapts=proto.Prototype, metaclass=PrototypeMeta):
    pass


class DefaultSpec(LangObject, adapts=proto.DefaultSpec, ignore_aliases=True):
    @classmethod
    def resolve(cls, data):
        if isinstance(data, dict) and 'query' in data:
            return QueryDefaultSpec
        else:
            return LiteralDefaultSpec


class LiteralDefaultSpec(DefaultSpec, adapts=proto.LiteralDefaultSpec):
    def construct(self):
        proto.LiteralDefaultSpec.__init__(self, self.data)

    @classmethod
    def represent(cls, data):
        return data.value


class QueryDefaultSpec(DefaultSpec, adapts=proto.QueryDefaultSpec):
    def construct(self):
        proto.QueryDefaultSpec.__init__(self, self.data['query'])

    @classmethod
    def represent(cls, data):
        return {'query': str(data.value)}


class AtomConstraint(LangObject, ignore_aliases=True):
    pass


class AtomConstraintMinLength(AtomConstraint, adapts=proto.AtomConstraintMinLength):
    def construct(self):
        proto.AtomConstraintMinLength.__init__(self, self.data['min-length'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'min-length': data.value}


class AtomConstraintMinValue(AtomConstraint, adapts=proto.AtomConstraintMinValue):
    def construct(self):
        proto.AtomConstraintMinValue.__init__(self, self.data['min-value'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'min-value': data.value}


class AtomConstraintMinExValue(AtomConstraint, adapts=proto.AtomConstraintMinExValue):
    def construct(self):
        proto.AtomConstraintMinExValue.__init__(self, self.data['min-value-ex'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'min-value-ex': data.value}


class AtomConstraintMaxLength(AtomConstraint, adapts=proto.AtomConstraintMaxLength):
    def construct(self):
        proto.AtomConstraintMaxLength.__init__(self, self.data['max-length'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'max-length': data.value}


class AtomConstraintMaxValue(AtomConstraint, adapts=proto.AtomConstraintMaxValue):
    def construct(self):
        proto.AtomConstraintMaxValue.__init__(self, self.data['max-value'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'max-value': data.value}


class AtomConstraintMaxExValue(AtomConstraint, adapts=proto.AtomConstraintMaxExValue):
    def construct(self):
        proto.AtomConstraintMaxValue.__init__(self, self.data['max-value-ex'], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'max-value-ex': data.value}


class AtomConstraintPrecision(AtomConstraint, adapts=proto.AtomConstraintPrecision):
    def construct(self):
        if isinstance(self.data['precision'], int):
            precision = (int(self.data['precision']), 0)
        else:
            precision = int(self.data['precision'][0])
            scale = int(self.data['precision'][1])

            if scale >= precision:
                raise ValueError('Scale must be strictly less than total numeric precision')

            precision = (precision, scale)
        proto.AtomConstraintPrecision.__init__(self, precision, context=self.context)

    @classmethod
    def represent(cls, data):
        if data.value[1] is None:
            return {'precision': data.value[0]}
        else:
            return {'precision': list(data.value)}


class AtomConstraintRounding(AtomConstraint, adapts=proto.AtomConstraintRounding):
    map = {
        'ceiling': decimal.ROUND_CEILING,
        'down': decimal.ROUND_DOWN,
        'floor': decimal.ROUND_FLOOR,
        'half-down': decimal.ROUND_HALF_DOWN,
        'half-even': decimal.ROUND_HALF_EVEN,
        'half-up': decimal.ROUND_HALF_UP,
        'up': decimal.ROUND_UP,
        '05up': decimal.ROUND_05UP
    }

    rmap = dict(zip(map.values(), map.keys()))

    def construct(self):
        proto.AtomConstraintRounding.__init__(self, self.map[self.data['rounding']], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'rounding': cls.rmap[data.value]}


class AtomConstraintExpr(AtomConstraint, adapts=proto.AtomConstraintExpr):
    def construct(self):
        proto.AtomConstraintExpr.__init__(self, [self.data['expr'].strip(' \n')], context=self.context)

    @classmethod
    def represent(cls, data):
        return {'expr': next(iter(data.values))}


class AtomConstraintRegExp(AtomConstraint, adapts=proto.AtomConstraintRegExp):
    def construct(self):
        proto.AtomConstraintRegExp.__init__(self, [self.data['regexp']], context=self.context)

    @classmethod
    def represent(self, data):
        return {'regexp': next(iter(data.values))}

default_name = None

class Atom(Prototype, adapts=proto.Atom):
    def construct(self):
        data = self.data

        default = data['default']
        if default and not isinstance(default, list):
            default = [default]

        proto.Atom.__init__(self, name=default_name, backend=None, base=data['extends'],
                            default=default, title=data['title'],
                            description=data['description'], is_abstract=data['abstract'],
                            is_final=data['final'],
                            attributes=data.get('attributes'),
                            _setdefaults_=False, _relaxrequired_=True)
        self._constraints = data.get('constraints')

    @classmethod
    def represent(cls, data):
        result = {
            'extends': data.base
        }

        if data.base:
            result['extends'] = data.base

        if data.default is not None:
            result['default'] = data.default

        if data.title:
            result['title'] = data.title

        if data.description:
            result['description'] = data.description

        if data.is_abstract:
            result['abstract'] = data.is_abstract

        if data.is_final:
            result['final'] = data.is_final

        if data.constraints:
            result['constraints'] = sorted(list(itertools.chain.from_iterable(data.constraints.values())),
                                           key=lambda i: i.__class__.constraint_name)

        if data.attributes:
            result['attributes'] = dict(data.attributes)

        return result


class Concept(Prototype, adapts=proto.Concept):
    def construct(self):
        data = self.data
        extends = data.get('extends')
        if extends:
            if not isinstance(extends, list):
                extends = [extends]

        proto.Concept.__init__(self, name=default_name, backend=None,
                               base=tuple(extends) if extends else tuple(),
                               title=data.get('title'), description=data.get('description'),
                               is_abstract=data.get('abstract'), is_final=data.get('final'),
                               _setdefaults_=False, _relaxrequired_=True)
        self._links = data.get('links', {})
        self._computables = data.get('computables', {})
        self._indexes = data.get('indexes') or ()

    @classmethod
    def represent(cls, data):
        result = {
            'extends': list(itertools.chain(data.base, data.custombases))
        }

        if data.title:
            result['title'] = data.title

        if data.description:
            result['description'] = data.description

        if data.is_abstract:
            result['abstract'] = data.is_abstract

        if data.is_final:
            result['final'] = data.is_final

        if data.own_pointers:
            result['links'] = {}
            result['computables'] = {}
            for ptr_name, ptr in data.own_pointers.items():
                if isinstance(ptr.first, proto.Computable):
                    result['computables'][ptr_name] = ptr
                else:
                    result['links'][ptr_name] = ptr

        if data.indexes:
            result['indexes'] = list(sorted(data.indexes, key=lambda i: i.expr))

        return result

    def process_index_expr(self, index):
        return index

    def materialize(self, meta):
        indexes = set()
        for index in self.indexes:
            indexes.add(self.process_index_expr(index))
        self.indexes = indexes

        proto.Concept.materialize(self, meta)


class SourceIndex(LangObject, adapts=proto.SourceIndex, ignore_aliases=True):
    def construct(self):
        proto.SourceIndex.__init__(self, expr=self.data)

    @classmethod
    def represent(cls, data):
        return str(data.expr)


class LinkPropertyDef(Prototype, proto.LinkProperty):
    def construct(self):
        data = self.data

        extends = data.get('extends')
        if extends:
            if not isinstance(extends, list):
                extends = [extends]

        proto.LinkProperty.__init__(self, name=default_name, title=data['title'],
                                    base=tuple(extends) if extends else tuple(),
                                    description=data['description'], readonly=data['readonly'],
                                    _setdefaults_=False, _relaxrequired_=True)


class LinkProperty(Prototype, adapts=proto.LinkProperty, ignore_aliases=True):
    def construct(self):
        data = self.data
        if isinstance(data, str):
            proto.LinkProperty.__init__(self, name=default_name, target=data, _relaxrequired_=True)
        else:
            atom_name, info = next(iter(data.items()))

            default = info['default']
            if default and not isinstance(default, list):
                default = [default]

            proto.LinkProperty.__init__(self, name=default_name, target=atom_name,
                                        title=info['title'], description=info['description'],
                                        readonly=info['readonly'], default=default,
                                        _setdefaults_=False, _relaxrequired_=True)
            self._constraints = info.get('constraints')
            self._abstract_constraints = info.get('abstract-constraints')

    @classmethod
    def represent(cls, data):
        result = {}

        if data.target and data.target.constraints and data.target.automatic:
            items = itertools.chain.from_iterable(data.target.constraints.values())
            result['constraints'] = list(items)

        if data.local_constraints:
            constraints = result.setdefault('constraints', [])
            constraints.extend(itertools.chain.from_iterable(data.local_constraints.values()))

        if data.abstract_constraints:
            items = itertools.chain.from_iterable(data.local_constraints.values())
            result['abstract-constraints'] = list(items)

        if data.title:
            result['title'] = data.title

        if data.description:
            result['description'] = data.description

        if data.default is not None:
            result['default'] = data.default

        if result:
            if data.target:
                return {data.target.name: result}
            else:
                return result
        else:
            if data.target:
                return str(data.target.name)
            else:
                return {}


class LinkPropertyProps(Prototype, proto.LinkProperty):
    def construct(self):
        data = self.data

        default = data['default']
        if default and not isinstance(default, list):
            default = [default]

        proto.LinkProperty.__init__(self, name=default_name, default=default,
                                    _setdefaults_=False, _relaxrequired_=True)


class LinkDef(Prototype, adapts=proto.Link):
    def construct(self):
        data = self.data
        extends = data.get('extends')
        if extends:
            if not isinstance(extends, list):
                extends = [extends]

        default = data['default']
        if default and not isinstance(default, list):
            default = [default]

        proto.Link.__init__(self, name=default_name, backend=None,
                            base=tuple(extends) if extends else tuple(),
                            title=data['title'], description=data['description'],
                            is_abstract=data.get('abstract'), is_final=data.get('final'),
                            readonly=data.get('readonly'),
                            mapping=data.get('mapping'),
                            default=default,
                            _setdefaults_=False, _relaxrequired_=True)

        self._properties = data['properties']
        self._computables = data.get('computables', {})
        self._indexes = data.get('indexes') or ()

    @classmethod
    def represent(cls, data):
        result = {}

        if data.generic():
            if data.base:
                result['extends'] = list(data.base)

        if data.title:
            result['title'] = data.title

        if data.description:
            result['description'] = data.description

        if data.is_abstract:
            result['abstract'] = data.is_abstract

        if data.is_final:
            result['final'] = data.is_final

        if data.readonly:
            result['readonly'] = data.readonly

        if data.mapping:
            result['mapping'] = data.mapping

        if isinstance(data.target, proto.Atom) and data.target.automatic:
            result['constraints'] = list(itertools.chain.from_iterable(data.target.constraints.values()))

        if data.required:
            result['required'] = data.required

        if data.default is not None:
            result['default'] = data.default

        if data.own_pointers:
            result['properties'] = {}
            result['computables'] = {}
            for ptr_name, ptr in data.own_pointers.items():
                if isinstance(ptr, proto.Computable):
                    result['computables'][ptr_name] = ptr
                else:
                    result['properties'][ptr_name] = ptr

        if data.local_constraints:
            constraints = result.setdefault('constraints', [])
            constraints.extend(itertools.chain.from_iterable(data.local_constraints.values()))

        if data.abstract_constraints:
            items = itertools.chain.from_iterable(data.abstract_constraints.values())
            result['abstract-constraints'] = list(items)

        if data.search:
            result['search'] = data.search

        if data.indexes:
            result['indexes'] = list(sorted(data.indexes, key=lambda i: i.expr))

        return result


class LinkSet(Prototype, adapts=proto.LinkSet):
    @classmethod
    def represent(cls, data):
        result = {}

        for l in data.links:
            if isinstance(l, proto.Computable):
                result = Computable.represent(l)
                break

            if isinstance(l.target, proto.Atom) and l.target.automatic:
                key = l.target.base
            else:
                key = l.target.name
            result[str(key)] = l

        return result


class Computable(Prototype, adapts=proto.Computable):
    def construct(self):
        if isinstance(self.data, str):
            data = {'expression': self.data}
        else:
            data = self.data

        proto.Computable.__init__(self, expression=data.get('expression'),
                                  name=default_name, source=None,
                                  title=data.get('title'),
                                  description=data.get('description'),
                                  _setdefaults_=False,
                                  _relaxrequired_=True)

    @classmethod
    def represent(cls, data):
        result = {}

        result['expression'] = data.expression
        return result


class PointerConstraint(LangObject, adapts=proto.PointerConstraint, ignore_aliases=True):
    @classmethod
    def represent(cls, data):
        return {cls.constraint_name: next(iter(data.values))}


class PointerConstraintUnique(PointerConstraint, adapts=proto.PointerConstraintUnique):
    def construct(self):
        values = {self.data[self.__class__.constraint_name]}
        proto.PointerConstraintUnique.__init__(self, values, context=self.context)


class LinkSearchConfiguration(LangObject, adapts=proto.LinkSearchConfiguration, ignore_aliases=True):
    def construct(self):
        if isinstance(self.data, bool):
            if self.data:
                weight = caos.types.SearchWeight_A
            else:
                weight = None
        else:
            if self.data:
                weight = caos.types.LinkSearchWeight(self.data['weight'])
            else:
                weight = None

        proto.LinkSearchConfiguration.__init__(self, weight=weight)

    @classmethod
    def represent(cls, data):
        if data.weight:
            return {'weight': data.weight}
        else:
            return None


class LinkList(LangObject, list):

    def construct(self):
        data = self.data
        if isinstance(data, str):
            link = proto.Link(source=None, target=data, name=default_name, _setdefaults_=False,
                              _relaxrequired_=True)
            link.context = self.context
            self.append(link)
        elif isinstance(data, list):
            for target in data:
                link = proto.Link(source=None, target=target, name=default_name,
                                  _setdefaults_=False, _relaxrequired_=True)
                link.context = self.context
                self.append(link)
        else:
            for target, info in data.items():
                if not isinstance(target, tuple):
                    target = (target,)

                default = info['default']
                if default and not isinstance(default, list):
                    default = [default]

                props = info['properties']

                for t in target:
                    link = proto.Link(name=default_name, target=t, mapping=info['mapping'],
                                      required=info['required'], title=info['title'],
                                      description=info['description'], readonly=info['readonly'],
                                      default=default,
                                      _setdefaults_=False, _relaxrequired_=True)

                    search = info.get('search')
                    if search and search.weight is not None:
                        link.search = search

                    link.context = self.context

                    link._constraints = info.get('constraints')
                    link._abstract_constraints = info.get('abstract-constraints')
                    link._properties = props

                    self.append(link)


class MetaSet(LangObject):
    def construct(self):
        data = self.data
        context = self.context

        if context.document.import_context.builtin:
            self.include_builtin = True
            realm_meta_class = proto.BuiltinRealmMeta
        else:
            self.include_builtin = False
            realm_meta_class = proto.RealmMeta

        self.toplevel = context.document.import_context.toplevel
        globalindex = context.document.import_context.metaindex

        localindex = realm_meta_class()
        self.module = data.get('module', None)
        if not self.module:
            self.module = context.document.module.__name__
        localindex.add_module(self.module, None)

        if self.toplevel and self.module and caos.Name.is_qualified(self.module):
            main_module = caos.Name(self.module)
        else:
            main_module = None
        self.finalindex = realm_meta_class(main_module=main_module)

        for alias, module in context.document.imports.items():
            localindex.add_module(module.__name__, alias)

        self.caosql_expr = caosql_expr.CaosQLExpression(globalindex, localindex.modules)

        self.read_atoms(data, globalindex, localindex)
        self.read_link_properties(data, globalindex, localindex)
        self.read_links(data, globalindex, localindex)
        self.read_concepts(data, globalindex, localindex)

        if self.toplevel:
            # The final pass on may produce additional objects,
            # thus, it has to be performed in reverse order.
            concepts = self.order_concepts(globalindex)
            links = self.order_links(globalindex)
            linkprops = self.order_link_properties(globalindex)
            computables = self.order_computables(globalindex)
            atoms = self.order_atoms(globalindex)

            for atom in atoms:
                if self.include_builtin or atom.name.module != 'semantix.caos.builtins':
                    atom.setdefaults()
                    self.finalindex.add(atom)

            for comp in computables:
                if self.include_builtin or comp.name.module != 'semantix.caos.builtins':
                    comp.setdefaults()
                    self.finalindex.add(comp)

            for prop in linkprops:
                if self.include_builtin or prop.name.module != 'semantix.caos.builtins':
                    prop.setdefaults()
                    self.finalindex.add(prop)

            for link in links:
                if self.include_builtin or link.name.module != 'semantix.caos.builtins':
                    link.setdefaults()
                    link.materialize(self.finalindex)
                    self.finalindex.add(link)

            for concept in concepts:
                if self.include_builtin or concept.name.module != 'semantix.caos.builtins':
                    concept.setdefaults()
                    concept.materialize(self.finalindex)
                    self.finalindex.add(concept)


    def _check_base(self, element, base_name, globalmeta):
        base = globalmeta.get(base_name, type=element.__class__.get_canonical_class(),
                              include_pyobjects=True)
        if isinstance(base, caos.types.ProtoObject) and base.is_final:
            raise MetaError('"%s" is final and cannot be inherited from' % base.name,
                            element.context)


    def read_atoms(self, data, globalmeta, localmeta):
        backend = None

        for atom_name, atom in data['atoms'].items():
            atom.name = caos.Name(name=atom_name, module=self.module)
            atom.backend = backend
            globalmeta.add(atom)
            localmeta.add(atom)

        ns = localmeta.get_namespace(proto.Atom)

        for atom in localmeta('atom', include_builtin=self.include_builtin):
            if atom.base:
                try:
                    atom.base = ns.normalize_name(atom.base, include_pyobjects=True)
                    self._check_base(atom, atom.base, globalmeta)
                except caos.MetaError as e:
                    raise MetaError(e, atom.context) from e


    def order_atoms(self, globalmeta):
        g = {}

        for atom in globalmeta('atom', include_automatic=True, include_builtin=True):
            constraints = getattr(atom, '_constraints', None)
            if constraints:
                atom.normalize_constraints(globalmeta, constraints)
                for constraint in constraints:
                    atom.add_constraint(constraint)

            g[atom.name] = {"item": atom, "merge": [], "deps": []}

            if atom.base:
                atom_base = globalmeta.get(atom.base, include_pyobjects=True)
                if isinstance(atom_base, proto.Atom):
                    atom.base = atom_base.name
                    g[atom.name]['merge'].append(atom.base)

        return topological.normalize(g, merger=proto.Atom.merge)

    def add_pointer_constraints(self, parent, constraints, type, constraint_type='regular'):
        if constraints:
            for constraint in constraints:
                if isinstance(constraint, proto.PointerConstraint):
                    if isinstance(constraint, proto.PointerConstraintUnique):
                        if type == 'atom':
                            if len(constraint.values) > 1 \
                                    or isinstance(list(constraint.values)[0], str):
                                raise caos.MetaError(('invalid value for atomic pointer "%s" '
                                                      'unique constraint') % parent.normal_name())
                        elif type == 'concept':
                            if not isinstance(list(constraint.values)[0], str):
                                raise caos.MetaError(('invalid value for non-atomic pointer "%s" '
                                                      'unique constraint, expecting an expression')\
                                                      % parent.normal_name())

                    if constraint_type == 'abstract':
                        parent.add_abstract_constraint(constraint)
                    else:
                        parent.add_constraint(constraint)

    def read_link_properties(self, data, globalmeta, localmeta):
        linkprop_ns = localmeta.get_namespace(proto.LinkProperty)

        for property_name, property in data['link-properties'].items():
            module = self.module
            property.name = caos.Name(name=property_name, module=module)

            globalmeta.add(property)
            localmeta.add(property)

        for prop in localmeta('link_property', include_builtin=self.include_builtin):
            if prop.base:
                prop.base = tuple(linkprop_ns.normalize_name(b) for b in prop.base)
            elif prop.name != 'semantix.caos.builtins.link_property':
                prop.base = (caos.Name('semantix.caos.builtins.link_property'),)


    def order_link_properties(self, globalmeta):
        g = {}

        for prop in globalmeta('link_property', include_automatic=True, include_builtin=True):
            g[prop.name] = {"item": prop, "merge": [], "deps": []}

            if prop.base:
                g[prop.name]['merge'].extend(prop.base)

        return topological.normalize(g, merger=proto.LinkProperty.merge)


    def read_properties_for_link(self, link, globalmeta, localmeta):
        atom_ns = localmeta.get_namespace(proto.Atom)
        linkprop_ns = localmeta.get_namespace(proto.LinkProperty)

        props = getattr(link, '_properties', None)
        if not props:
            return

        for property_name, property in props.items():

            property_qname = linkprop_ns.normalize_name(property_name, default=None)

            if not property_qname:
                if not link.generic():
                    # Only generic links can implicitly define properties
                    raise caos.MetaError('reference to an undefined property "%s"' % property_name)

                # The link property has not been defined globally.
                if not caos.Name.is_qualified(property_name):
                    # If the name is not fully qualified, assume inline link property
                    # definition. The only attribute that is used for global definition
                    # is the name.
                    property_qname = caos.Name(name=property_name, module=self.module)
                    propdef = proto.LinkProperty(name=property_qname,
                                    base=(caos.Name('semantix.caos.builtins.link_property'),))
                    globalmeta.add(propdef)
                    localmeta.add(propdef)
                else:
                    property_qname = caos.Name(property_name)

            if link.generic():
                property.target = atom_ns.normalize_name(property.target)
            else:
                link_base = globalmeta.get(link.base[0], type=proto.Link)
                propdef = link_base.pointers.get(property_qname)
                if not propdef:
                    raise caos.MetaError('link "%s" does not define property "%s"' \
                                         % (link.name, property_qname))
                property_qname = propdef.normal_name()

            # A new specialized subclass of the link property is created for each
            # (source, property_name, target_atom) combination
            property.base = (property_qname,)
            prop_genname = proto.LinkProperty.generate_name(link.name, property.target,
                                                            property_qname)
            property.name = caos.Name(name=prop_genname, module=property_qname.module)
            property.source = link

            self.add_pointer_constraints(property, getattr(property, '_constraints', ()), 'atom')
            self.add_pointer_constraints(property, getattr(property, '_abstract_constraints', ()),
                                                                     'atom', 'abstract')

            globalmeta.add(property)
            localmeta.add(property)

            link.add_pointer(property)

    def _create_base_link(self, link, link_qname, globalmeta, localmeta, type=None):
        type = type or proto.Link

        base = 'semantix.caos.builtins.link' if type is proto.Link else \
               'semantix.caos.builtins.link_property'

        linkdef = type(name=link_qname,
                       base=(caos.Name(base),),
                       _setdefaults_=False)
        if isinstance(link.target, str):
            target = globalmeta.get(link.target)
        else:
            target = link.target
        linkdef.is_atom = isinstance(target, proto.Atom)
        globalmeta.add(linkdef)
        if localmeta:
            localmeta.add(linkdef)
        return linkdef

    def _read_computables(self, source, globalmeta, localmeta):
        comp_ns = localmeta.get_namespace(proto.Computable)

        for cname, computable in source._computables.items():
            computable_qname = comp_ns.normalize_name(cname, default=None)

            if not computable_qname:
                if not caos.Name.is_qualified(cname):
                    computable_qname = caos.Name(name=cname, module=self.module)
                else:
                    computable_qname = caos.Name(cname)

            if computable_qname in source.own_pointers:
                raise MetaError('computable "%(name)s" conflicts with "%(name)s" pointer '
                                'defined in the same source' % {'name': computable_qname},
                                 computable.context)

            computable_name = proto.Computable.generate_name(source.name, None, cname)
            computable.source = source
            computable.name = caos.Name(name=computable_name, module=computable_qname.module)
            computable.setdefaults()
            source.add_pointer(computable)

            globalmeta.add(computable)
            localmeta.add(computable)

    def order_computables(self, globalmeta):
        return globalmeta('computable', include_automatic=True, include_builtin=True)


    def read_links(self, data, globalmeta, localmeta):

        link_ns = localmeta.get_namespace(proto.Link)

        for link_name, link in data['links'].items():
            module = self.module
            link.name = caos.Name(name=link_name, module=module)

            self.read_properties_for_link(link, globalmeta, localmeta)

            globalmeta.add(link)
            localmeta.add(link)

        for link in localmeta('link', include_builtin=self.include_builtin):
            if link.base:
                link.base = tuple(link_ns.normalize_name(b) for b in link.base)
            elif link.name != 'semantix.caos.builtins.link':
                link.base = (caos.Name('semantix.caos.builtins.link'),)

            self._read_computables(link, globalmeta, localmeta)

            for index in link._indexes:
                expr, tree = self.normalize_index_expr(index.expr, link, globalmeta, localmeta)
                idx = proto.SourceIndex(expr, tree=tree)
                idx.context = index.context
                link.add_index(idx)

    def order_links(self, globalmeta):
        g = {}

        for link in globalmeta('link', include_automatic=True, include_builtin=True):
            for property_name, property in link.pointers.items():
                if property.target:
                    property.target = globalmeta.get(property.target)

                    constraints = getattr(property, 'constraints', None)
                    if constraints:
                        atom_constraints = [c for c in constraints.values()
                                            if isinstance(c, proto.AtomConstraint)]
                    else:
                        atom_constraints = None
                    if atom_constraints:
                        # Got an inline atom definition.
                        atom = self.genatom(globalmeta, link, property.target.name, property_name,
                                                                                 atom_constraints)
                        globalmeta.add(atom)
                        property.target = atom

            if link.source and not isinstance(link.source, proto.Prototype):
                link.source = globalmeta.get(link.source)

            if link.target and not isinstance(link.target, proto.Prototype):
                link.target = globalmeta.get(link.target)

            g[link.name] = {"item": link, "merge": [], "deps": []}

            if not link.generic() and not link.atomic():
                base = globalmeta.get(link.normal_name())
                if base.is_atom:
                    raise caos.MetaError('%s link target conflict (atom/concept)' % \
                                         link.normal_name())

            constraints = getattr(link, '_constraints', ())
            type = 'atom' if link.atomic() else 'concept'
            if not link.generic() and constraints:
                link_constraints = [c for c in constraints if isinstance(c, proto.PointerConstraint)]
                self.add_pointer_constraints(link, link_constraints, type)

            self.add_pointer_constraints(link, getattr(link, '_abstract_constraints', ()),
                                                             type, 'abstract')

            if link.base:
                for base_name in link.base:
                    self._check_base(link, base_name, globalmeta)

                g[link.name]['merge'].extend(link.base)

        links = topological.normalize(g, merger=proto.Link.merge)

        csql_expr = caosql_expr.CaosQLExpression(globalmeta)

        try:
            for link in links:
                for index in link.indexes:
                    csql_expr.check_source_atomic_expr(index.tree, link)

                self.normalize_computables(link, globalmeta)
                self.normalize_pointer_defaults(link, globalmeta)

        except caosql_exc.CaosQLReferenceError as e:
            raise MetaError(e.args[0], context=index.context) from e

        return links

    def read_concepts(self, data, globalmeta, localmeta):
        backend = None

        concept_ns = localmeta.get_namespace(proto.Concept)
        link_ns = localmeta.get_namespace(proto.Link)

        for concept_name, concept in data['concepts'].items():
            concept.name = caos.Name(name=concept_name, module=self.module)
            concept.backend = backend

            if globalmeta.get(concept.name, None):
                raise caos.MetaError('%s already defined' % concept.name)

            globalmeta.add(concept)
            localmeta.add(concept)

        for concept in localmeta('concept', include_builtin=self.include_builtin):
            bases = []
            custombases = []

            if concept.base:
                for b in concept.base:
                    base_name = concept_ns.normalize_name(b, include_pyobjects=True)
                    if proto.Concept.is_prototype(base_name):
                        bases.append(base_name)
                    else:
                        cls = localmeta.get(base_name, include_pyobjects=True)
                        if not issubclass(cls, caos.concept.Concept):
                            raise caos.MetaError('custom concept base classes must inherit from '
                                                 'caos.concept.Concept: %s' % base_name)
                        custombases.append(base_name)

            if not bases and concept.name != 'semantix.caos.builtins.BaseObject':
                bases.append(caos.Name('semantix.caos.builtins.Object'))

            concept.base = tuple(bases)
            concept.custombases = tuple(custombases)

            for link_name, links in concept._links.items():
                for link in links:
                    link.source = concept.name
                    link.target = concept_ns.normalize_name(link.target)

                    link_qname = link_ns.normalize_name(link_name, default=None)
                    if not link_qname:
                        # The link has not been defined globally.
                        if not caos.Name.is_qualified(link_name):
                            # If the name is not fully qualified, assume inline link definition.
                            # The only attribute that is used for global definition is the name.
                            link_qname = caos.Name(name=link_name, module=self.module)
                            self._create_base_link(link, link_qname, globalmeta, localmeta)
                        else:
                            link_qname = caos.Name(link_name)

                    # A new specialized subclass of the link is created for each
                    # (source, link_name, target) combination
                    link.base = (link_qname,)
                    link_genname = proto.Link.generate_name(link.source, link.target, link_qname)
                    link.name = caos.Name(name=link_genname, module=link_qname.module)

                    self.read_properties_for_link(link, globalmeta, localmeta)

                    globalmeta.add(link)
                    localmeta.add(link)
                    concept.add_pointer(link)

        for concept in localmeta('concept', include_builtin=self.include_builtin):
            self._read_computables(concept, globalmeta, localmeta)

            for index in concept._indexes:
                expr, tree = self.normalize_index_expr(index.expr, concept, globalmeta, localmeta)
                index.expr = expr
                index.tree = tree
                concept.add_index(index)


    def normalize_index_expr(self, expr, concept, globalmeta, localmeta):
        expr, tree = self.caosql_expr.normalize_source_expr(expr, concept)
        return expr, tree


    def normalize_pointer_defaults(self, source, globalmeta):
        for link_name, links in source.pointers.items():
            if not isinstance(links, proto.LinkSet):
                links = [links]

            for link in links:
                if isinstance(link, proto.Computable):
                    continue

                if link.default:
                    for default in link.default:
                        if isinstance(default, QueryDefaultSpec):
                            module_aliases = {None: str(default.context.document.import_context)}
                            for alias, module in default.context.document.imports.items():
                                module_aliases[alias] = module.__name__

                            value, tree = self.caosql_expr.normalize_expr(default.value,
                                                                          module_aliases)

                            first = list(tree.result_types.values())[0][0]
                            if len(tree.result_types) > 1 or not \
                                                first.issubclass(globalmeta, link.target):
                                raise MetaError(('default value query must yield a '
                                                 'single-column result of type "%s"') %
                                                 link.target.name, default.context)

                            if not isinstance(link.target, caos.types.ProtoAtom):
                                if link.mapping not in (caos.types.ManyToOne,
                                                        caos.types.ManyToMany):
                                    raise MetaError('concept links with query defaults ' \
                                                    'must have either a "*1" or "**" mapping',
                                                     default.context)

                            default.value = value
                    link.normalize_defaults()


    def normalize_computables(self, source, globalmeta):
        for link_name, links in source.pointers.items():
            if not isinstance(links, proto.LinkSet):
                links = [links]

            for link in links:
                if not isinstance(link, proto.Computable):
                    continue

                module_aliases = {None: str(source.context.document.import_context)}
                for alias, module in source.context.document.imports.items():
                    module_aliases[alias] = module.__name__

                expression, tree = self.caosql_expr.normalize_expr(link.expression,
                                                                   module_aliases,
                                                                   anchors={'self': source})
                refs = self.caosql_expr.get_node_references(tree)

                expression = self.caosql_expr.normalize_refs(link.expression, module_aliases)

                first = list(tree.result_types.values())[0][0]

                assert first, "Could not determine computable expression result type"

                if len(tree.result_types) > 1:
                    raise MetaError(('computable expression must yield a '
                                     'single-column result'), link.context)

                if isinstance(source, proto.Link) and not isinstance(first, proto.Atom):
                    raise MetaError(('computable expression for link property must yield a '
                                     'scalar'), link.context)

                link.target = first
                link.expression = expression
                link.is_local = len(refs) == 1 and tuple(refs)[0] is source
                link.is_atom = isinstance(link.target, caos.types.ProtoAtom)

                type = proto.Link if isinstance(source, proto.Concept) else proto.LinkProperty
                parent_link = globalmeta.get(link.normal_name(), type=type, default=None)
                if not parent_link:
                    parent_link = self._create_base_link(link, link.normal_name(),
                                                         globalmeta, localmeta=None, type=type)

                link.base = (parent_link.name,)


    def order_concepts(self, globalmeta):
        g = {}

        for concept in globalmeta('concept', include_builtin=True):
            links = {}
            link_target_types = {}

            for link_name, links in concept.pointers.items():
                for link in links:
                    if not isinstance(link.source, proto.Prototype):
                        link.source = globalmeta.get(link.source)

                    if not isinstance(link, proto.Computable):
                        if not isinstance(link.target, proto.Prototype):
                            link.target = globalmeta.get(link.target)
                            if isinstance(link.target, caos.types.ProtoConcept):
                                link.target.add_rlink(link)

                        if isinstance(link.target, proto.Atom):
                            link.is_atom = True

                            if link_name in link_target_types and link_target_types[link_name] != 'atom':
                                raise caos.MetaError('%s link is already defined as a link to non-atom')

                            constraints = getattr(link, '_constraints', None)
                            if constraints:
                                atom_constraints = [c for c in constraints if isinstance(c, proto.AtomConstraint)]
                            else:
                                atom_constraints = None
                            if atom_constraints:
                                # Got an inline atom definition.
                                atom = self.genatom(globalmeta, concept, link.target.name, link_name,
                                                                                    atom_constraints)
                                globalmeta.add(atom)
                                link.target = atom

                            if link.mapping and link.mapping != caos.types.OneToOne:
                                raise caos.MetaError('%s: links to atoms can only have a "1 to 1" mapping'
                                                     % link_name)

                            link_target_types[link_name] = 'atom'
                        else:
                            if link_name in link_target_types and link_target_types[link_name] == 'atom':
                                raise caos.MetaError('%s link is already defined as a link to atom')

                            link_target_types[link_name] = 'concept'

            g[concept.name] = {"item": concept, "merge": [], "deps": []}
            if concept.base:
                for base_name in concept.base:
                    self._check_base(concept, base_name, globalmeta)
                g[concept.name]["merge"].extend(concept.base)

        concepts = topological.normalize(g, merger=proto.Concept.merge)

        csql_expr = caosql_expr.CaosQLExpression(globalmeta)

        try:
            for concept in concepts:
                for index in concept.indexes:
                    csql_expr.check_source_atomic_expr(index.tree, concept)

                self.normalize_pointer_defaults(concept, globalmeta)
                self.normalize_computables(concept, globalmeta)

        except caosql_exc.CaosQLReferenceError as e:
            raise MetaError(e.args[0], context=index.context) from e

        return concepts


    def genatom(self, meta, host, base, link_name, constraints):
        atom_name = Atom.gen_atom_name(host, link_name)
        atom = proto.Atom(name=caos.Name(name=atom_name, module=host.name.module),
                          base=base, automatic=True, backend=None)
        atom.normalize_constraints(meta, constraints)
        for constraint in constraints:
            atom.add_constraint(constraint)
        return atom


    def items(self):
        return itertools.chain([('_index_', self.finalindex), ('_module_', self.module)])


class EntityShell(LangObject, adapts=caos.concept.EntityShell):
    def __init__(self, data, context):
        super().__init__(data=data, context=context)
        caos.concept.EntityShell.__init__(self)

    def construct(self):
        if isinstance(self.data, str):
            self.id = self.data
        elif isinstance(self.data, dict) and 'query' in self.data:
            query = self.data['query']

            aliases = {alias: mod.__name__ for alias, mod in self.context.document.imports.items()}
            session = self.context.document.session

            cursor = caos_query.CaosQLCursor(session, aliases)
            prepared = cursor.prepare(query)

            output = prepared.describe_output()

            assert len(output) == 1, "query expressions must return a single entity"
            target, is_constant = next(iter(output.values()))

            assert target, "could not determine expression result type: %s" % query

            self.entity = prepared.first()

            assert self.entity, "query returned empty result: %s" % query

        else:
            aliases = {alias: mod.__name__ for alias, mod in self.context.document.imports.items()}
            session = self.context.document.session
            factory = session.realm.getfactory(module_aliases=aliases, session=session)

            concept, data = next(iter(self.data.items()))

            links = {}
            props = {}
            for link_name, linkval in data.items():
                if isinstance(linkval, list):
                    links[link_name] = list()
                    for item in linkval:
                        if isinstance(item, dict):
                            links[link_name].append(item['target'])
                            props[(link_name, item['target'])] = item['properties']
                        else:
                            links[link_name].append(item)
                else:
                    links[link_name] = linkval

            self.entity = factory(concept)(**links)
            for (link_name, target), link_properties in props.items():
                linkcls = caos.concept.getlink(self.entity, link_name, target)
                linkcls.update(**link_properties)

            self.context.document.entities.append(self.entity)


class RealmMeta(LangObject, adapts=proto.RealmMeta):
    @classmethod
    def represent(cls, data):
        result = {'atoms': {}, 'links': {}, 'concepts': {}, 'link-properties': {}}

        for type in ('atom', 'link', 'concept', 'link_property'):
            for obj in data(type=type, include_builtin=False, include_automatic=False):
                # XXX
                if type in ('link', 'link_property') and not obj.generic():
                    continue
                if type == 'link_property':
                    key = 'link-properties'
                else:
                    key = type + 's'

                result[key][str(obj.name)] = obj

        return result


class DataSet(LangObject):
    def construct(self):

        entities = {id: [shell.entity for shell in shells] for id, shells in self.data.items()}
        for entity in self.context.document.entities:
            entity.__class__.materialize_links(entity, entities)


class CaosName(LangObject, adapts=caos.Name, ignore_aliases=True):
    def __new__(cls, context, data):
        return caos.Name.__new__(cls, data)

    @classmethod
    def represent(cls, data):
        return str(data)


class ModuleFromData:
    def __init__(self, name):
        self.__name__ = name


class Backend(backends.MetaBackend):

    def __init__(self, deltarepo, module=None, data=None):
        if module:
            self.metadata = module
        else:
            self.metadata = self.load_from_string(data)

        modhash = persistent_hash(self.metadata._module_)

        repo = deltarepo(module=self.metadata._module_, id=modhash)
        super().__init__(repo)

    def load_from_string(self, data):
        import_context = proto.ImportContext('<string>', toplevel=True)
        module = ModuleFromData('<string>')
        context = lang.meta.DocumentContext(module=module, import_context=import_context)
        for k, v in lang.yaml.Language.load_dict(io.StringIO(data), context):
            setattr(module, str(k), v)

        return module

    def getmeta(self):
        return self.metadata._index_

    def dump_meta(self, meta):
        prologue = '%SCHEMA semantix.caos.backends.yaml.schemas.Semantics\n---\n'
        return prologue + yaml.Language.dump(meta)
