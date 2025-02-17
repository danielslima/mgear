from . import base
from . import exception
from . import datatypes
from maya import cmds
from maya import mel as _mel
from maya.api import OpenMaya
import functools
import inspect
import pprint


__all__ = []
__DO_NOT_CAST_FUNCS = set()
__SCOPE_ATTR_FUNCS = {"listAttr"}


SCOPE_ATTR = 0
SCOPE_NODE = 1
Callback = functools.partial
displayError = OpenMaya.MGlobal.displayError
displayInfo = OpenMaya.MGlobal.displayInfo
displayWarning = OpenMaya.MGlobal.displayWarning
# TODO : None to list


# maybe we need same class of cmds
class _Mel(object):
    __Instance = None

    def __new__(self):
        if _Mel.__Instance is None:
            _Mel.__Instance = super(_Mel, self).__new__(self)
            _Mel.__Instance.__cmds = {}
            _Mel.__Instance.eval = _mel.eval

        return _Mel.__Instance

    def __init__(self):
        super(_Mel, self).__init__()

    def __wrap_mel(self, melcmd, *args):
        argstr = ", ".join([x.__repr__() for x in args])
        return super(_Mel, self).__getattribute__("eval")(
            "{}({})".format(melcmd, argstr)
        )

    def __getattribute__(self, name):
        try:
            return super(_Mel, self).__getattribute__(name)
        except AttributeError:
            cache = super(_Mel, self).__getattribute__("_Mel__cmds")
            if name in cache:
                return cache[name]

            if name == "eval":
                return super(_Mel, self).__getattribute__("eval")

            incmd = getattr(cmds, name, None)
            if incmd is not None:
                cache[name] = _pymaya_cmd_wrap(incmd, wrap_object=False)
                return cache[name]

            res = super(_Mel, self).__getattribute__("eval")(
                "whatIs {}".format(name)
            )
            if res.endswith(".mel"):
                cache[name] = functools.partial(
                    super(_Mel, self).__getattribute__("_Mel__wrap_mel"), name
                )
                return cache[name]

            raise


mel = _Mel()


def exportSelected(*args, **kwargs):
    cmds.file(*args, es=True, **kwargs)


def hasAttr(obj, attr, checkShape=True):
    obj = _obj_to_name(obj)

    has = cmds.attributeQuery(attr, n=obj, ex=True)
    if not has and checkShape:
        shapes = cmds.listRelatives(obj, s=True) or []
        for s in shapes:
            has = cmds.attributeQuery(attr, n=s, ex=True)
            if has:
                break

    return has


def selected(**kwargs):
    return _name_to_obj(cmds.ls(sl=True, **kwargs))


class versions:
    def current():
        return cmds.about(api=True)


def importFile(filepath, **kwargs):
    return _name_to_obj(cmds.file(filepath, i=True, **kwargs))


def sceneName():
    return cmds.file(q=True, sn=True)


class MayaGUIs(object):
    def GraphEditor(self):
        cmds.GraphEditor()


runtime = MayaGUIs()


def confirmBox(title, message, yes="Yes", no="No", *moreButtons, **kwargs):
    ret = cmds.confirmDialog(
        t=title,
        m=message,
        b=[yes, no] + list(moreButtons),
        db=yes,
        ma="center",
        cb=no,
        ds=no,
    )
    if moreButtons:
        return ret
    else:
        return ret == yes


__all__.append("Callback")
__all__.append("displayError")
__all__.append("displayInfo")
__all__.append("displayWarning")
__all__.append("exportSelected")
__all__.append("mel")
__all__.append("hasAttr")
__all__.append("selected")
__all__.append("versions")
__all__.append("importFile")
__all__.append("sceneName")
__all__.append("runtime")
__all__.append("confirmBox")


def _obj_to_name(arg):
    if isinstance(arg, (list, set, tuple)):
        return arg.__class__([_obj_to_name(x) for x in arg])
    elif isinstance(arg, dict):
        newdic = {}
        for k, v in arg.items():
            newdic[k] = _obj_to_name(v)
        return newdic
    elif isinstance(arg, base.Geom):
        return arg.toStringList()
    elif isinstance(arg, base.Base):
        return arg.name()
    else:
        return arg


def _dt_to_value(arg):
    if isinstance(arg, (list, set, tuple)):
        return arg.__class__([_dt_to_value(x) for x in arg])
    elif isinstance(arg, datatypes.Vector):
        return [arg[0], arg[1], arg[2]]
    elif isinstance(arg, datatypes.Point):
        return [arg[0], arg[1], arg[2], arg[3]]
    elif isinstance(arg, datatypes.Matrix):
        return [
            arg[0][0],
            arg[0][1],
            arg[0][2],
            arg[0][3],
            arg[1][0],
            arg[1][1],
            arg[1][2],
            arg[1][3],
            arg[2][0],
            arg[2][1],
            arg[2][2],
            arg[2][3],
            arg[3][0],
            arg[3][1],
            arg[3][2],
            arg[3][3],
        ]
    else:
        return arg


