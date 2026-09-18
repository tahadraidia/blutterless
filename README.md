# B(l)utter
Flutter Mobile Application Reverse Engineering Tool by Compiling Dart AOT Runtime

Currently the application supports only Android libapp.so (arm64 only).
Also the application is currently work only against recent Dart versions.

For high priority missing features, see [TODO](#todo)


## Environment Setup
This application uses C++20 Formatting library. It requires very recent C++ compiler such as g++>=13, Clang>=16.

I recommend using Linux OS (only tested on Deiban sid/trixie) because it is easy to setup.

### Debian Unstable (gcc 13)
**_NOTE:_**
Use ONLY Debian/Ubuntu version that provides gcc>=13 from its own main repository.
Using ported gcc to old Debian/Ubuntu version does not work.

- Install build tools and depenencies
```
apt install python3-pyelftools python3-requests git cmake ninja-build \
    build-essential pkg-config libicu-dev libcapstone-dev
```

### Windows
- Install git and python 3
- Install latest Visual Studio with "Desktop development with C++" and "C++ CMake tools"
- Install required libraries (libcapstone and libicu4c)
```
python scripts\init_env_win.py
```
- Start "x64 Native Tools Command Prompt"

### macOS Sequoia
- Install XCode
- Install required tools
```
brew install cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

### macOS Ventura and Sonoma (clang 16)
- Install XCode
- Install clang 16 and required tools
```
brew install llvm@16 cmake ninja pkg-config icu4c capstone
pip3 install pyelftools requests
```

## Usage
Extract "lib" directory from apk file
```
python3 blutter.py path/to/app/lib/arm64-v8a out_dir
```
The blutter.py will automatically detect the Dart version from the flutter engine and call executable of blutter to get the information from libapp.so.

If the blutter executable for required Dart version does not exists, the script will automatically checkout Dart source code and compiling it.

## Update
You can use ```git pull``` to update and run blutter.py with ```--rebuild``` option to force rebuild the executable
```
python3 blutter.py path/to/app/lib/arm64-v8a out_dir --rebuild
```

## Python Module (CPython extension)
Blutter can also be built as a CPython extension module (`pyblutter`) instead of an executable, so the whole analysis can be driven from Python code. The module is built from exactly the same sources and compatibility macros as the executable; only `main.cpp` is replaced by the C-API glue in `blutter/src/pyblutter.cpp`.

Requirements are the same as the executable plus the Python development headers (`apt install python3-dev` on Debian/Ubuntu; included with python.org / Homebrew / Windows installers).

The `blutter_py.py` driver has the same command line as `blutter.py`. It detects the Dart version, fetches and builds the Dart VM library if needed, builds the module for the running interpreter and calls it:
```
python3 blutter_py.py path/to/app/lib/arm64-v8a out_dir
```

From your own Python code:
```python
import blutter_py

# detect Dart version, build the module if needed, and dump. returns the produced paths
outputs = blutter_py.dump("path/to/app/lib/arm64-v8a", "out_dir")
print(outputs)  # {'pp': ..., 'objs': ..., 'asm': ..., 'ida_script': ..., 'frida': ...}

# or keep the module for repeated use
mod = blutter_py.load_module("path/to/app.apk")
print(mod.version())                    # e.g. "3.4.2_android_arm64"
mod.dump("path/to/libapp.so", "out_dir", quiet=True)
```

Exposed C-API:
```C
static PyMethodDef methods[] = {
    {"dump", (PyCFunction)py_dump, METH_VARARGS | METH_KEYWORDS, "dump(libapp, outdir, quiet=False) -> dict of output paths"},
    {"version", py_version, METH_NOARGS, "version() -> Dart version this module was built for"},
    {NULL, NULL, 0, NULL}
};
```
Like the executable, one module is built per Dart version (into `bin/py/blutter_dartvm<ver>_<os>_<arch>/`). The module raises `RuntimeError` when the snapshot cannot be loaded. Progress messages are written to the process stdout (not `sys.stdout`); pass `quiet=True` to silence them.

Building manually (opt-in flag, the executable build is unaffected):
```
cmake -GNinja -B build/py -DDARTLIB=dartvm3.4.2_android_arm64 -DBUILD_PYTHON_MODULE=ON -DPython3_EXECUTABLE=$(which python3) <compat macros> blutter
ninja -C build/py pyblutter
cmake --install build/py --component python
```

## Output files
- **asm/\*** libapp assemblies with symbols
- **blutter_frida.js** the frida script template for the target application
- **objs.txt** complete (nested) dump of Object from Object Pool
- **pp.txt** all Dart objects in Object Pool


## Directories
- **bin** contains blutter executables for each Dart version in "blutter_dartvm\<ver\>\_\<os\>\_\<arch\>" format
- **bin/py** contains the pyblutter CPython extension modules, one directory per Dart version
- **blutter** contains source code. need building against Dart VM library
- **build** contains building projects which can be deleted after finishing the build process
- **dartsdk** contains checkout of Dart Runtime which can be deleted after finishing the build process
- **external** contains 3rd party libraries for Windows only
- **packages** contains the static libraries of Dart Runtime
- **scripts** contains python scripts for getting/building Dart


## Generating Visual Studio Solution for Development
I use Visual Studio to delevlop Blutter on Windows. ```--vs-sln``` options can be used to generate a Visual Studio solution.
```
python blutter.py path\to\lib\arm64-v8a build\vs --vs-sln
```

## TODO
- More code analysis
  - Function arguments and return type
  - Some psuedo code for code pattern
- Generate better Frida script
  - More internal classes
  - Object modification
- Obfuscated app (still missing many functions)
- Reading iOS binary
- Input as apk or ipa
