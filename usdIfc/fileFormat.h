#ifndef PXR_USD_PLUGIN_USD_IFC_FILE_FORMAT_H
#define PXR_USD_PLUGIN_USD_IFC_FILE_FORMAT_H

#include "pxr/pxr.h"
#include "pxr/usd/sdf/fileFormat.h"

PXR_NAMESPACE_OPEN_SCOPE

TF_DECLARE_WEAK_AND_REF_PTRS(UsdIfcFileFormat);

/// Read-only IFC materialization through the family converter.
class UsdIfcFileFormat final : public SdfFileFormat {
public:
    bool CanRead(const std::string& file) const override;
    bool Read(SdfLayer* layer, const std::string& resolvedPath,
              bool metadataOnly) const override;
    bool WriteToFile(const SdfLayer&, const std::string&,
                     const std::string& = std::string(),
                     const FileFormatArguments& = FileFormatArguments()) const override;
    bool WriteToString(const SdfLayer&, std::string*,
                       const std::string& = std::string()) const override;
    bool WriteToStream(const SdfSpecHandle&, std::ostream&, size_t) const override;

protected:
    SDF_FILE_FORMAT_FACTORY_ACCESS;
    UsdIfcFileFormat();
    ~UsdIfcFileFormat() override = default;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif
