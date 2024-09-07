import re
from maya.api import OpenMaya
from maya.api import OpenMayaAnim
from maya import cmds
from . import cmd
from . import attr
from . import base
from . import datatypes
from . import exception
from . import geometry
from . import util
from functools import partial


RE_ATTR_INDEX = re.compile("\[([0-9]+)\]")


def _getPivots(node, **kwargs):
    kwargs.pop("pivots", kwargs.pop("piv", None))
    kwargs["pivots"] = True
    kwargs["q"] = True
    res = cmd.xform(node, **kwargs)
    return (datatypes.Vector(res[:3]), datatypes.Vector(res[3:]))


def _setTransformation(node, matrix):
    """Sets the transformation of the node using the provided matrix.

    Args:
        node: The node whose transformation will be set.
        matrix: Can be either an OpenMaya.MMatrix, OpenMaya.MTransformationMatrix,
                or a list of lists representing a 4x4 transformation matrix.
    """

    # If the matrix is a list of lists, convert it to OpenMaya.MMatrix
    if isinstance(matrix, list):
        # Ensure it's a 4x4 matrix (list of 4 lists, each with 4 elements)
        if len(matrix) == 4 and all(len(row) == 4 for row in matrix):
            flat_matrix = [elem for row in matrix for elem in row]
            m_matrix = OpenMaya.MMatrix(flat_matrix)
            matrix = m_matrix
        else:
            raise ValueError("Matrix must be a 4x4 list of lists.")

    # If the matrix is MMatrix, convert it to MTransformationMatrix
    if isinstance(matrix, OpenMaya.MMatrix):
        matrix = OpenMaya.MTransformationMatrix(matrix)

    # Apply the transformation to the node
    OpenMaya.MFnTransform(node.dagPath()).setTransformation(matrix)


def _getTransformation(node):
    return datatypes.TransformationMatrix(
        OpenMaya.MFnTransform(node.dagPath()).transformationMatrix()
    )


def _getShape(node, **kwargs):
    shapes = node.getShapes(**kwargs)
    if shapes:
        return shapes[0]

    return None


def _getShapes(node, **kwargs):
    kwargs.pop("shapes", kwargs.pop("s", None))
    kwargs["shapes"] = True
    return cmd.listRelatives(node, **kwargs)


def _getParent(node, generations=1):
    if generations == 1:
        res = cmd.listRelatives(node, p=True)
        if res:
            return res[0]

        return None
    else:
        splt = [x for x in node.dagPath().fullPathName().split("|") if x]
        spltlen = len(splt)
        if generations >= 0:
            if generations >= spltlen:
                return None

            return BindNode("|" + "|".join(splt[: spltlen - generations]))
        else:
            if abs(generations) > spltlen:
                return None

            return BindNode("|" + "|".join(splt[:-generations]))


def _getChildren(node, **kwargs):
    kwargs["c"] = True
    return cmd.listRelatives(node, **kwargs)


def _addChild(node, child, **kwargs):
    return cmd.parent(child, node, **kwargs)


def _setMatrix(node, val, **kwargs):
    kwargs.pop("m", kwargs.pop("matrix", None))
    kwargs["m"] = cmd._dt_to_value(val)
    cmd.xform(node, **kwargs)


def _getMatrix(node, **kwargs):
    kwargs.pop("m", kwargs.pop("matrix", None))
    kwargs.pop("q", kwargs.pop("query", None))
    kwargs.update({"q": True, "m": True})

    return datatypes.Matrix(cmd.xform(node, **kwargs))


def _getTranslation(node, space="object"):
    space = util.to_mspace(space)
    return datatypes.Vector(
        OpenMaya.MFnTransform(node.dagPath()).translation(space)
    )


def _setTranslation(node, value, space="object", **kwargs):
    if kwargs.pop("worldSpace", kwargs.pop("ws", False)):
        space = "world"
    elif kwargs.pop("objectSpace", kwargs.pop("os", False)):
        space = "object"

    space = util.to_mspace(space)
    OpenMaya.MFnTransform(node.dagPath()).setTranslation(value, space)


def _getRotation(node, space="object", quaternion=False, **kwargs):
    space = util.to_mspace(space)
    res = OpenMaya.MFnTransform(node.dagPath()).rotation(
        space=space, asQuaternion=True
    )

    if quaternion:
        return datatypes.Quaternion(res)
    else:
        return datatypes.EulerRotation(res.asEulerRotation())


def _setRotation(node, rotation, space="object"):
    if isinstance(rotation, list):
        if len(rotation) == 3:
            rotation = datatypes.EulerRotation(
                *[math.radians(x) for x in rotation]
            )
        elif len(rotation) == 4:
            rotation = datatypes.Quaternion(*rotation)

    if isinstance(rotation, OpenMaya.MEulerRotation):
        rotation = rotation.asQuaternion()

    space = util.to_mspace(space)
    OpenMaya.MFnTransform(node.dagPath()).setRotation(rotation, space)


def _setScale(node, scale):
    OpenMaya.MFnTransform(node.dagPath()).setScale(scale)


def _getScale(node):
    return OpenMaya.MFnTransform(node.dagPath()).scale()


def _getBoundingBox(node, invisible=False, space="object"):
    opts = {"query": True}
    if invisible:
        opts["boundingBoxInvisible"] = True
    else:
        opts["boundingBox"] = True

    if space == "object":
        opts["objectSpace"] = True
    elif space == "world":
        opts["worldSpace"] = True
    else:
        raise Exception("unknown space '{}'".format(space))

    res = cmd.xform(node, **opts)

    return datatypes.BoundingBox(res[:3], res[3:])


class _Node(base.Node):
    __selection_list = OpenMaya.MSelectionList()

    @staticmethod
    def __getObjectFromName(nodename):
        _Node.__selection_list.clear()
        try:
            _Node.__selection_list.add(nodename)
        except RuntimeError as e:
            return None

        return _Node.__selection_list.getDependNode(0)

    def __hash__(self):
        return hash(self.name())

    def __init__(self, nodename_or_mobject):
        super(_Node, self).__init__()
        self.__attrs = {}
        self.__api_mfn = None

        if isinstance(nodename_or_mobject, OpenMaya.MObject):
            self.__obj = nodename_or_mobject
        else:
            self.__obj = _Node.__getObjectFromName(nodename_or_mobject)
            if self.__obj is None:
                raise exception.MayaNodeError(
                    "No such node '{}'".format(nodename_or_mobject)
                )

        if not self.__obj.hasFn(OpenMaya.MFn.kDependencyNode):
            raise exception.MayaNodeError(
                "Not a dependency node '{}'".format(nodename_or_mobject)
            )

        self.__fn_dg = OpenMaya.MFnDependencyNode(self.__obj)
        self.__api_mfn = self.__fn_dg
        self.__is_transform = False

        if self.__obj.hasFn(OpenMaya.MFn.kDagNode):
            dagpath = OpenMaya.MDagPath.getAPathTo(self.__obj)
            self.__dagpath = dagpath
            self.__fn_dag = OpenMaya.MFnDagNode(dagpath)
            self.getParent = partial(_getParent, self)
            self.getChildren = partial(_getChildren, self)
            self.addChild = partial(_addChild, self)
            if self.__obj.hasFn(OpenMaya.MFn.kTransform):
                self.__is_transform = True
                self.getBoundingBox = partial(_getBoundingBox, self)
                self.getPivots = partial(_getPivots, self)
                self.setTransformation = partial(_setTransformation, self)
                self.getTransformation = partial(_getTransformation, self)
                self.getShape = partial(_getShape, self)
                self.getShapes = partial(_getShapes, self)
                self.setMatrix = partial(_setMatrix, self)
                self.getMatrix = partial(_getMatrix, self)
                self.getTranslation = partial(_getTranslation, self)
                self.setTranslation = partial(_setTranslation, self)
                self.setRotation = partial(_setRotation, self)
                self.getRotation = partial(_getRotation, self)
                self.getScale = partial(_getScale, self)
                self.setScale = partial(_setScale, self)

            self.__api_mfn = self.__fn_dag
        else:
            self.__dagpath = None
            self.__fn_dag = None

    def __getattribute__(self, name):
        try:
            return super(_Node, self).__getattribute__(name)
        except AttributeError:
            nfnc = super(_Node, self).__getattribute__("name")
            if cmds.ls("{}.{}".format(nfnc(), name)):
                return super(_Node, self).__getattribute__("attr")(name)
            elif cmds.ls("{}.{}[:]".format(nfnc(), name)):
                return geometry.BindGeometry("{}.{}[:]".format(nfnc(), name))
            elif self.__is_transform:
                sp = self.getShape()
                if sp:
                    sym = getattr(sp, name, None)
                    if sym:
                        return sym

            raise

    def __eq__(self, other):
        if isinstance(other, str):
            other = _Node(other)
        return self.__obj == other.__obj

    def __ne__(self, other):
        if isinstance(other, str):
            other = _Node(other)
        return self.__obj != other.__obj

    def object(self):
        return self.__obj

    def dgFn(self):
        return self.__fn_dg

    def dagFn(self):
        return self.__fn_dag

    def dagPath(self):
        return self.__dagpath

    def isDag(self):
        return self.__fn_dag is not None

    def __apimfn__(self):
        return self.__api_mfn

    def name(self, long=False):
        fdag = super(_Node, self).__getattribute__("_Node__fn_dag")
        if fdag is not None:
            return fdag.partialPathName() if not long else fdag.fullPathName()
        fdg = super(_Node, self).__getattribute__("_Node__fn_dg")
        return fdg.name()

    def longName(self):
        fdag = super(_Node, self).__getattribute__("_Node__fn_dag")
        if fdag is not None:
            return fdag.fullPathName()
        fdg = super(_Node, self).__getattribute__("_Node__fn_dg")
        return fdg.name()

    def shortName(self):
        """Return the short name of the node."""
        fdag = super(_Node, self).__getattribute__("_Node__fn_dag")
        if fdag is not None:
            return fdag.partialPathName()
        fdg = super(_Node, self).__getattribute__("_Node__fn_dg")
        return fdg.name().split("|")[-1]

    def namespace(self, **kwargs):
        n = self.name()
        if ":" not in n:
            return ""
        return ":".join(n.split("|")[-1].split(":")[:-1]) + ":"

    def stripNamespace(self):
        return "|".join([x.split(":")[-1] for x in self.name().split("|")])

    def attr(self, name, checkShape=True):
        attr_cache = super(_Node, self).__getattribute__("_Node__attrs")
        if name in attr_cache:
            return attr_cache[name]

        p = None
        idx = None
        attrname = name
        idre = RE_ATTR_INDEX.search(name)
        if idre:
            attrname = name[: idre.start()]
            idx = int(idre.group(1))

        fn_dg = super(_Node, self).__getattribute__("_Node__fn_dg")
        try:
            p = fn_dg.findPlug(attrname, False)
        except Exception:
            if checkShape:
                get_shape = super(_Node, self).__getattribute__("getShape")
                shape = get_shape()
                if shape:
                    try:
                        p = shape.dgFn().findPlug(attrname, False)
                    except:
                        pass

            if p is None:
                raise exception.MayaAttributeError(
                    "No '{}' attr found".format(name)
                )

        at = attr.Attribute(p)
        if idx is not None:
            at = at[idx]

        attr_cache[name] = at
        return at

    def addAttr(self, name, **kwargs):
        kwargs.pop("ln", None)
        kwargs.pop("longName", None)
        kwargs["longName"] = name
        return cmd.addAttr(self.name(), **kwargs)

    def getAttr(self, name, **kwargs):
        return cmd.getAttr("{}.{}".format(self.name(), name), **kwargs)

    def setAttr(self, name, *args, **kwargs):
        return cmd.setAttr("{}.{}".format(self.name(), name), *args, **kwargs)

    def hasAttr(self, name, checkShape=True):
        return cmds.objExists("{}.{}".format(self.name(), name))

    def listAttr(self, **kwargs):
        return cmd.listAttr(**kwargs)

    def listConnections(self, **kwargs):
        return cmd.listConnections(self, **kwargs)

    def listRelatives(self, **kwargs):
        return cmd.listRelatives(self, **kwargs)

    def type(self):
        return self.__fn_dg.typeName

    # same????
    def nodeType(self):
        return self.__fn_dg.typeName

    def namespace(self):
        nss = self.name().split("|")[-1].split(":")[:-1]
        if not nss:
            return ""

        return ":".join(nss) + ":"

    def node(self):
        return self

    def rename(self, name):
        return cmds.rename(self.name(), name)

    def startswith(self, word):
        return self.name().startswith(word)

    def endswith(self, word):
        return self.name().endswith(word)

    def replace(self, old, new):
        return self.name().replace(old, new)

    def split(self, word):
        return self.name().split(word)


