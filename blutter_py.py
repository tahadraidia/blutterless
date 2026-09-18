#!/usr/bin/python3
"""
blutter_py.py - use B(l)utter from Python through the "pyblutter" CPython extension module.

This is the Python counterpart of blutter.py. Instead of building and running the
blutter executable, it builds (once, per Dart version) a CPython extension module and
imports it into the current interpreter, so libapp.so can be analyzed from Python code:

    import blutter_py

    # 1. one call does everything (detect Dart version, build module if needed, dump)
    outputs = blutter_py.dump("path/to/app/lib/arm64-v8a", "out_dir")
    print(outputs["frida"])

    # 2. or keep the module around and call it directly
    mod = blutter_py.load_module("path/to/app/lib/arm64-v8a")
    print(mod.version())                       # e.g. "3.4.2_android_arm64"
    mod.dump("path/to/libapp.so", "out_dir")   # same output files as the executable

The command line is the same as blutter.py:

    python3 blutter_py.py path/to/app/lib/arm64-v8a out_dir [--rebuild] [--no-analysis] [--dart-version 3.4.2_android_arm64]

All Dart version detection, Dart SDK fetching/building and compatibility macro logic is
reused from blutter.py / dartvm_fetch_build.py, so this file never needs to duplicate it.
"""
import argparse
import glob
import importlib.util
import os
import platform
import subprocess
import sys
import tempfile

# reuse everything from the original CLI driver (importing blutter.py has no side effects)
from blutter import (
    BlutterInput, find_lib_files, extract_libs_from_apk, find_compat_macro, get_dart_lib_info,
    SCRIPT_DIR, BIN_DIR, BUILD_DIR, PKG_LIB_DIR, CMAKE_CMD, NINJA_CMD,
)
from dartvm_fetch_build import DartLibInfo

PY_BIN_DIR = os.path.join(BIN_DIR, 'py')
MODULE_NAME = 'pyblutter'

# cache of loaded modules, keyed by module file path (one module per Dart version)
_loaded_modules = {}


def _module_dir(input: BlutterInput) -> str:
    return os.path.join(PY_BIN_DIR, input.blutter_name)


def find_module_file(input: BlutterInput):
    """Return the path of the built extension module for this Dart version, or None."""
    files = glob.glob(os.path.join(_module_dir(input), MODULE_NAME + '.*.so')) \
        + glob.glob(os.path.join(_module_dir(input), MODULE_NAME + '.*.pyd')) \
        + glob.glob(os.path.join(_module_dir(input), MODULE_NAME + '.so')) \
        + glob.glob(os.path.join(_module_dir(input), MODULE_NAME + '.pyd'))
    return files[0] if files else None


def _mac_env():
    # same compiler selection as blutter.cmake_blutter()
    if platform.system() == 'Darwin':
        mac_ver = int(platform.mac_ver()[0].split('.', 1)[0])
        if mac_ver < 15:
            llvm_path = subprocess.run(['brew', '--prefix', 'llvm@16'], capture_output=True, check=True).stdout.decode().strip()
            clang_file = os.path.join(llvm_path, 'bin', 'clang')
            return {**os.environ, 'CC': clang_file, 'CXX': clang_file + '++'}
    return None


def cmake_module(input: BlutterInput, python_executable: str = None):
    """Configure, build and install the pyblutter module for input.dart_info (Dart VM must already be built)."""
    blutter_dir = os.path.join(SCRIPT_DIR, 'blutter')
    # separate build directory from the executable build so cache variables never mix
    builddir = os.path.join(BUILD_DIR, input.blutter_name + '_py')
    macros = find_compat_macro(input.dart_info.version, input.no_analysis)
    python_executable = python_executable or sys.executable

    subprocess.run([CMAKE_CMD, '-GNinja', '-B', builddir,
                    f'-DDARTLIB={input.dart_info.lib_name}', f'-DNAME_SUFFIX={input.name_suffix}',
                    '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_PYTHON_MODULE=ON',
                    f'-DPython3_EXECUTABLE={python_executable}',
                    '--log-level=NOTICE'] + macros,
                   cwd=blutter_dir, check=True, env=_mac_env())
    # build only the module target (the executable target still exists but is not needed)
    subprocess.run([NINJA_CMD, MODULE_NAME], cwd=builddir, check=True)
    subprocess.run([CMAKE_CMD, '--install', '.', '--component', 'python'], cwd=builddir, check=True)