def _name_to_obj(arg, scope=SCOPE_NODE, known_node=None):
    # lazy importing
    from . import bind

    if arg is None:
        return None

    elif isinstance(arg, (list, set, tuple)):
        return arg.__class__(
            [_name_to_obj(x, scope=scope, known_node=known_node) for x in arg]
        )

    elif isinstance(arg, str):
        if scope == SCOPE_ATTR and known_node is not None:
            try:
                return bind.PyNode("{}.{}".format(known_node, arg))
            except:
                return arg
        else:
            try:
                return bind.PyNode(arg)
            except:
                return arg
    else:
        return arg


def _pymaya_cmd_wrap(func, wrap_object=True, scope=SCOPE_NODE):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        args = _obj_to_name(args)
        kwargs = _obj_to_name(kwargs)

        res = func(*args, **kwargs)
        # filter if the function should not return as list
        # Constraints
        if (
            func.__name__.endswith("Constraint")
            and "query" not in kwargs.keys()
        ):
            res = res[0] if res else None

        # Convert None to empty list for list commands
        # NOTE : is it correct?
        if func.__name__.startswith("list") and res is None:
            res = []

        # # NOTE: we can't use a general unwrapping since the return should be
        # # a list depending of the command, for example pm.deformer
        # # New general unwrapping of single-element lists
        # elif (
        #     not func.__name__.startswith("list")
        #     and isinstance(res, list)
        #     and len(res) == 1
        # ):
        #     # Unwrap the single-item list into just the object

        #     print(
        #         f" {func.__name__}: unwrap single item list: {str(res)}, to {str(res[0])}"
        #     )
        #     res = res[0]

        if wrap_object:
            known_node = None
            if scope == SCOPE_ATTR:
                candi = None

                if args:
                    known_node = args[0]
                else:
                    sel = cmds.ls(sl=True)
                    if sel:
                        known_node = sel[0]

                if known_node is not None:
                    if not isinstance(_name_to_obj(known_node), base.Base):
                        known_node = None

            return _name_to_obj(res, scope=scope, known_node=known_node)
        else:
            return res

    return wrapper


