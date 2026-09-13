#include "pxr/pxr.h"
#include "fileFormat.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/getenv.h"
#include "pxr/usd/sdf/layer.h"
#include "pxr/usd/usd/stage.h"

#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/evp.h>
#endif
#include <array>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <spawn.h>
#include <sstream>
#include <stdexcept>
#include <sys/file.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

extern char** environ;

PXR_NAMESPACE_OPEN_SCOPE
namespace {
namespace fs = std::filesystem;

// Use the platform cryptography library; IFC is read in bounded chunks.
std::string _Sha256(std::istream& input)
{
#ifdef __APPLE__
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
#else
    std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>
        context(EVP_MD_CTX_new(), EVP_MD_CTX_free);
    if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1)
        throw std::runtime_error("cannot initialize SHA-256");
#endif
    std::array<char, 65536> buffer;
    while (input) {
        input.read(buffer.data(), buffer.size());
#ifdef __APPLE__
        CC_SHA256_Update(&context, buffer.data(), static_cast<CC_LONG>(input.gcount()));
#else
        if (EVP_DigestUpdate(context.get(), buffer.data(), input.gcount()) != 1)
            throw std::runtime_error("cannot update SHA-256");
#endif
    }
    if (!input.eof()) throw std::runtime_error("cannot read IFC bytes");
    unsigned char digest[32];
#ifdef __APPLE__
    CC_SHA256_Final(digest, &context);
#else
    if (EVP_DigestFinal_ex(context.get(), digest, nullptr) != 1)
        throw std::runtime_error("cannot finish SHA-256");
#endif
    const char* hex = "0123456789abcdef";
    std::string result;
    for (auto byte : digest) {
        result += hex[byte >> 4];
        result += hex[byte & 15];
    }
    return result;
}

std::string _FileHash(const std::string& path)
{
    std::ifstream file(path, std::ios::binary);
    if (!file) throw std::runtime_error("cannot open IFC bytes: " + path);
    return _Sha256(file);
}

struct _Fd {
    int value;
    ~_Fd() { if (value >= 0) close(value); }
};

// No shell expansion. Capture a bounded stdout/stderr tail (including Python
// tracebacks). posix_spawn is safe when USD calls Read from worker threads.
std::string _Run(const std::vector<std::string>& command)
{
    int descriptors[2];
    if (pipe(descriptors) != 0) throw std::runtime_error("cannot create converter pipe");
    _Fd reader{descriptors[0]}, writer{descriptors[1]};
    fcntl(reader.value, F_SETFD, FD_CLOEXEC);
    fcntl(writer.value, F_SETFD, FD_CLOEXEC);
    posix_spawn_file_actions_t actions;
    posix_spawn_file_actions_init(&actions);
    posix_spawn_file_actions_adddup2(&actions, writer.value, STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&actions, writer.value, STDERR_FILENO);
    posix_spawn_file_actions_addclose(&actions, reader.value);
    posix_spawn_file_actions_addclose(&actions, writer.value);
    std::vector<char*> argv;
    for (const auto& arg : command) argv.push_back(const_cast<char*>(arg.c_str()));
    argv.push_back(nullptr);
    pid_t pid;
    int error = posix_spawnp(&pid, argv[0], &actions, nullptr, argv.data(), environ);
    posix_spawn_file_actions_destroy(&actions);
    if (error) throw std::runtime_error("cannot launch converter: " + std::string(strerror(error)));
    close(writer.value);
    writer.value = -1;
    std::string tail;
    std::array<char, 4096> buffer;
    ssize_t count;
    while ((count = read(reader.value, buffer.data(), buffer.size())) != 0) {
        if (count < 0) {
            if (errno == EINTR) continue;
            break;
        }
        tail.append(buffer.data(), count);
        if (tail.size() > 8192) tail.erase(0, tail.size() - 8192);
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR) throw std::runtime_error("cannot wait for converter");
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
        throw std::runtime_error("converter failed; output/stderr tail:\n" + tail);
    return tail;
}

fs::path _CacheRoot()
{
    auto root = TfGetenv("USDAECO_IFC_CACHE");
    if (!root.empty()) return fs::absolute(root);
    root = TfGetenv("XDG_CACHE_HOME");
    if (!root.empty()) return fs::absolute(root) / "usdaeco-ifc";
    root = TfGetenv("HOME");
    if (root.empty()) throw std::runtime_error("set USDAECO_IFC_CACHE or HOME");
    return fs::path(root) / ".cache/usdaeco-ifc";
}

struct _TemporaryDirectory {
    fs::path path;
    ~_TemporaryDirectory() { std::error_code error; fs::remove_all(path, error); }
};
} // namespace

UsdIfcFileFormat::UsdIfcFileFormat()
    : SdfFileFormat(TfToken("ifc"), TfToken("1.0"), TfToken("usd"), "ifc") {}