def ensure_module(input: BlutterInput) -> str:
    """Make sure the Dart VM library and the pyblutter module exist for this Dart version. Returns module path."""
    module_file = None if input.rebuild_blutter else find_module_file(input)
    if module_file is None:
        if os.name == 'nt':
            dartlib_file = os.path.join(PKG_LIB_DIR, input.dart_info.lib_name + '.lib')
        else:
            dartlib_file = os.path.join(PKG_LIB_DIR, 'lib' + input.dart_info.lib_name + '.a')
        if not os.path.isfile(dartlib_file):
            from dartvm_fetch_build import fetch_and_build
            fetch_and_build(input.dart_info)
        cmake_module(input)
        module_file = find_module_file(input)
        assert module_file is not None, "Build complete but cannot find pyblutter module in " + _module_dir(input)
    return module_file


def import_module_file(module_file: str):
    """Import a built pyblutter module from its file path (not registered in sys.modules)."""
    module_file = os.path.abspath(module_file)
    mod = _loaded_modules.get(module_file)
    if mod is None:
        # the spec name must end with "pyblutter" so the loader looks for PyInit_pyblutter
        spec = importlib.util.spec_from_file_location(MODULE_NAME, module_file)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _loaded_modules[module_file] = mod
    return mod


def _make_input(libapp_path: str, libflutter_path: str = None, dart_version: str = None,
                rebuild: bool = False, no_analysis: bool = False) -> BlutterInput:
    if dart_version is not None:
        version, os_name, arch = dart_version.split('_')
        dart_info = DartLibInfo(version, os_name, arch)
    else:
        assert libflutter_path is not None, "libflutter path or dart_version is required"
        dart_info = get_dart_lib_info(libapp_path, libflutter_path)
    # outdir is not used for building the module
    return BlutterInput(libapp_path, dart_info, '', rebuild, False, no_analysis)


def _resolve_input_paths(indir: str, tmp_dir: str):
    if indir.endswith('.apk'):
        return extract_libs_from_apk(indir, tmp_dir)
    if os.path.isfile(indir):
        # a bare libapp.so, only valid together with dart_version
        return os.path.abspath(indir), None
    return find_lib_files(indir)


def load_module(indir: str, dart_version: str = None, rebuild: bool = False, no_analysis: bool = False):
    """
    Return the imported pyblutter module matching the Dart version of the application.

    indir: an apk, a directory containing libapp.so and libflutter.so, or (with dart_version) a libapp.so file.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        libapp_path, libflutter_path = _resolve_input_paths(indir, tmp_dir)
        input = _make_input(libapp_path, libflutter_path, dart_version, rebuild, no_analysis)
        module_file = ensure_module(input)
    return import_module_file(module_file)


def dump(indir: str, outdir: str, dart_version: str = None, rebuild: bool = False,
         no_analysis: bool = False, quiet: bool = False) -> dict:
    """
    Analyze a Flutter application and write the same output files as blutter.py into outdir.
    Returns the dict of produced paths from pyblutter.dump().
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        libapp_path, libflutter_path = _resolve_input_paths(indir, tmp_dir)
        input = _make_input(libapp_path, libflutter_path, dart_version, rebuild, no_analysis)
        module_file = ensure_module(input)
        mod = import_module_file(module_file)
        # libapp extracted from an apk lives in tmp_dir, so dump inside the with block
        return mod.dump(libapp_path, outdir, quiet=quiet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog='B(l)utter (Python module)',
        description='Reversing a flutter application tool, running as a CPython extension module')
    parser.add_argument('indir', help='An apk or a directory that contains both libapp.so and libflutter.so')
    parser.add_argument('outdir', help='An output directory')
    parser.add_argument('--rebuild', action='store_true', default=False, help='Force rebuild the pyblutter module')
    parser.add_argument('--no-analysis', action='store_true', default=False, help='Do not build with code analysis')
    parser.add_argument('--quiet', action='store_true', default=False, help='Suppress progress output of the module')
    parser.add_argument('--dart-version', help='Run without libflutter (indir become libapp.so) by specify dart version such as "3.4.2_android_arm64"')
    args = parser.parse_args()

    outputs = dump(args.indir, args.outdir, args.dart_version, args.rebuild, args.no_analysis, args.quiet)
    for key, path in outputs.items():
        print(f'{key}: {path}')
