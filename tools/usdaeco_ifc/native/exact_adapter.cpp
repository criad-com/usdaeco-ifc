// SPDX-License-Identifier: MIT
// Small ABI-matched adapter for operations absent from usdSolidOcct v0.1.
#include <pxr/pxr.h>
#include <TopoDS_Shape.hxx>
#include <pxr/external/boost/python.hpp>
#include <BRepTools.hxx>
#include <BRep_Builder.hxx>
#include <BRep_Tool.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepBuilderAPI_Transform.hxx>
#include <BRepBuilderAPI_Copy.hxx>
#include <ShapeUpgrade_ShapeDivideClosed.hxx>
#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <GCPnts_QuasiUniformDeflection.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Iterator.hxx>
#include <Standard_Failure.hxx>
#include <sstream>
#include <stdexcept>
#include <algorithm>

PXR_NAMESPACE_USING_DIRECTIVE
namespace py = pxr_boost::python;
namespace {
py::tuple xyz(const gp_XYZ& p) { return py::make_tuple(p.X(), p.Y(), p.Z()); }
TopoDS_Shape ReadBrep(const std::string& data) {
    std::istringstream stream(data);
    BRep_Builder builder;
    TopoDS_Shape shape;
    BRepTools::Read(shape, stream, builder);
    if (shape.IsNull()) throw std::invalid_argument("Invalid or empty OCCT BRep text");
    return shape;
}
TopoDS_Shape Transform(const TopoDS_Shape& shape, const py::object& matrix) {
    gp_Trsf t;
    t.SetValues(py::extract<double>(matrix[0]), py::extract<double>(matrix[4]), py::extract<double>(matrix[8]), py::extract<double>(matrix[12]),
                py::extract<double>(matrix[1]), py::extract<double>(matrix[5]), py::extract<double>(matrix[9]), py::extract<double>(matrix[13]),
                py::extract<double>(matrix[2]), py::extract<double>(matrix[6]), py::extract<double>(matrix[10]), py::extract<double>(matrix[14]));
    return BRepBuilderAPI_Transform(shape, t, true).Shape();
}
TopoDS_Shape SplitClosed(const TopoDS_Shape& shape) {
    ShapeUpgrade_ShapeDivideClosed split(BRepBuilderAPI_Copy(shape).Shape());
    split.Perform();
    return split.Result();
}
py::dict Inspect(const TopoDS_Shape& shape) {
    py::dict result;
    double tolerance = 0;
    py::list faces;
    TopTools_IndexedMapOfShape faceMap;
    TopExp::MapShapes(shape, TopAbs_FACE, faceMap);
    for (int i = 1; i <= faceMap.Extent(); ++i) {
        auto face = TopoDS::Face(faceMap(i));
        tolerance = std::max(tolerance, BRep_Tool::Tolerance(face));
        BRepAdaptor_Surface surface(face);
        GProp_GProps props;
        BRepGProp::SurfaceProperties(face, props);
        py::dict row;
        row["index"] = i - 1;
        row["area"] = props.Mass();
        row["type"] = "other";
        if (surface.GetType() == GeomAbs_Plane) {
            auto plane = surface.Plane();
            row["type"] = "plane";
            row["origin"] = xyz(plane.Location().XYZ());
            row["normal"] = xyz(plane.Axis().Direction().XYZ());
        } else if (surface.GetType() == GeomAbs_Cylinder) {
            auto cylinder = surface.Cylinder();
            row["type"] = "cylinder";
            row["radius"] = cylinder.Radius();
            row["origin"] = xyz(cylinder.Location().XYZ());
            row["axis"] = xyz(cylinder.Axis().Direction().XYZ());
        }
        faces.append(row);
    }
    for (TopExp_Explorer e(shape, TopAbs_EDGE); e.More(); e.Next())
        tolerance = std::max(tolerance, BRep_Tool::Tolerance(TopoDS::Edge(e.Current())));
    for (TopExp_Explorer e(shape, TopAbs_VERTEX); e.More(); e.Next())
        tolerance = std::max(tolerance, BRep_Tool::Tolerance(TopoDS::Vertex(e.Current())));
    result["tolerance"] = tolerance;
    result["faces"] = faces;
    py::list groups;
    // IfcOpenShell SERIALIZED reports one style per representation item.
    // The serialized compound preserves those items as its direct children.
    if (shape.ShapeType() == TopAbs_COMPOUND) {
        for (TopoDS_Iterator item(shape); item.More(); item.Next()) {
            py::list indices;
            TopTools_IndexedMapOfShape itemFaces;
            TopExp::MapShapes(item.Value(), TopAbs_FACE, itemFaces);
            for (int j = 1; j <= itemFaces.Extent(); ++j)
                indices.append(faceMap.FindIndex(itemFaces(j)) - 1);
            groups.append(indices);
        }
    } else {
        py::list indices;
        for (int i = 0; i < faceMap.Extent(); ++i) indices.append(i);
        groups.append(indices);
    }
    result["itemFaces"] = groups;
    return result;
}
py::list Edges(const TopoDS_Shape& shape, double deflection) {
    if (!(deflection > 0)) throw std::invalid_argument("Deflection must be positive");
    py::list result;
    TopTools_IndexedMapOfShape edges;
    TopExp::MapShapes(shape, TopAbs_EDGE, edges);
    for (int i = 1; i <= edges.Extent(); ++i) {
        auto edge = TopoDS::Edge(edges(i));
        if (BRep_Tool::Degenerated(edge)) continue;
        BRepAdaptor_Curve curve(edge);
        GCPnts_QuasiUniformDeflection sampling(curve, deflection);
        if (!sampling.IsDone()) throw std::runtime_error("Could not discretize edge");
        py::list points;
        for (int j = 1; j <= sampling.NbPoints(); ++j) points.append(xyz(sampling.Value(j).XYZ()));
        result.append(points);
    }
    return result;
}
void OcctError(const Standard_Failure& e) { PyErr_SetString(PyExc_RuntimeError, e.GetMessageString()); }
void ArgumentError(const std::invalid_argument& e) { PyErr_SetString(PyExc_ValueError, e.what()); }
}
PXR_BOOST_PYTHON_MODULE(_exact_native) {
    py::register_exception_translator<Standard_Failure>(&OcctError);
    py::register_exception_translator<std::invalid_argument>(&ArgumentError);
    py::def("ReadBrep", ReadBrep);
    py::def("Transform", Transform);
    py::def("SplitClosed", SplitClosed);
    py::def("Inspect", Inspect);
    py::def("Edges", Edges);
}