def getAttr(*args, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    try:
        res = cmds.getAttr(*args, **kwargs)
    except Exception as e:
        raise exception.MayaAttributeError(*e.args)

    if isinstance(res, list) and len(res) > 0:
        at = cmds.getAttr(args[0], type=True)
        if isinstance(res[0], tuple):
            if at == "pointArray":
                return [datatypes.Vector(x) for x in res]
            elif at == "vectorArray":
                return [datatypes.Point(x) for x in res]

            if at.endswith("3"):
                return datatypes.Vector(res[0])

            return res[0]
        else:
            if at == "vectorArray":
                return [
                    datatypes.Vector(res[i], res[i + 1], res[i + 2])
                    for i in range(0, len(res), 3)
                ]
            elif at == "matrix":
                return datatypes.Matrix(res)

            return res

    return res


def setAttr(*args, **kwargs):
    args = _dt_to_value(_obj_to_name(args))
    kwargs = _obj_to_name(kwargs)

    try:
        fargs = []
        for arg in args:
            if isinstance(arg, (list, set, tuple)):
                fargs.extend(arg)
            else:
                fargs.append(arg)

        if (
            len(fargs) == 2
            and isinstance(fargs[1], str)
            and "typ" not in kwargs
            and "type" not in kwargs
        ):
            kwargs["type"] = "string"

        cmds.setAttr(*fargs, **kwargs)
    except Exception as e:
        raise exception.MayaAttributeError(*e.args)


def currentTime(*args, **kwargs):
    if not args and not kwargs:
        kwargs["query"] = True

    return cmds.currentTime(*args, **kwargs)


def listHistory(*args, type=None, exactType=None, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    res = cmds.listHistory(*args, **kwargs) or []

    if exactType:
        return _name_to_obj([x for x in res if cmds.nodeType(x) == exactType])
    elif type:
        return _name_to_obj(
            [x for x in res if type in cmds.nodeType(x, inherited=True)]
        )
    else:
        return _name_to_obj(res)


def listConnections(*args, sourceFirst=False, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    if sourceFirst:
        # first  list the source connections
        if "source" not in kwargs or not kwargs["source"]:
            kwargs["source"] = True
        if "destination" not in kwargs or kwargs["destination"]:
            kwargs["destination"] = False

        connections = cmds.listConnections(*args, **kwargs) or []
        res_source = [
            (connections[i + 1], connections[i])
            for i in range(0, len(connections), 2)
        ]

        # add the connections from the destination side
        kwargs["source"] = False
        kwargs["destination"] = True

        connections = cmds.listConnections(*args, **kwargs) or []
        res_destination = [
            (connections[i], connections[i + 1])
            for i in range(0, len(connections), 2)
        ]

        res = res_destination + res_source

    else:
        res = cmds.listConnections(*args, **kwargs) or []
    return _name_to_obj(res)


def keyframe(*args, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    t = kwargs.pop("time", kwargs.pop("k", None))
    if t is not None:
        if isinstance(t, (int, float)):
            kwargs["time"] = (t,)
        else:
            kwargs["time"] = t

    return cmds.keyframe(*args, **kwargs)


def cutKey(*args, **kwargs):
    nargs = _obj_to_name(args)
    nkwargs = {}
    for k, v in kwargs.items():
        nkwargs[k] = _obj_to_name(v)

    t = nkwargs.pop("time", nkwargs.pop("k", None))
    if t is not None:
        if isinstance(t, (int, float)):
            nkwargs["time"] = (t,)
        else:
            nkwargs["time"] = t

    return cmds.cutKey(*nargs, **nkwargs)


def bakeResults(*args, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    t = kwargs.pop("t", kwargs.pop("time", None))
    if t is not None:
        if isinstance(t, str) and ":" in t:
            t = tuple([float(x) for x in t.split(":")])
        kwargs["time"] = t

    return cmds.bakeResults(*args, **kwargs)


def sets(*args, **kwargs):
    # from pymel general sets
    _set_set_flags = {
        "subtract",
        "sub",
        "union",
        "un",
        "intersection",
        "int",
        "isIntersecting",
        "ii",
        "isMember",
        "im",
        "split",
        "sp",
        "addElement",
        "add",
        "include",
        "in",
        "remove",
        "rm",
        "forceElement",
        "fe",
    }
    _set_flags = {"copy", "cp", "clear", "cl", "flatten", "fl"}

    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    for flag, value in kwargs.items():
        if flag in _set_set_flags:
            kwargs[flag] = args[0]

            if isinstance(value, (tuple, list, set)):
                args = tuple(value)
            elif isinstance(value, str):
                args = (value,)
            else:
                args = ()
            break
        elif flag in _set_flags:
            kwargs[flag] = args[0]
            args = ()
            break

    return _name_to_obj(cmds.sets(*args, **kwargs))


def disconnectAttr(*args, **kwargs):
    args = _obj_to_name(args)
    kwargs = _obj_to_name(kwargs)

    if len(args) == 1:
        cons = (
            cmds.listConnections(args[0], s=True, d=False, p=True, c=True)
            or []
        )
        for i in range(0, len(cons), 2):
            cmds.disconnectAttr(cons[i + 1], cons[i], **kwargs)
        cons = (
            cmds.listConnections(args[0], s=False, d=True, p=True, c=True)
            or []
        )
        for i in range(0, len(cons), 2):
            cmds.disconnectAttr(cons[i], cons[i + 1], **kwargs)
    else:
        cmds.disconnectAttr(*args, **kwargs)


def curve(*args, **kwargs):
    """
    Creates a NURBS curve

    """
    curve_obj = cmds.curve(*args, **kwargs)

    # Get the actual transform name (handles Maya's auto-renaming)
    transform_name = cmds.ls(curve_obj, long=False)[0]
    # Find the shapes of the curve
    shapes = (
        cmds.listRelatives(transform_name, shapes=True, fullPath=True) or []
    )

    # Rename shapes based on the transform name
    if len(shapes) == 1:
        # Single shape: rename without index
        cmds.rename(
            shapes[0], "{}Shape".format(transform_name.replace("|", ""))
        )
    else:
        # Multiple shapes: rename with index
        for i, shape in enumerate(shapes, start=1):
            shape_name = "{}Shape{}".format(transform_name, i)
            cmds.rename(shape, shape_name.replace("|", ""))
    the_return = _name_to_obj(transform_name)
    return the_return


# set Locals dict

local_dict = locals()

for n, func in inspect.getmembers(cmds, callable):
    if n not in local_dict:
        local_dict[n] = _pymaya_cmd_wrap(
            func,
            wrap_object=(n not in __DO_NOT_CAST_FUNCS),
            scope=SCOPE_ATTR if n in __SCOPE_ATTR_FUNCS else SCOPE_NODE,
        )
    __all__.append(n)
