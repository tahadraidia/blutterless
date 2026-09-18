// pyblutter.cpp - CPython extension module for B(l)utter
//
// This file is the Python counterpart of main.cpp. It performs exactly the same
// steps as the command line executable (load libapp, analyze, dump object pool,
// objects, assemblies, IDA script and the Frida script) but is compiled into a
// shared module that can be imported from Python:
//
//     import pyblutter
//     outputs = pyblutter.dump("/path/to/libapp.so", "/path/to/out_dir")
//
// It is only compiled when the project is configured with -DBUILD_PYTHON_MODULE=ON
// (see python.cmake). None of the original Blutter sources are modified; the
// module simply links the same objects as the executable, minus main.cpp.
//
// The module is built for one specific Dart version (the one it was linked
// against), like the executable. blutter_py.py takes care of selecting/building
// the module that matches the Dart version found in libflutter.so.

// Python.h must come first (it may define feature macros used by system headers)
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "pch.h"
#include "DartApp.h"
#include "DartDumper.h"
#include "CodeAnalyzer.h"
#include "FridaWriter.h"
#include <filesystem>
#include <streambuf>
#include <string>
#include <exception>

#ifndef PYBLUTTER_DARTLIB
#	define PYBLUTTER_DARTLIB "dartvm_unknown"
#endif
#ifndef PYBLUTTER_BINNAME
#	define PYBLUTTER_BINNAME "blutter_" PYBLUTTER_DARTLIB
#endif