bool UsdIfcFileFormat::CanRead(const std::string& file) const
{
    std::ifstream input(file, std::ios::binary);
    char header[4096] = {};
    input.read(header, sizeof(header));
    return std::string(header, input.gcount()).find("ISO-10303-21;") != std::string::npos;
}

bool UsdIfcFileFormat::Read(SdfLayer* layer, const std::string& resolvedPath,
                          bool /* metadataOnly */) const
{
    try {
        std::string spine = "def", geometry = "1";
        for (const auto& arg : layer->GetFileFormatArguments()) {
            if (arg.first == "spine") spine = arg.second;
            else if (arg.first == "geometry") geometry = arg.second;
            else if (arg.first != "target" || arg.second != "usd")
                throw std::runtime_error("unknown IFC format argument: " + arg.first);
        }
        if ((spine != "def" && spine != "over") || (geometry != "0" && geometry != "1"))
            throw std::runtime_error("expected spine=def|over and geometry=0|1");
        const auto python = TfGetenv("USDAECO_IFC_PYTHON", USDAECO_IFC_DEFAULT_PYTHON);
        auto version = _Run({python, "-c", "from usdaeco_ifc import __version__; print(__version__)"});
        while (!version.empty() && (version.back() == '\n' || version.back() == '\r')) version.pop_back();
        if (version.empty() || version.size() > 128 ||
            version.find_first_not_of("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.+-_") != std::string::npos)
            throw std::runtime_error("invalid converter version response: " + version);
        const auto sourceHash = _FileHash(resolvedPath);
        std::istringstream keyInput("usdIfc-cache-v1\n" + sourceHash + "\nspine=" + spine +
                                    "\ngeometry=" + geometry + "\nconverter=" + version + "\n");
        const auto key = _Sha256(keyInput);
        const auto cache = _CacheRoot();
        fs::create_directories(cache);
        // The lock spans publication and reading. Crashes release flock; no
        // stale lock-directory protocol or partially published cache entries.
        _Fd lock{open((cache / (key + ".lock")).c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600)};
        if (lock.value < 0 || flock(lock.value, LOCK_EX) != 0)
            throw std::runtime_error("cannot lock IFC cache");
        const auto entry = cache / key;
        const auto flattened = entry / "flattened.usdc";
        if (!fs::is_regular_file(flattened)) {
            std::string pattern = (cache / (key + ".tmp-XXXXXX")).string();
            if (!mkdtemp(pattern.data())) throw std::runtime_error("cannot create cache work directory");
            _TemporaryDirectory temporary{pattern};
            const auto model = temporary.path / "model.usda";
            std::vector<std::string> command{python, "-m", "usdaeco_ifc.convert", resolvedPath, "-o", model.string()};
            if (spine == "over") command.push_back("--overlay-spine");
            if (geometry == "0") command.push_back("--no-geometry");
            const auto output = _Run(command);
            if (_FileHash(resolvedPath) != sourceHash)
                throw std::runtime_error("IFC changed during conversion; retry the open");
            auto stage = UsdStage::Open(model.string());
            if (!stage || !stage->GetCompositionErrors().empty())
                throw std::runtime_error("converter root does not compose; output/stderr tail:\n" + output);
            auto flat = stage->Flatten(false);
            if (!flat) throw std::runtime_error("cannot flatten converter root");
            // Flatten deliberately omits customLayerData; retain source stamps.
            flat->SetCustomLayerData(stage->GetRootLayer()->GetCustomLayerData());
            if (!flat->Export((temporary.path / "flattened.usdc").string()))
                throw std::runtime_error("cannot export flattened cache layer");
            fs::rename(temporary.path, entry);
        }
        // Anonymous open avoids stale Sdf registry entries after cache eviction.
        auto flat = SdfLayer::OpenAsAnonymous(flattened.string());
        if (!flat) throw std::runtime_error("cannot read flattened IFC cache; remove the cache entry and retry");
        layer->TransferContent(flat);
        return true;
    } catch (const std::exception& error) {
        TF_RUNTIME_ERROR("usdIfc: %s", error.what());
        return false;
    }
}

bool UsdIfcFileFormat::WriteToFile(const SdfLayer&, const std::string&,
                                 const std::string&, const FileFormatArguments&) const
{
    return false;
}

bool UsdIfcFileFormat::WriteToString(const SdfLayer& layer, std::string* text,
                                   const std::string& comment) const
{
    return SdfFileFormat::FindById(TfToken("usda"))->WriteToString(layer, text, comment);
}

bool UsdIfcFileFormat::WriteToStream(const SdfSpecHandle& spec, std::ostream& stream,
                                   size_t indent) const
{
    return SdfFileFormat::FindById(TfToken("usda"))->WriteToStream(spec, stream, indent);
}

TF_REGISTRY_FUNCTION(TfType)
{
    SDF_DEFINE_FILE_FORMAT(UsdIfcFileFormat, SdfFileFormat);
}

PXR_NAMESPACE_CLOSE_SCOPE
