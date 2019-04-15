import os
import argparse

import interface_parser as parser
import template_instantiator as instantiator

from functools import reduce


class MatlabWrapper(object):
    """ Wrap the given C++ code into Matlab.

    Attributes
        module: the C++ module being wrapped
        module_name: name of the C++ module being wrapped
        top_module_namespace: C++ namespace for the top module (default '')
        ignore_classes: A list of classes to ignore (default [])
    """

    """Map the data type to its Matlab class.
    Found in Argument.cpp in old wrapper
    """
    data_type = {
        'string': 'char', 'char': 'char',
        'unsigned char': 'unsigned char',
        'Vector': 'double', 'Matrix': 'double',
        'int': 'numeric', 'size_t': 'numeric',
        'bool': 'logical'
    }

    """Map the data type into the type used in Matlab methods.
    Found in matlab.h in old wrapper
    """
    data_type_param = {
        'string': 'char', 'char': 'char',
        'unsigned char': 'unsigned char',
        'size_t': 'int', 'int': 'int',
        'double': 'double', 'Vector': 'double', 'Matrix': 'double',
        'bool': 'bool'
    }

    """Methods that should not be wrapped directly"""
    whitelist = ['serializable', 'serialize']

    """Datatypes that do not need to be checked in methods"""
    not_check_type = ['int', 'double', 'bool', 'char', 'unsigned char',
                      'size_t']

    """Ignore the namespace for these datatypes"""
    ignore_namespace = ['Matrix', 'Vector']

    """The amount of times the wrapper has created a call to
    geometry_wrapper
    """
    wrapper_id = 0

    """Map each wrapper id to what its collector function namespace, class,
    type, and string format"""
    wrapper_map = {}

    """Set of all the includes in the namespace"""
    includes = {}

    """Set of all classes in the namespace"""
    classes = []
    classes_elems = {}

    """Id for ordering global functions in the wrapper"""
    global_function_id = 0

    """Files and their content"""
    content = []

    def __init__(self, module, module_name, top_module_namespace='',
                 ignore_classes=[]):
        self.module = module
        self.module_name = module_name
        self.top_module_namespace = top_module_namespace
        self.ignore_classes = ignore_classes

    def _add_include(self, include):
        self.includes[include] = 0

    def _add_class(self, instantiated_class):
        if self.classes_elems.get(instantiated_class) is None:
            self.classes_elems[instantiated_class] = 0
            self.classes.append(instantiated_class)

    def _update_wrapper_id(self, collector_function=None, id_diff=0):
        """Get and define wrapper ids.

        Generates the map of id -> collector function.

        Args:
            collector_function: tuple storing info about the wrapper function
                (namespace, class instance, function type, function name,
                extra)
            id_diff: constant to add to the id in the map

        Returns:
            the current wrapper id
        """
        if collector_function is not None:
            if isinstance(collector_function[1],
                          instantiator.InstantiatedClass):
                function_name = collector_function[0] + \
                    collector_function[1].name + '_' + collector_function[2]
            else:
                function_name = collector_function[1].name

            self.wrapper_map[self.wrapper_id] = (
                collector_function[0],
                collector_function[1],
                collector_function[2],
                function_name + '_' + str(self.wrapper_id + id_diff),
                collector_function[3]
            )

        self.wrapper_id += 1

        return self.wrapper_id - 1

    def _qualified_name(self, names):
        return 'handle' if names == '' else names

    def _insert_spaces(self, x, y):
        """Insert spaces at the beginning of each line

        Args:
            x: the statement currently generated
            y: the addition to add to the statement
        """
        return x + '\n' + ('' if y == '' else '  ') + y

    def _is_ptr(self, arg_type):
        """Determine if the interface_parser.Type should be treated as a
        pointer in the wrapper.
        """
        return arg_type.is_ptr or \
            (arg_type.typename.name not in self.not_check_type and
             arg_type.typename.name not in self.ignore_namespace and
             arg_type.typename.name != 'string')

    def _is_ref(self, arg_type):
        """Determine if the interface_parser.Type should be treated as a
        reference in the wrapper.
        """
        return arg_type.typename.name not in self.ignore_namespace and \
            arg_type.is_ref

    def _group_methods(self, methods):
        """Group overloaded methods together"""
        method_map = {}
        method_out = []

        for method in methods:
            method_index = method_map.get(method.name)

            if method_index is None:
                method_map[method.name] = len(method_out)
                method_out.append([method])
            else:
                method_out[method_index].append(method)

        return method_out

    def _clean_class_name(self, instantiated_class):
        """Reformatted the C++ class name to fit Matlab defined naming
        standards
        """
        if len(instantiated_class.ctors) != 0:
            return instantiated_class.ctors[0].name

        return instantiated_class.cpp_class()

    def _format_type_name(self, type_name, separator='::',
                          include_namespace=True, constructor=False,
                          method=False):
        """
        Args:
            type_name: an interface_parser.Typename to reformat
            separator: the statement to add between namespaces and typename
            include_namespace: whether to include namespaces when reformatting
            constructor: if the typename will be in a constructor
            method: if the typename will be in a method

        Raises:
            constructor and method cannot both be true
        """
        if constructor and method:
            raise Exception(
                'constructor and method parameters cannot both be True')

        formatted_type_name = ''
        name = type_name.name

        if include_namespace:
            for namespace in type_name.namespaces:
                if name not in self.ignore_namespace:
                    formatted_type_name += namespace + separator

        if constructor:
            formatted_type_name += self.data_type.get(name) or name
        elif method:
            formatted_type_name += self.data_type_param.get(name) or name
        else:
            formatted_type_name += name

        return formatted_type_name

    def _format_return_type(self, return_type, include_namespace=False):
        """Format return_type.

        Args:
            return_type: an interface_parser.ReturnType to reformat
            include_namespace: whether to include namespaces when reformatting
        """
        return_wrap = ''

        if self._return_count(return_type) == 1:
            return_wrap = self._format_type_name(
                return_type.type1.typename,
                include_namespace=include_namespace)
        else:
            return_wrap = 'pair< {type1}, {type2} >'.format(
                type1=self._format_type_name(
                    return_type.type1.typename,
                    include_namespace=include_namespace),
                type2=self._format_type_name(
                    return_type.type2.typename,
                    include_namespace=include_namespace))

        return return_wrap

    def _format_class_name(self, instantiated_class, separator=''):
        """Format a template_instantiator.InstantiatedClass name."""
        class_name = instantiated_class.parent.name

        if class_name != '':
            class_name += separator

        class_name += instantiated_class.name

        return class_name

    def _format_static_method(self, static_method, separator=''):
        """Example:

                gtsamPoint3.staticFunction
        """
        method = ''

        if isinstance(static_method, parser.StaticMethod):
            method += static_method.parent.namespaces()[-1] + \
                separator + static_method.parent.name + separator

        return method

    def _wrap_args(self, args):
        """Wrap an interface_parser.ArgumentList into a list of arguments.

        Returns:
            A string representation of the arguments. For example:
                'int x, double y'
        """
        arg_wrap = ''

        for i, arg in enumerate(args.args_list, 1):
            arg_wrap += '{c_type} {arg_name}{comma}'.format(
                c_type=self._format_type_name(
                    arg.ctype.typename, include_namespace=False),
                arg_name=arg.name,
                comma='' if i == len(args.args_list) else ', ')

        return arg_wrap

    def _wrap_variable_arguments(self, args, wrap_datatypes=True):
        """ Wrap an interface_parser.ArgumentList into a statement of argument
        checks.

        Returns:
            A string representation of a variable arguments for an if
            statement. For example:
                ' && isa(varargin{1},'double') && isa(varargin{2},'numeric')'
        """
        var_arg_wrap = ''

        for i, arg in enumerate(args.args_list, 1):
            var_arg_wrap += " && isa(varargin{{{num}}},'{data_type}')".format(
                num=i,
                data_type=self._format_type_name(
                    arg.ctype.typename, separator='.',
                    constructor=not wrap_datatypes
                )
            )

        return var_arg_wrap

    def _wrap_list_variable_arguments(self, args):
        """ Wrap an interface_parser.ArgumentList into a list of argument
        variables.

        Returns:
            A string representation of a list of variable arguments.
            For example:
                'varargin{1}, varargin{2}, varargin{3}'
        """
        var_list_wrap = ''
        first = True

        for i in range(1, len(args.args_list) + 1):
            if first:
                var_list_wrap += 'varargin{{{num}}}'.format(num=i)
                first = False
            else:
                var_list_wrap += ', varargin{{{num}}}'.format(num=i)

        return var_list_wrap

    def _wrap_method_check_statement(self, args):
        """Wrap the given arguments into either just a varargout call or a
        call in an if statement that checks if the parameters are accurate.
        """
        check_statement = ''
        id = 1

        for i, arg in enumerate(args.args_list):
            name = arg.ctype.typename.name

            if name in self.not_check_type:
                continue

            if check_statement == '':
                check_statement = 'if length(varargin) == {param_count}' \
                    .format(param_count=len(args.args_list))

            check_type = self.data_type_param.get(name)

            if check_type is None:
                check_type = self._format_type_name(
                    arg.ctype.typename,
                    separator='.')

            check_statement += " && isa(varargin{{{id}}},'{ctype}')".format(
                id=id,
                ctype=check_type)

            if name == 'Vector':
                check_statement += ' && size(varargin{1},2)==1'

            id += 1

        return check_statement if check_statement == '' else check_statement \
            + '\n'

    def _wrapper_unwrap_arguments(self, args, id=0, constructor=False):
        """Format the interface_parser.Arguments.

        Examples:
            ((a), unsigned char a = unwrap< unsigned char >(in[1]);),
            ((a), Test& t = *unwrap_shared_ptr< Test >(in[1], "ptr_Test");),
            ((a), std::shared_ptr<Test> p1 = unwrap_shared_ptr< Test >(in[1], 
                "ptr_Test");)
        """
        params = ''
        body_args = ''

        for arg in args.args_list:
            if params != '':
                params += ','

            params += arg.name

            if self._is_ref(arg.ctype) and not constructor:
                body_args += '  {ctype}& {name} = *unwrap_shared_ptr< ' \
                    '{ctype} >(in[{id}], "ptr_{ctype_camel}");\n'.format(
                        ctype=self._format_type_name(arg.ctype.typename),
                        ctype_camel=self._format_type_name(
                            arg.ctype.typename, separator=''),
                        name=arg.name,
                        id=id
                    )
            elif self._is_ptr(arg.ctype) and \
                    arg.ctype.typename.name not in self.ignore_namespace:
                body_args += '  std::shared_ptr<{ctype_sep}> {name} = unwrap' \
                    '_shared_ptr< {ctype_sep} >(in[{id}], "ptr_{ctype}");' \
                    '\n'.format(
                        ctype_sep=self._format_type_name(arg.ctype.typename),
                        ctype=self._format_type_name(
                            arg.ctype.typename, separator=''),
                        name=arg.name,
                        id=id
                    )
            else:
                body_args += '  {ctype} {name} = unwrap< {ctype} >(in[' \
                    '{id}]);\n'.format(
                        ctype=arg.ctype.typename.name,
                        name=arg.name,
                        id=id)

            id += 1

        return params, body_args

    def _return_count(self, return_type):
        """The amount of objects returned by the given
        interface_parser.ReturnType.
        """
        return 1 if str(return_type.type2) == '' else 2

    def _wrapper_name(self):
        """Determine the name of wrapper function."""
        return self.module_name + '_wrapper'

    def class_serialize_comment(self, class_name, static_methods):
        """Generate comments for serialize methods."""
        comment_wrap = ''
        static_methods = sorted(static_methods, key=lambda name: name.name)

        for static_method in static_methods:
            if comment_wrap == '':
                comment_wrap = '%-------Static Methods-------\n'

            comment_wrap += '%{name}({args}) : returns {return_type}\n'.format(
                name=static_method.name,
                args=self._wrap_args(static_method.args),
                return_type=self._format_return_type(
                    static_method.return_type,
                    include_namespace=True))

        comment_wrap += '%\n'\
            '%-------Serialization Interface-------\n'\
            '%string_serialize() : returns string\n'\
            '%string_deserialize(string serialized) : returns {class_name}'\
            '\n%\n'.format(class_name=class_name)

        return comment_wrap

    def class_comment(self, instantiated_class):
        """Generate comments for the given class in Matlab.

        Args
            instantiated_class: the class being wrapped
            ctors: a list of the constructors in the class
            methods: a list of the methods in the class
        """
        class_name = instantiated_class.name
        ctors = instantiated_class.ctors
        methods = instantiated_class.methods
        static_methods = instantiated_class.static_methods

        comment = '%class {class_name}, see Doxygen page for details\n'\
            '%at http://research.cc.gatech.edu/borg/sites/edu.borg/html/'\
            'index.html\n'.format(class_name=class_name)

        if len(ctors) != 0:
            comment += '%\n%-------Constructors-------\n'

        # Write constructors
        for ctor in ctors:
            comment += '%{ctor_name}({args})\n'.format(
                ctor_name=ctor.name,
                args=self._wrap_args(ctor.args))

        if len(methods) != 0:
            comment += '%\n'\
                '%-------Methods-------\n'

        methods = sorted(methods, key=lambda name: name.name)

        # Write methods
        for method in methods:
            if method.name in self.whitelist:
                continue

            comment += '%{name}({args})'.format(
                name=method.name,
                args=self._wrap_args(method.args))

            if method.return_type.type2 == '':
                return_type = self._format_type_name(
                    method.return_type.type1.typename)
            else:
                return_type = 'pair< {type1}, {type2} >'.format(
                    type1=self._format_type_name(
                        method.return_type.type1.typename),
                    type2=self._format_type_name(
                        method.return_type.type2.typename))

            comment += ' : returns {return_type}\n'.format(
                return_type=return_type)

        comment += '%\n'

        if len(static_methods) != 0:
            comment += self.class_serialize_comment(class_name, static_methods)

        return comment

    def generate_matlab_wrapper(self):
        """Generate the C++ file for the wrapper."""
        file_name = self._wrapper_name() + '.cpp'

        wrapper_file = '#include <wrap/matlab.h>\n' \
            '#include <map>\n\n'

        return file_name, wrapper_file

    def wrap_method(self, methods):
        """Wrap methods in the body of a class."""
        if not isinstance(methods, list):
            methods = [methods]

        for method in methods:
            output = ''

        return ''

    def wrap_methods(self, methods, globals=False):
        """Wrap a sequence of methods. Groups methods with the same names
        together. If globals is True then output every method into its own
        file.
        """
        output = ''
        methods = self._group_methods(methods)

        for method in methods:
            if globals:
                method_text = self.wrap_global_function(method)
                self.content.append((method[0].name + '.m', method_text))
            else:
                method_text = self.wrap_method(method)
                output += ''

        return output

    def wrap_global_function(self, function):
        """Wrap the given global function."""
        if not isinstance(function, list):
            function = [function]

        m_method = function[0].name

        # Get all combinations of parameters
        param_list = [m.args for m in function]
        param_wrap = ''

        for i, p in enumerate(param_list):
            param_wrap += '      if' if i == 0 else '      elseif'
            param_wrap += ' length(varargin) == '

            if len(p.args_list) == 0:
                param_wrap += '0\n'
            else:
                param_wrap += str(len(p.args_list)) \
                    + self._wrap_variable_arguments(p, False) + '\n'

            param_wrap += '        varargout{{1}} = {module_name}'\
                '_wrapper({num}, varargin{{:}});\n'.format(
                    module_name=self.module_name,
                    num=self._update_wrapper_id(
                        collector_function=(
                            function[0].parent.name,
                            function[i],
                            'global_function',
                            None
                        )
                    )
                )

        param_wrap += '      else\n'\
            "        error('Arguments do not match any overload of function "\
            "{func_name}');".format(func_name=m_method)

        return 'function varargout = {m_method}(varargin)\n'\
            '{statements}\n'\
            '      end\n'.format(m_method=m_method, statements=param_wrap)

    def wrap_class_constructors(self, namespace_name, inst_class, parent_name,
                                ctors, is_virtual):
        """Wrap class constructor.

        Args:
            namespace_name: the name of the namespace ('' if it does not exist)
            inst_class: instance of the class
            parent_name: the name of the parent class if it exists
            ctors: the interface_parser.Constructor in the class
            is_virtual: whether the class is part of a virtual inheritance
                chain
        """
        has_parent = parent_name != ''
        class_name = inst_class.name

        if type(ctors) != list:
            ctors = [ctors]

        methods_wrap = 'methods\n' \
            '  function obj = {class_name}(varargin)\n'.format(
                class_name=class_name)

        if is_virtual:
            methods_wrap += '    if (nargin == 2 || (nargin == 3 && strcmp(' \
                "varargin{3}, 'void')))"
        else:
            methods_wrap += '    if nargin == 2'

        methods_wrap += " && isa(varargin{1}, 'uint64') && varargin{1} == " \
            'uint64(5139824614673773682)\n'

        if is_virtual:
            methods_wrap += '      if nargin == 2\n' \
                '        my_ptr = varargin{{2}};\n' \
                '      else\n' \
                '        my_ptr = {wrapper_name}({id}, varargin{{2}});\n' \
                '      end\n'.format(
                    wrapper_name=self._wrapper_name(),
                    id=self._update_wrapper_id() + 1)
        else:
            methods_wrap += '      my_ptr = varargin{2};\n'

        collector_base_id = self._update_wrapper_id(
            (namespace_name, inst_class, 'collectorInsertAndMakeBase', None),
            id_diff=-1 if is_virtual else 0
        )

        methods_wrap += '      {ptr}{wrapper_name}({id}, my_ptr);\n' \
            .format(ptr='base_ptr = ' if has_parent else '',
                    wrapper_name=self._wrapper_name(),
                    id=collector_base_id - (1 if is_virtual else 0))

        for ctor in ctors:
            wrapper_return = '[ my_ptr, base_ptr ] = ' if has_parent \
                else 'my_ptr = '

            methods_wrap += '    elseif nargin == {len}{varargin}'\
                '\n      {ptr}{wrapper}({num}{comma}{var_arg});\n'.format(
                    len=len(ctor.args.args_list),
                    varargin=self._wrap_variable_arguments(ctor.args, False),
                    ptr=wrapper_return,
                    wrapper=self._wrapper_name(),
                    num=self._update_wrapper_id(
                        (namespace_name, inst_class, 'constructor', ctor)
                    ),
                    comma='' if len(ctor.args.args_list) == 0 else ', ',
                    var_arg=self._wrap_list_variable_arguments(ctor.args))

        base_obj = ''

        if has_parent:
            base_obj = '    obj = obj@{parent_name}(uint64(' \
                '5139824614673773682), base_ptr);\n'.format(
                    parent_name=parent_name)

        methods_wrap += "    else\n" \
            "      error('Arguments do not match any overload of " \
            "{namespace}{d}{class_name} constructor');\n" \
            '    end\n{base_obj}' \
            '    obj.ptr_{namespace}{class_name} = my_ptr;\n' \
            '  end\n\n'.format(
                namespace=namespace_name,
                d='' if namespace_name == '' else '.',
                class_name=class_name,
                base_obj=base_obj)

        return methods_wrap

    def wrap_class_properties(self, class_name):
        """Generate properties of class."""
        return 'properties\n'\
            '  ptr_{class_name} = 0\n'\
            'end\n'.format(class_name=class_name)

    def wrap_class_deconstructor(self, namespace_name, inst_class):
        """Generate the delete function for the Matlab class."""
        class_name = inst_class.name

        methods_text = '  function delete(obj)\n'\
            '    {wrapper}({num}, obj.ptr_{class_name});\n'\
            '  end\n\n'.format(
                num=self._update_wrapper_id(
                    (namespace_name, inst_class, 'deconstructor', None)),
                wrapper=self._wrapper_name(),
                class_name=namespace_name + class_name)

        return methods_text

    def wrap_class_display(self):
        """Generate the display function for the Matlab class."""
        return "  function display(obj), obj.print(''); end\n"\
            '  %DISPLAY Calls print on the object\n'\
            '  function disp(obj), obj.display; end\n'\
            '  %DISP Calls print on the object\n'

    def wrap_class_methods(self, namespace_name, inst_class, methods,
                           serialize=[False]):
        """Wrap the methods in the class.

        Args:
            namespace_name: the name of the class's namespace
            inst_class: the instantiated class whose methods to wrap
            methods: the methods to wrap in the order to wrap them
            serialize: mutable param storing if one of the methods is serialize
        """
        method_text = ''

        for method in methods:
            if method.name in self.whitelist and method.name != 'serialize':
                continue

            if method.name == 'serialize':
                method_text += self.wrap_class_serialize_method(
                    namespace_name, inst_class)
                serialize[0] = True
            else:
                # Generate method code
                method_text += ''\
                    'function varargout = {method_name}(this, varargin)\n'\
                    '  % {caps_name} usage: {method_name}('.format(
                        caps_name=method.name.upper(),
                        method_name=method.name)

                # Determine format of return and varargout statements
                return_type = self._format_return_type(
                    method.return_type, include_namespace=True)

                if self._return_count(method.return_type) == 1:
                    varargout = '' if return_type == 'void' \
                        else 'varargout{1} = '
                else:
                    varargout = '[ varargout{1} varargout{2} ] = '

                check_statement = self._wrap_method_check_statement(
                    method.args)
                class_name = namespace_name + \
                    ('' if namespace_name == '' else '.') + inst_class.name

                end_statement = '' if check_statement == '' else \
                    '  else\n'\
                    "    error('Arguments do not match any overload of "\
                    "function {class_name}.{method_name}');\n" \
                    '  end\n'.format(
                        class_name=class_name,
                        method_name=method.original.name)

                method_text += '{method_args}) : returns {return_type}\n'\
                    '  % Doxygen can be found at http://research.cc.gatech'\
                    '.edu/borg/sites/edu.borg/html/index.html\n'\
                    '  {check_statement}'\
                    '{spacing}{varargout}{wrapper}({num}, this, '\
                    'varargin{{:}});\n{end_statement}'.format(
                        method_args=self._wrap_args(method.args),
                        return_type=return_type,
                        num=self._update_wrapper_id(
                            (namespace_name, inst_class,
                             method.original.name, method)
                        ),
                        check_statement=check_statement,
                        spacing='' if check_statement == '' else '    ',
                        varargout=varargout,
                        wrapper=self._wrapper_name(),
                        end_statement=end_statement)

                method_text += 'end\n\n'

        return method_text

    def wrap_static_methods(self, namespace_name, instantiated_class,
                            serialize):
        class_name = instantiated_class.name

        method_text = 'methods(Static = true)\n'
        static_methods = sorted(
            instantiated_class.static_methods,
            key=lambda name: name.name)

        for static_method in static_methods:
            format_name = list(static_method.name)
            format_name[0] = format_name[0].upper()

            method_text += '  function varargout = {name}(varargin)\n' \
                '    % {name_caps} usage: {name_upper_case}({args}) : ' \
                'returns {return_type}\n' \
                '    % Doxygen can be found at http://research.cc.gatech.edu' \
                '/borg/sites/edu.borg/html/index.html\n' \
                '    varargout{{1}} = {wrapper}({id}, varargin{{:}});\n' \
                '  end\n\n'.format(
                    name=''.join(format_name),
                    name_caps=static_method.name.upper(),
                    name_upper_case=static_method.name,
                    args=self._wrap_args(static_method.args),
                    return_type=self._format_return_type(
                        static_method.return_type,
                        include_namespace=True),
                    length=len(static_method.args.args_list),
                    var_args_list=self._wrap_variable_arguments(
                        static_method.args),
                    wrapper=self._wrapper_name(),
                    id=self._update_wrapper_id((
                        namespace_name, instantiated_class, static_method.name,
                        static_method)),
                    class_name=instantiated_class.name
                )

        if serialize:
            method_text += '  function varargout = string_deserialize'\
                '(varargin)\n'\
                '    % STRING_DESERIALIZE usage: string_deserialize() : '\
                'returns {class_name}\n'\
                '    % Doxygen can be found at http://research.cc.gatech.edu/'\
                'borg/sites/edu.borg/html/index.html\n'\
                '    if length(varargin) == 1\n'\
                '      varargout{{1}} = {wrapper}({id}, varargin{{:}});\n'\
                '    else\n'\
                "      error('Arguments do not match any overload of function"\
                " {class_name}.string_deserialize');\n"\
                '    end\n'\
                '  end\n\n'\
                '  function obj = loadobj(sobj)\n'\
                '    % LOADOBJ Saves the object to a matlab-readable format\n'\
                '    obj = {class_name}.string_deserialize(sobj);\n'\
                '  end\n'.format(
                    class_name=namespace_name + '.' + instantiated_class.name,
                    wrapper=self._wrapper_name(),
                    id=self._update_wrapper_id(
                        (namespace_name, instantiated_class,
                            'string_deserialize', 'deserialize'))
                )

        return method_text

    def wrap_instantiated_class(self, instantiated_class, namespace_name=''):
        """Generate comments and code for given class.

        Args:
            instantiated_class: template_instantiator.InstantiatedClass
                instance storing the class to wrap
            namespace_name: the name of the namespace if there is one
        """
        file_name = self._clean_class_name(instantiated_class)
        namespace_file_name = namespace_name + file_name

        # Class comment
        content_text = self.class_comment(instantiated_class)
        content_text += self.wrap_methods(instantiated_class.methods)

        # Class definition
        content_text += 'classdef {class_name} < {parent}\n'.format(
            class_name=file_name,
            parent=self._qualified_name(instantiated_class.parent_class))

        # Class properties
        content_text += '  ' + reduce(
            self._insert_spaces,
            self.wrap_class_properties(namespace_file_name).splitlines()
        ) + '\n'

        # Class constructor
        content_text += '  ' + reduce(
            self._insert_spaces,
            self.wrap_class_constructors(
                namespace_name,
                instantiated_class,
                instantiated_class.parent_class,
                instantiated_class.ctors,
                instantiated_class.is_virtual,
            ).splitlines()
        ) + '\n'

        # Delete function
        content_text += '  ' + reduce(
            self._insert_spaces,
            self.wrap_class_deconstructor(
                namespace_name, instantiated_class).splitlines()
        ) + '\n'

        # Display function
        content_text += '  ' + reduce(
            self._insert_spaces,
            self.wrap_class_display().splitlines()
        ) + '\n'

        # Class methods
        serialize = [False]

        if len(instantiated_class.methods) != 0:
            methods = sorted(instantiated_class.methods,
                             key=lambda name: name.name)

            content_text += '    ' + reduce(
                lambda x, y: x + '\n' + ('' if y == '' else '    ') + y,
                self.wrap_class_methods(
                    namespace_name,
                    instantiated_class,
                    methods,
                    serialize=serialize)
                .splitlines()
            ) + '\n'

        # Static class methods
        content_text += '  end\n\n  ' + reduce(
            self._insert_spaces,
            self.wrap_static_methods(
                namespace_name,
                instantiated_class,
                serialize[0]).splitlines()
        ) + '\n'

        content_text += '  end\nend\n'

        return file_name + '.m', content_text

    def wrap_namespace(self, namespace):
        """Wrap a namespace by wrapping all of its components.

        Args:
            namespace: the interface_parser.namespace instance of the namespace
        """
        test_output = ''
        namespaces = namespace.full_namespaces()
        inner_namespace = namespace.name != ''
        wrapped = []

        matlab_wrapper = self.generate_matlab_wrapper()
        self.content.append((matlab_wrapper[0], matlab_wrapper[1]))

        current_scope = []
        namespace_scope = []

        for element in namespace.content:
            if isinstance(element, parser.Include):
                self._add_include(element)
            elif isinstance(element, parser.Namespace):
                self.wrap_namespace(element)
            elif isinstance(element, instantiator.InstantiatedClass):
                self._add_class(element)

                if inner_namespace:
                    class_text = self.wrap_instantiated_class(
                        element, namespace.name)

                    namespace_scope.append(
                        (
                            '+' + namespace.name,
                            [(class_text[0], class_text[1])]
                        )
                    )
                else:
                    class_text = self.wrap_instantiated_class(element)
                    current_scope.append((class_text[0], class_text[1]))

        self.content.extend(current_scope)

        if inner_namespace:
            self.content.append(namespace_scope)

        # Global functions
        all_funcs = [
            func for func in namespace.content
            if isinstance(func, parser.GlobalFunction)
        ]

        test_output += self.wrap_methods(all_funcs, True)

        return wrapped

    def wrap_collector_function_shared_return(self, return_type_name,
                                              shared_obj, id, new_line=True):
        new_line = '\n' if new_line else ''

        return '  {{\n' \
            '  Shared{name}* ret = new Shared{name}({shared_obj});\n' \
            '  out[{id}] = wrap_shared_ptr(ret,"{name}");\n' \
            '  }}{new_line}'.format(
                name=self._format_type_name(
                    return_type_name, include_namespace=False),
                shared_obj=shared_obj,
                id=id,
                new_line=new_line
            )

    def wrap_collector_function_return_types(self, return_type, id):
        return_type_text = '  out[' + str(id) + '] = '
        pair_value = 'first' if id == 0 else 'second'
        new_line = '\n' if id == 0 else ''

        if len(return_type.typename.namespaces) > 0:
            namespace = return_type.typename.namespaces[0]
        else:
            namespace = ''

        if self._is_ptr(return_type):
            shared_obj = 'pairResult.' + pair_value

            if not return_type.is_ptr:
                shared_obj = 'Shared{name}(new {formatted_name}({shared_obj}' \
                    '))'.format(
                        name=return_type.typename.name,
                        formatted_name=self._format_type_name(
                            return_type.typename),
                        shared_obj='pairResult.' + pair_value)

            if return_type.typename.name in self.ignore_namespace:
                return_type_text = self.wrap_collector_function_shared_return(
                    return_type.typename,
                    shared_obj,
                    id,
                    True if id == 0 else False)
            else:
                return_type_text += 'wrap_shared_ptr({},"{}", false);'\
                    '{new_line}'.format(
                        shared_obj,
                        self._format_type_name(
                            return_type.typename, separator='.'),
                        new_line=new_line)
        else:
            return_type_text += 'wrap< {} >(pairResult.{});{}'.format(
                self._format_type_name(return_type.typename, separator='.'),
                pair_value,
                new_line)

        return return_type_text

    def wrap_collector_function_return(self, method):
        expanded = ''

        params = self._wrapper_unwrap_arguments(method.args, id=1)[0]

        return_1 = method.return_type.type1
        return_count = self._return_count(method.return_type)
        return_1_name = method.return_type.type1.typename.name
        obj_start = ''

        if isinstance(method, instantiator.InstantiatedMethod):
            method_name = method.original.name
            obj_start = 'obj->'

            if method.instantiation:
                method_name += '<{}>'.format(
                    self._format_type_name(method.instantiation))
        else:
            method_name = self._format_static_method(method, '::')
            method_name += method.name

        obj = '  ' if return_1_name == 'void' else ''
        obj += '{}{}({})'.format(obj_start, method_name, params)

        if return_1_name != 'void':
            if return_count == 1:
                if self._is_ptr(return_1):
                    if return_1.is_ptr:
                        shared_obj = obj
                    else:
                        shared_obj = 'Shared{method_name}(new ' \
                            '{method_name_sep}({obj}))'.format(
                                method_name=return_1.typename.name,
                                method_name_sep=self._format_type_name(
                                    return_1.typename, include_namespace=True),
                                obj=obj)

                    if return_1.typename.name in self.ignore_namespace:
                        expanded += self.wrap_collector_function_shared_return(
                            return_1.typename,
                            shared_obj,
                            0,
                            new_line=False)
                    else:
                        expanded += '  out[0] = wrap_shared_ptr({},"{}", ' \
                            'false);'.format(
                                shared_obj,
                                self._format_type_name(
                                    return_1.typename, separator='.'))
                else:
                    expanded += '  out[0] = wrap< {} >({});'.format(
                        return_1.typename.name,
                        obj)
            elif return_count == 2:
                return_2 = method.return_type.type2

                if self._is_ptr(return_1):
                    if return_1.is_ptr:
                        return_1_typename = 'Shared' + return_1.typename.name
                    else:
                        return_1_typename = self._format_type_name(
                            return_1.typename)
                else:
                    return_1_typename = self._format_type_name(
                        return_1.typename)

                if self._is_ptr(return_2):
                    if return_2.is_ptr:
                        return_2_typename = 'Shared' + return_2.typename.name
                    else:
                        return_2_typename = self._format_type_name(
                            return_2.typename)
                else:
                    return_2_typename = self._format_type_name(
                        return_2.typename)

                expanded += '  pair< {}, {} > pairResult = {};\n'.format(
                    return_1_typename,
                    return_2_typename,
                    obj)

                expanded += self.wrap_collector_function_return_types(
                    return_1, 0)
                expanded += self.wrap_collector_function_return_types(
                    return_2, 1)
        else:
            expanded += obj + ';'

        return expanded

    def wrap_collector_function_upcast_from_void(self, class_name, id):
        return 'void {class_name}_upcastFromVoid_{id}(int nargout, mxArray ' \
            '*out[], int nargin, const mxArray *in[]) {{\n' \
            '  mexAtExit(&_deleteAllObjects);\n' \
            '  typedef std::shared_ptr<{class_name}> Shared;\n' \
            '  std::shared_ptr<void> *asVoid = *reinterpret_cast<std::' \
            'shared_ptr<void>**> (mxGetData(in[0]));\n' \
            '  out[0] = mxCreateNumericMatrix(1, 1, mxUINT32OR64_CLASS, ' \
            'mxREAL);\n' \
            '  Shared *self = new Shared(boost::static_pointer_cast<' \
            '{class_name}>(*asVoid));\n' \
            '  *reinterpret_cast<Shared**>(mxGetData(out[0])) = self;\n' \
            '}}\n\n'.format(class_name=class_name, id=id)

    def generate_collector_function(self, id):
        collector_func = self.wrapper_map.get(id)

        if collector_func is None:
            return ''

        collector_function = 'void {}(int nargout, ' \
            'mxArray *out[], int nargin, const mxArray *in[]' \
            ')\n'.format(collector_func[3])

        if isinstance(collector_func[1], instantiator.InstantiatedClass):
            body = '{\n'

            extra = collector_func[4]

            class_name = collector_func[0] + collector_func[1].name
            class_name_separated = collector_func[0]
            is_method = isinstance(extra, parser.Method)
            is_static_method = isinstance(extra, parser.StaticMethod)

            if class_name_separated != '':
                class_name_separated += '::'
            class_name_separated += collector_func[1].name

            if collector_func[2] == 'collectorInsertAndMakeBase':
                body += '  mexAtExit(&_deleteAllObjects);\n' \
                    '  typedef std::shared_ptr<{class_name_sep}> Shared;\n\n' \
                    '  Shared *self = *reinterpret_cast<Shared**> ' \
                    '(mxGetData(in[0]));\n' \
                    '  collector_{class_name}.insert(self);\n'.format(
                        class_name_sep=class_name_separated,
                        class_name=class_name)

                if collector_func[1].parent_class:
                    body += '\n' \
                        '  typedef std::shared_ptr<{}> SharedBase;\n' \
                        '  out[0] = mxCreateNumericMatrix(1, 1, ' \
                        'mxUINT32OR64_CLASS, mxREAL);\n' \
                        '  *reinterpret_cast<SharedBase**>(mxGetData(out[0])' \
                        ') = new SharedBase(*self);\n'.format(
                            collector_func[1].parent_class)
            elif collector_func[2] == 'constructor':
                params, body_args = self._wrapper_unwrap_arguments(
                    extra.args, constructor=True)
                base = ''

                if collector_func[1].parent_class:
                    base += '\n' \
                        '  typedef std::shared_ptr<{}> SharedBase;\n' \
                        '  out[1] = mxCreateNumericMatrix(1, 1, ' \
                        'mxUINT32OR64_CLASS, mxREAL);\n' \
                        '  *reinterpret_cast<SharedBase**>(mxGetData(out[1])' \
                        ') = new SharedBase(*self);\n'.format(
                            collector_func[1].parent_class)

                body += '  mexAtExit(&_deleteAllObjects);\n' \
                    '  typedef std::shared_ptr<{class_name_sep}> Shared;\n\n' \
                    '{body_args}' \
                    '  Shared *self = new Shared(new {class_name_sep}(' \
                    '{params}));\n' \
                    '  collector_{class_name}.insert(self);\n' \
                    '  out[0] = mxCreateNumericMatrix(1, 1, ' \
                    'mxUINT32OR64_CLASS, mxREAL);\n' \
                    '  *reinterpret_cast<Shared**> (mxGetData(out[0])) = ' \
                    'self;\n' \
                    '{base}'.format(
                        class_name_sep=class_name_separated,
                        body_args=body_args,
                        params=params,
                        class_name=class_name,
                        base=base)
            elif collector_func[2] == 'deconstructor':
                body += '  typedef std::shared_ptr<{class_name_sep}> Shared' \
                    ';\n' \
                    '  checkArguments("delete_{class_name}",nargout,nargin,1' \
                    ');\n' \
                    '  Shared *self = *reinterpret_cast<Shared**>(mxGetData(' \
                    'in[0]));\n' \
                    '  Collector_{class_name}::iterator item;\n' \
                    '  item = collector_{class_name}.find(self);\n' \
                    '  if(item != collector_{class_name}.end()) {{\n' \
                    '    delete self;\n' \
                    '    collector_{class_name}.erase(item);\n' \
                    '  }}\n'.format(
                        class_name_sep=class_name_separated,
                        class_name=class_name)
            elif extra == 'serialize':
                body += self.wrap_collector_function_serialize(
                    collector_func[1].name,
                    namespace=collector_func[0]
                )
            elif extra == 'deserialize':
                body += self.wrap_collector_function_deserialize(
                    collector_func[1].name,
                    namespace=collector_func[0]
                )
            elif is_method or is_static_method:
                method_name = ''

                if is_static_method:
                    method_name = self._format_static_method(extra) + '.'

                method_name += extra.name

                return_type = extra.return_type
                return_count = self._return_count(return_type)

                params, body_args = self._wrapper_unwrap_arguments(
                    extra.args,
                    id=1 if is_method else 0)
                return_body = self.wrap_collector_function_return(extra)

                shared_obj = ''

                if is_method:
                    shared_obj = '  Shared obj = unwrap_shared_ptr<' \
                        '{class_name_sep}>(in[0], "ptr_{class_name}");' \
                        '\n'.format(
                            class_name_sep=class_name_separated,
                            class_name=class_name)

                if return_count >= 1:
                    return_1 = return_type.type1

                    if return_1.typename.name != 'void' and \
                            self._is_ptr(return_1) and return_1.typename.name \
                            not in self.ignore_namespace:
                        body += '  typedef std::shared_ptr<{}> {}\n'.format(
                            self._format_type_name(return_1.typename),
                            'Shared' + return_1.typename.name + ';')

                if return_count == 2:
                    return_2 = return_type.type2

                    if self._is_ptr(return_2) and return_2.typename.name \
                            not in self.ignore_namespace:
                        body += '  typedef std::shared_ptr<{}> {}\n'.format(
                            self._format_type_name(return_2.typename),
                            'Shared' + return_2.typename.name + ';')

                body += '  typedef std::shared_ptr<{class_name_sep}> Shared;' \
                    '\n' \
                    '  checkArguments("{method_name}",nargout,nargin{min1},' \
                    '{num_args});\n' \
                    '{shared_obj}' \
                    '{body_args}' \
                    '{return_body}\n'.format(
                        class_name_sep=class_name_separated,
                        min1='-1' if is_method else '',
                        shared_obj=shared_obj,
                        method_name=method_name,
                        num_args=len(extra.args.args_list),
                        class_name=class_name,
                        body_args=body_args,
                        return_body=return_body)

            body += '}\n'

            if extra not in ['serialize', 'deserialize']:
                body += '\n'

            collector_function += body
        else:
            body = '{{\n' \
                '  checkArguments("{function_name}",nargout,nargin,{id});\n' \
                ''.format(
                    function_name=collector_func[1].name,
                    id=self.global_function_id
                )

            body += self._wrapper_unwrap_arguments(collector_func[1].args)[1]
            body += self.wrap_collector_function_return(
                collector_func[1]) + '\n}\n'

            collector_function += body

            self.global_function_id += 1

        return collector_function

    def mex_function(self):
        cases = ''
        next_case = None

        for id in range(self.wrapper_id):
            id_val = self.wrapper_map.get(id)
            set_next_case = False

            if id_val is None:
                id_val = self.wrapper_map.get(id + 1)

                if id_val is None:
                    continue

                set_next_case = True

            cases += '    case {}:\n' \
                '      {}(nargout, out, nargin-1, in+1);\n' \
                '      break;\n'.format(
                    id,
                    next_case if next_case else id_val[3])

            if set_next_case:
                next_case = id_val[1].name + \
                    '_upcastFromVoid' + '_' + str(id + 1)
            else:
                next_case = None

        return '\nvoid mexFunction(int nargout, mxArray *out[], int nargin, ' \
            'const mxArray *in[])\n{{\n' \
            '  mstream mout;\n' \
            '  std::streambuf *outbuf = std::cout.rdbuf(&mout);\n\n' \
            '  _{module_name}_RTTIRegister();\n\n' \
            '  int id = unwrap<int>(in[0]);\n\n' \
            '  try {{\n' \
            '    switch(id) {{\n' \
            '{cases}' \
            '    }}\n' \
            '  }} catch(const std::exception& e) {{\n' \
            '    mexErrMsgTxt(("Exception from gtsam:\\n" + std::string(' \
            'e.what()) + "\\n").c_str());\n' \
            '  }}\n\n' \
            '  std::cout.rdbuf(outbuf);\n' \
            '}}\n'.format(module_name=self.module_name, cases=cases)

    def generate_wrapper(self, namespace):
        """Generate the c++ wrapper."""
        # Includes
        wrapper_file = '#include <wrap/matlab.h>\n' \
            '#include <map>\n\n' \
            '#include <boost/archive/text_iarchive.hpp>\n' \
            '#include <boost/archive/text_oarchive.hpp>\n' \
            '#include <boost/serialization/export.hpp>\n\n'

        includes_list = sorted(
            list(self.includes.keys()),
            key=lambda include: include.header)

        wrapper_file += reduce(
            lambda x, y: str(x) + '\n' + str(y),
            includes_list) + '\n'

        typedef_instances = '\n'
        typedef_collectors = ''
        boost_class_export_guid = ''
        delete_objs = 'void _deleteAllObjects()\n' \
            '{\n' \
            '  mstream mout;\n' \
            '  std::streambuf *outbuf = std::cout.rdbuf(&mout);\n\n' \
            '  bool anyDeleted = false;\n'
        rtti_reg_start = 'void _geometry_RTTIRegister() {{\n' \
            '  const mxArray *alreadyCreated = mexGetVariablePtr("global", ' \
            '"gtsam_{module_name}_rttiRegistry_created");\n' \
            '  if(!alreadyCreated) {{\n' \
            '    std::map<std::string, std::string> types;\n'.format(
                module_name=self.module_name)
        rtti_reg_mid = ''
        rtti_reg_end = '\n    mxArray *registry = mexGetVariable(' \
            '"global", "gtsamwrap_rttiRegistry");\n' \
            '    if(!registry)\n' \
            '      registry = mxCreateStructMatrix(1, 1, 0, NULL);\n' \
            '    typedef std::pair<std::string, std::string> StringPair;\n' \
            '    for(const StringPair& rtti_matlab: types) {\n' \
            '      int fieldId = mxAddField(registry, rtti_matlab.first.' \
            'c_str());\n' \
            '      if(fieldId < 0)\n' \
            '        mexErrMsgTxt("gtsam wrap:  Error indexing RTTI types, ' \
            'inheritance will not work correctly");\n' \
            '      mxArray *matlabName = mxCreateString(rtti_matlab.second.' \
            'c_str());\n' \
            '      mxSetFieldByNumber(registry, 0, fieldId, matlabName);\n' \
            '    }\n' \
            '    if(mexPutVariable("global", "gtsamwrap_rttiRegistry", ' \
            'registry) != 0)\n' \
            '      mexErrMsgTxt("gtsam wrap:  Error indexing RTTI types, ' \
            'inheritance will not work correctly");\n' \
            '    mxDestroyArray(registry);\n    \n' \
            '    mxArray *newAlreadyCreated = mxCreateNumericMatrix(0, 0, ' \
            'mxINT8_CLASS, mxREAL);\n' \
            '    if(mexPutVariable("global", "gtsam_geometry_rttiRegistry_' \
            'created", newAlreadyCreated) != 0)\n' \
            '      mexErrMsgTxt("gtsam wrap:  Error indexing RTTI types, ' \
            'inheritance will not work correctly");\n' \
            '    mxDestroyArray(newAlreadyCreated);\n' \
            '  }\n}\n'
        ptr_ctor_frag = ''

        for cls in self.classes:
            if len(cls.instantiations):
                cls_insts = ''

                for i, inst in enumerate(cls.instantiations):
                    if i != 0:
                        cls_insts += ', '

                    cls_insts += self._format_type_name(inst)

                typedef_instances += 'typedef {original_class_name}<' \
                    '{class_inst}> {class_name};\n'.format(
                        original_class_name=cls.original.name,
                        class_inst=cls_insts,
                        class_name=cls.name
                    )

            class_name = self._format_class_name(cls, '::')
            className = self._format_class_name(cls)

            if len(cls.original.namespaces()) > 1:
                boost_class_export_guid += 'BOOST_CLASS_EXPORT_GUID({}, ' \
                    '"{}");\n'.format(class_name, className)

            typedef_collectors += 'typedef std::set<std::shared_ptr' \
                '<{class_name}>*' \
                '> Collector_{className};\n' \
                'static Collector_{className} collector_{className};\n'.format(
                    class_name=class_name,
                    className=className
                )
            delete_objs += '  {{ for(Collector_{className}::iterator iter = ' \
                'collector_{className}.begin();\n' \
                '      iter != collector_{className}.end(); ) {{\n' \
                '    delete *iter;\n' \
                '    collector_{className}.erase(iter++);\n' \
                '    anyDeleted = true;\n' \
                '  }} }}\n'.format(className=className)

            if cls.is_virtual:
                rtti_reg_mid += '    types.insert(std::make_pair(typeid({}).'\
                    'name(), "{}"));\n'.format(class_name, className)

        set_next_case = False

        for id in range(self.wrapper_id):
            id_val = self.wrapper_map.get(id)
            queue_set_next_case = set_next_case

            set_next_case = False

            if id_val is None:
                id_val = self.wrapper_map.get(id + 1)

                if id_val is None:
                    continue

                set_next_case = True

            ptr_ctor_frag += self.generate_collector_function(id)

            if queue_set_next_case:
                ptr_ctor_frag += self.wrap_collector_function_upcast_from_void(
                    id_val[1].name,
                    id
                )

        wrapper_file += \
            '{typedef_instances}\n' \
            '{boost_class_export_guid}\n' \
            '{typedefs_collectors}\n' \
            '{delete_objs}' \
            '  if(anyDeleted)\n' \
            '    cout <<\n' \
            '      "WARNING:  Wrap modules with variables in the workspace ' \
            'have been reloaded due to\\n"\n' \
            '      "calling destructors, call \'clear all\' again if you ' \
            'plan to now recompile a wrap\\n"\n' \
            '      "module, so that your recompiled module is used instead ' \
            'of the old one." << endl;\n' \
            '  std::cout.rdbuf(outbuf);\n' \
            '}}\n\n' \
            '{rtti_register}\n' \
            '{pointer_contstructor_fragment}' \
            '{mex_function}'.format(
                typedef_instances=typedef_instances,
                boost_class_export_guid=boost_class_export_guid,
                typedefs_collectors=typedef_collectors,
                delete_objs=delete_objs,
                rtti_register=rtti_reg_start + rtti_reg_mid + rtti_reg_end,
                pointer_contstructor_fragment=ptr_ctor_frag,
                mex_function=self.mex_function()
            )

        self.content.append((self._wrapper_name() + '.cpp', wrapper_file))

    def wrap_class_serialize_method(self, namespace_name, inst_class):
        class_name = inst_class.name

        return 'function varargout = string_serialize(this, varargin)\n'\
            '  % STRING_SERIALIZE usage: string_serialize() : returns '\
            'string\n'\
            '  % Doxygen can be found at '\
            'http://research.cc.gatech.edu/borg/sites/edu.borg/html/index.html\n'\
            '  if length(varargin) == 0\n'\
            '    varargout{{1}} = {wrapper}({id}, this, '\
            'varargin{{:}});\n'\
            '  else\n'\
            "    error('Arguments do not match any overload of function "\
            "{class_name}.string_serialize');\n"\
            '  end\nend\n\n'\
            'function sobj = saveobj(obj)\n'\
            '  % SAVEOBJ Saves the object to a matlab-readable format\n'\
            '  sobj = obj.string_serialize();\nend\n'.format(
                wrapper=self._wrapper_name(),
                id=self._update_wrapper_id(
                    (namespace_name, inst_class, 'string_serialize',
                     'serialize')),
                class_name=namespace_name + '.' + class_name)

    def wrap_collector_function_serialize(self, class_name, namespace=''):
        return \
            '  typedef std::shared_ptr<{namespace}::{class_name}> Shared;\n' \
            '  checkArguments("string_serialize",nargout,nargin-1,0);\n' \
            '  Shared obj = unwrap_shared_ptr<gtsam::Point3>(in[0], ' \
            '"ptr_{namespace}{class_name}");\n' \
            '  ostringstream out_archive_stream;\n' \
            '  boost::archive::text_oarchive out_archive(out_archive_stream)' \
            ';\n' \
            '  out_archive << *obj;\n' \
            '  out[0] = wrap< string >(out_archive_stream.str());\n'.format(
                class_name=class_name,
                namespace=namespace)

    def wrap_collector_function_deserialize(self, class_name, namespace=''):
        return \
            '  typedef std::shared_ptr<{namespace}::{class_name}> Shared;\n' \
            '  checkArguments("{namespace}{class_name}.string_deserialize",' \
            'nargout,nargin,1);\n' \
            '  string serialized = unwrap< string >(in[0]);\n' \
            '  istringstream in_archive_stream(serialized);\n' \
            '  boost::archive::text_iarchive in_archive(in_archive_stream);' \
            '\n' \
            '  Shared output(new {namespace}::{class_name}());\n' \
            '  in_archive >> *output;\n' \
            '  out[0] = wrap_shared_ptr(output,"{namespace}.{class_name}", ' \
            'false);\n'.format(class_name=class_name, namespace=namespace)

    def wrap(self):
        self.wrap_namespace(self.module)
        self.generate_wrapper(self.module)

        return self.content


def _generate_content(cc_content, path):
    """Generate files and folders from matlab wrapper content.

    Keyword arguments:
    cc_content -- the content to generate formatted as
        (file_name, file_content) or
        (folder_name, [(file_name, file_content)])
    path -- the path to the files parent folder within the main folder
    """
    for c in cc_content:
        if type(c) == list:
            path_to_folder = path + '/' + c[0][0]

            if not os.path.isdir(path_to_folder):
                try:
                    os.mkdir(path_to_folder)
                except OSError:
                    pass

            for sub_content in c:
                _generate_content(sub_content[1], path_to_folder)
        else:
            path_to_file = path + '/' + c[0]

            if not os.path.isdir(path_to_file):
                try:
                    os.mkdir(path)
                except OSError:
                    pass

            with open(path_to_file, 'w') as f:
                f.write(c[1])


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    arg_parser.add_argument(
        "--src", type=str, required=True, help="Input interface .h file.")
    arg_parser.add_argument(
        "--module_name",
        type=str,
        required=True,
        help="Name of the C++ class being wrapped.")
    arg_parser.add_argument(
        "--out",
        type=str,
        required=True,
        help="Name of the output folder.")
    arg_parser.add_argument(
        "--top_module_namespaces",
        type=str,
        default="",
        help="C++ namespace for the top module, e.g. `ns1::ns2::ns3`. "
        "Only the content within this namespace and its sub-namespaces "
        "will be wrapped. The content of this namespace will be available at "
        "the top module level, and its sub-namespaces' in the submodules.\n"
        "For example, `import <module_name>` gives you access to a Python "
        "`<module_name>.Class` of the corresponding C++ `ns1::ns2::ns3::Class`"
        ", and `from <module_name> import ns4` gives you access to a Python "
        "`ns4.Class` of the C++ `ns1::ns2::ns3::ns4::Class`. ")
    arg_parser.add_argument(
        "--ignore",
        nargs='*',
        type=str,
        help="A space-separated list of classes to ignore. "
        "Class names must include their full namespaces.")
    args = arg_parser.parse_args()

    top_module_namespaces = args.top_module_namespaces.split("::")
    if top_module_namespaces[0]:
        top_module_namespaces = [''] + top_module_namespaces

    with open(args.src, 'r') as f:
        content = f.read()

    if not os.path.exists(args.src):
        os.mkdir(args.src)

    module = parser.Module.parseString(content)

    instantiator.instantiate_namespace_inplace(module)

    wrapper = MatlabWrapper(
        module=module,
        module_name=args.module_name,
        top_module_namespace=top_module_namespaces,
        ignore_classes=args.ignore
    )

    cc_content = wrapper.wrap()

    _generate_content(cc_content, args.out)