class _NodeTypes(object):
    __Instance = None

    def __new__(self):
        if _NodeTypes.__Instance is None:
            _NodeTypes.__Instance = super(_NodeTypes, self).__new__(self)
            _NodeTypes.__Instance.__types = {}

        return _NodeTypes.__Instance

    def registerClass(self, typename, cls=None):
        if cls is not None:
            self.__types[typename] = cls
        else:
            clsname = "{}{}".format(typename[0].upper(), typename[1:])

            class _New(_Node):
                def __repr__(self):
                    return "{}('{}')".format(clsname, self.name())

            _New.__name__ = clsname
            self.__types[typename] = _New

    def getTypeClass(self, typename):
        self_types = super(_NodeTypes, self).__getattribute__(
            "_NodeTypes__types"
        )
        if typename in self_types:
            return self_types[typename]

        if typename in cmds.allNodeTypes():
            self.registerClass(typename, cls=None)
            return self_types[typename]

        return None

    def __getattribute__(self, name):
        try:
            return super(_NodeTypes, self).__getattribute__(name)
        except AttributeError:
            tcls = super(_NodeTypes, self).__getattribute__("getTypeClass")(
                "{}{}".format(name[0].lower(), name[1:])
            )
            if tcls:
                return tcls

            raise

    def __init__(self):
        super(_NodeTypes, self).__init__()


nt = _NodeTypes()


class SoftMod(_Node):
    def __init__(self, nodename_or_mobject):
        super(SoftMod, self).__init__(nodename_or_mobject)

    def getGeometry(self):
        # pymel returns str list
        return cmds.softMod(self.name(), g=True, q=True)


nt.registerClass("softMod", cls=SoftMod)


class ObjectSet(_Node):
    def __init__(self, nodename_or_mobject):
        super(ObjectSet, self).__init__(nodename_or_mobject)

    def members(self):
        """
        Return the members of the set.

        Returns:
            list: A list of members in the set, or an empty list if no members exist.
        """
        return cmds.sets(self, q=True) or []

    def union(self, *other_sets):
        """
        Perform a union of this set with other sets and return a new ObjectSet.

        Args:
            other_sets (ObjectSet): One or more ObjectSet instances to union with.

        Returns:
            ObjectSet: A new ObjectSet containing the union of the members.
        """
        # Gather the members of the current set
        members = set(self.members())

        # Loop through all other sets and gather their members
        for other_set in other_sets:
            if isinstance(other_set, ObjectSet):
                members.update(other_set.members())
            else:
                raise TypeError(
                    "Expected ObjectSet, got {}".format(type(other_set))
                )

        # Create a new set for the union
        union_set_name = cmds.sets(name="unionSet", empty=True)
        cmds.sets(list(members), forceElement=union_set_name)

        # Return the new ObjectSet
        return ObjectSet(union_set_name)


nt.registerClass("objectSet", cls=ObjectSet)


class NurbsCurve(_Node):
    def __init__(self, nodename_or_mobject):
        super(NurbsCurve, self).__init__(nodename_or_mobject)
        self.__fn_curve = OpenMaya.MFnNurbsCurve(self.dagPath())

    def length(self):
        return self.__fn_curve.length()

    def findParamFromLength(self, l):
        return self.__fn_curve.findParamFromLength(l)

    def getPointAtParam(self, p):
        return self.__fn_curve.getPointAtParam(p)

    def form(self):
        frm = self.__fn_curve.form
        if frm == OpenMaya.MFnNurbsCurve.kInvalid:
            return attr.EnumValue(0, "invalid")
        elif frm == OpenMaya.MFnNurbsCurve.kOpen:
            return attr.EnumValue(1, "open")
        elif frm == OpenMaya.MFnNurbsCurve.kClosed:
            return attr.EnumValue(2, "closed")
        elif frm == OpenMaya.MFnNurbsCurve.kPeriodic:
            return attr.EnumValue(3, "periodic")
        else:
            return attr.EnumValue(4, "last")

    def degree(self):
        return self.__fn_curve.degree

    def getKnots(self):
        return [x for x in self.__fn_curve.knots()]

    def getCVs(self, space="preTransform"):
        return [
            datatypes.Point(x)
            for x in self.__fn_curve.cvPositions(util.to_mspace(space))
        ]


nt.registerClass("nurbsCurve", cls=NurbsCurve)


class SkinCluster(_Node):
    def __init__(self, nodename_or_mobject):
        super(SkinCluster, self).__init__(nodename_or_mobject)
        self.__skn = OpenMayaAnim.MFnSkinCluster(self.object())

    def getGeometry(self, **kwargs):
        kwargs["geometry"] = True
        kwargs["query"] = True
        return cmd.skinCluster(self, **kwargs)

    def __apimfn__(self):
        return self.__skn


nt.registerClass("skinCluster", cls=SkinCluster)


class Mesh(_Node):
    def __init__(self, nodename_or_mobject):
        super(Mesh, self).__init__(nodename_or_mobject)
        self.__fm = OpenMaya.MFnMesh(self.object())

    @property
    def faces(self):
        return geometry.BindGeometry("{}.f[:]".format(self.name()))

    def numFaces(self):
        return self.__fm.numPolygons


nt.registerClass("mesh", cls=Mesh)


class Joint(_Node):
    def __init__(self, nodename_or_mobject):
        super(Joint, self).__init__(nodename_or_mobject)

    def getRadius(self):
        return cmd.joint(self, q=True, radius=True)[0]


nt.registerClass("joint", cls=Joint)


def BindNode(name):
    if not cmds.objExists(name):
        raise exception.MayaNodeError("No such node '{}'".format(name))

    return nt.getTypeClass(cmds.nodeType(name))(name)