namespace {

// swallow std::cout while "quiet=True"
class NullBuffer : public std::streambuf {
protected:
	int overflow(int c) override { return c; }
};

struct CoutSilencer {
	std::streambuf* saved{ nullptr };
	NullBuffer nullBuf;
	explicit CoutSilencer(bool enable) {
		if (enable) {
			saved = std::cout.rdbuf(&nullBuf);
		}
	}
	~CoutSilencer() {
		if (saved) {
			std::cout.rdbuf(saved);
		}
	}
};

struct DumpResult {
	std::string pp;
	std::string objs;
	std::string asm_dir;
	std::string ida_script;
	std::string frida;
};

// The body of main.cpp, kept in sync step by step with the executable.
// Runs without the GIL (no Python object is touched here). Throws on failure.
DumpResult do_dump(const std::string& libappPath, const std::filesystem::path& outDir)
{
	DartApp app{ libappPath.c_str() };
	std::cout << std::format("libapp is loaded at {:#x}\n", app.base());
	std::cout << std::format("Dart heap at {:#x}\n", app.heap_base());

	app.EnterScope();
	app.LoadInfo();
	app.ExitScope();

	app.EnterScope();
#ifndef NO_CODE_ANALYSIS
	std::cout << "Analyzing the application\n";
	CodeAnalyzer analyzer{ app };
	analyzer.AnalyzeAll();
#endif

	DumpResult res;
	res.pp = (outDir / "pp.txt").string();
	res.objs = (outDir / "objs.txt").string();
	res.asm_dir = (outDir / "asm").string();
	res.ida_script = (outDir / "ida_script").string();
	res.frida = (outDir / "blutter_frida.js").string();

	DartDumper dumper{ app };
	std::cout << "Dumping Object Pool\n";
	dumper.DumpObjectPool(res.pp.c_str());
	dumper.DumpObjects(res.objs.c_str());
#ifndef NO_CODE_ANALYSIS
	std::cout << "Generating application assemblies\n";
#else
	std::cout << "Generating application functions in asm folder\n";
#endif
	dumper.DumpCode(res.asm_dir.c_str());
	dumper.Dump4Ida(outDir / "ida_script");

	std::cout << "Generating Frida script\n";
	FridaWriter fwriter{ app };
	fwriter.Create(res.frida.c_str());

	app.ExitScope();
	// ~DartApp shuts the isolate down and calls Dart_Cleanup(), so dump() can be called again
	return res;
}

PyObject* py_dump(PyObject* /*self*/, PyObject* args, PyObject* kwargs)
{
	static const char* kwlist[] = { "libapp", "outdir", "quiet", nullptr };
	const char* libapp = nullptr;
	const char* outdir = nullptr;
	int quiet = 0;
	if (!PyArg_ParseTupleAndKeywords(args, kwargs, "ss|p:dump", const_cast<char**>(kwlist), &libapp, &outdir, &quiet)) {
		return nullptr;
	}
	if (!*libapp || !*outdir) {
		PyErr_SetString(PyExc_ValueError, "libapp and outdir must be non-empty paths");
		return nullptr;
	}

	// ElfHelper maps the file without checking open() failures (the executable segfaults on a bad
	// path), so validate here before handing it to the loader.
	std::error_code ec;
	if (!std::filesystem::is_regular_file(libapp, ec)) {
		PyErr_Format(PyExc_FileNotFoundError, "libapp file not found: '%s'", libapp);
		return nullptr;
	}

	std::filesystem::path outDir{ outdir };
	if (!std::filesystem::create_directory(outDir, ec) && ec.value() != 0) {
		PyErr_Format(PyExc_OSError, "Failed to create output directory '%s': %s", outdir, ec.message().c_str());
		return nullptr;
	}

	DumpResult res;
	std::string error;
	bool ok = false;

	Py_BEGIN_ALLOW_THREADS
	try {
		CoutSilencer silencer{ quiet != 0 };
		res = do_dump(libapp, outDir);
		ok = true;
	}
	catch (std::exception& e) {
		error = e.what();
	}
	catch (...) {
		error = "unknown error";
	}
	Py_END_ALLOW_THREADS

	if (!ok) {
		PyErr_SetString(PyExc_RuntimeError, error.c_str());
		return nullptr;
	}

	return Py_BuildValue("{s:s,s:s,s:s,s:s,s:s}",
		"pp", res.pp.c_str(),
		"objs", res.objs.c_str(),
		"asm", res.asm_dir.c_str(),
		"ida_script", res.ida_script.c_str(),
		"frida", res.frida.c_str());
}

PyObject* py_version(PyObject* /*self*/, PyObject* /*unused*/)
{
	// "dartvm3.4.2_android_arm64" -> "3.4.2_android_arm64" (same format as blutter.py --dart-version)
	const char* lib = PYBLUTTER_DARTLIB;
	if (strncmp(lib, "dartvm", 6) == 0) {
		lib += 6;
	}
	return PyUnicode_FromString(lib);
}

PyMethodDef methods[] = {
	{ "dump", (PyCFunction)(void(*)(void))py_dump, METH_VARARGS | METH_KEYWORDS,
	  "dump(libapp, outdir, quiet=False) -> dict\n\n"
	  "Load a Dart AOT snapshot (libapp.so / App) and write the same output files as the\n"
	  "blutter executable into outdir (pp.txt, objs.txt, asm/, ida_script/, blutter_frida.js).\n"
	  "Returns a dict with the produced paths. Raises RuntimeError on failure.\n"
	  "The libapp must have been compiled with the Dart version this module was built for\n"
	  "(see pyblutter.version())." },
	{ "version", py_version, METH_NOARGS,
	  "version() -> str\n\nDart version/OS/arch this module was built for, e.g. '3.4.2_android_arm64'." },
	{ nullptr, nullptr, 0, nullptr }
};

PyModuleDef moduledef = {
	PyModuleDef_HEAD_INIT,
	"pyblutter",
	"B(l)utter - Flutter application reverse engineering, as a CPython extension module",
	-1,
	methods,
	nullptr, nullptr, nullptr, nullptr
};

} // namespace

PyMODINIT_FUNC PyInit_pyblutter(void)
{
	PyObject* m = PyModule_Create(&moduledef);
	if (m == nullptr) {
		return nullptr;
	}
	if (PyModule_AddStringConstant(m, "DARTLIB", PYBLUTTER_DARTLIB) < 0 ||
		PyModule_AddStringConstant(m, "BINNAME", PYBLUTTER_BINNAME) < 0 ||
#ifndef NO_CODE_ANALYSIS
		PyModule_AddIntConstant(m, "CODE_ANALYSIS", 1) < 0)
#else
		PyModule_AddIntConstant(m, "CODE_ANALYSIS", 0) < 0)
#endif
	{
		Py_DECREF(m);
		return nullptr;
	}
	return m;
}
