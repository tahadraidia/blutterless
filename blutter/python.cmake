# python.cmake - optional CPython extension module build for B(l)utter
#
# This file is included from the end of CMakeLists.txt only when
# -DBUILD_PYTHON_MODULE=ON is given. It reuses every variable the executable
# target already computed (DARTLIB, SRCS, defines, cc_opts, capstone, ...) so
# the two targets always compile with identical compatibility macros.
#
# The executable target is left completely untouched; the module is a second,
# independent target named "pyblutter" that is built with:
#     ninja pyblutter
# and installed with:
#     cmake --install . --component python
#
# Nothing in this file is required by the original Blutter project, which
# keeps upstream merges trivial (only a 3 line hook lives in CMakeLists.txt).

if (NOT DEFINED Python3_EXECUTABLE AND DEFINED ENV{PYBLUTTER_PYTHON})
	set(Python3_EXECUTABLE "$ENV{PYBLUTTER_PYTHON}")
endif()
# Development.Module is enough for an extension module (no libpython link needed on
# Linux/macOS, which is what makes the same .so importable by any matching interpreter)
find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
message(STATUS "pyblutter: building for Python ${Python3_VERSION} (${Python3_EXECUTABLE})")

# Python import name. Must be a valid identifier (PyInit_<name>) and must not clash with
# the repository's "blutter.py" / "blutter/" directory, hence the "py" prefix.
set(PYMODNAME "pyblutter")

# same sources as the executable, minus the CLI entry point, plus the C-API glue
set(PYSRCS ${SRCS})
list(REMOVE_ITEM PYSRCS "${SRCDIR}/main.cpp")
list(APPEND PYSRCS "${SRCDIR}/pyblutter.cpp")

# WITH_SOABI names the file e.g. pyblutter.cpython-312-x86_64-linux-gnu.so / pyblutter.cp312-win_amd64.pyd
Python3_add_library(${PYMODNAME} MODULE WITH_SOABI ${PYSRCS})

target_link_libraries(${PYMODNAME} PRIVATE ${DARTLIB} capstone)
target_precompile_headers(${PYMODNAME} PRIVATE "${SRCDIR}/pch.h")
# pyblutter.cpp must include <Python.h> before anything else, so it includes pch.h itself
set_source_files_properties("${SRCDIR}/pyblutter.cpp" PROPERTIES SKIP_PRECOMPILE_HEADERS ON)

# identical compile definitions/options as the executable + the Dart version string for pyblutter.version()
target_compile_definitions(${PYMODNAME} PRIVATE ${defines} PYBLUTTER_DARTLIB="${DARTLIB}" PYBLUTTER_BINNAME="${BINNAME}")
target_compile_options(${PYMODNAME} PRIVATE ${cc_opts})

if (MSVC)
	# for dynamic function (exception) table (same as the executable)
	target_link_libraries(${PYMODNAME} PRIVATE ntdll)
	if (NOT CMAKE_GENERATOR MATCHES "Visual Studio")
		target_link_options(${PYMODNAME} PRIVATE /LTCG /OPT:REF /OPT:ICF)
	endif()
else()
	# the Dart VM static library is built with POSITION_INDEPENDENT_CODE ON (scripts/CMakeLists.txt),
	# so it can be linked into a shared module. Make sure our own objects are PIC too.
	set_target_properties(${PYMODNAME} PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()

# install next to the executables: bin/py/<blutter_dartvmX.Y.Z_os_arch[suffix]>/pyblutter.<soabi>.so
# a directory per Dart version lets blutter_py.py pick the right module while the import name stays "pyblutter"
cmake_path(SET PY_DST_DIR NORMALIZE "${PROJECT_SOURCE_DIR}/../bin/py/${BINNAME}")
# COMPONENT is per artifact kind: LIBRARY is the .so on Linux/macOS, RUNTIME is the .pyd on Windows
install(TARGETS ${PYMODNAME}
	LIBRARY DESTINATION ${PY_DST_DIR} COMPONENT python
	RUNTIME DESTINATION ${PY_DST_DIR} COMPONENT python
)
