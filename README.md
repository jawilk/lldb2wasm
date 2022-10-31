# lldb2wasm

```python3 server.py```

Then visit http://localhost:8000/


### Q&A
Why? The browser doesn't has any ptrace support.

That's true. We solely rely on the gdb-remote process plugin. Meaning we only aim to debug remote processes after attaching via the GDB Remote Serial Protocol. (In fact, emscripten recently [removed](https://github.com/emscripten-core/emscripten/blob/main/ChangeLog.md#3120---08242022) the ptrace specifc linux header from the sysroot).

### Try it first
You can test the resulting lldb.wasm in this repo. Just do ```python3 server.py``` and visit localhost:8000.

### Outline
- Compilation with emscripten
- Create C API
- Test in browser by loading a simple x86 executable as a target
- Adjust networking logic to accommodate for emscripten posix sockets emulation
- Remove threads
- Connect to [websockify](https://github.com/novnc/websockify)
- Writing a vscode web inline debugger extension using [lldb-vscode](https://github.com/llvm/llvm-project/tree/main/lldb/tools/lldb-vscode) (tbd)

### Intro
Since we rely on the gdb-remote process plugin, there are two possible setup scenarios:
- (i) Compile the target vm to wasm as well to have a browser only solution
- (ii) Let emscripten emulate the posix sockets over websockets and use websockify to translate the traffic back to tcp on server side

While having lldb and the to-be-debugged process (or vm in our case) physically separated certainly is the less efficient approach, we will in the following embark on the second route.

![Alt text](/media/lldb_wasm_.png "title")

### Compilation
We work off commit [d825850](https://github.com/llvm/llvm-project/commit/d8258508d49845c577db635ef14ef506df02e5e6) in the llvm-project repo and emsdk version 3.1.10.  To cross-compile lldb, we need to first compile llvm-tblgen and clang-tblgen for the host architecture.
```
~: git clone https://github.com/llvm/llvm-project
~: cd llvm-project
~: git checkout d8258508d49845c577db635ef14ef506df02e5e6
~: mkdir build_native && cd build_native
~: cmake -G Ninja \
        -S ../llvm/ \
        -B ./ \
        -DLLVM_BUILD_TOOLS=OFF \
        -DLLVM_ENABLE_THREADS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DLLDB_ENABLE_PYTHON=OFF \
        -DLLDB_ENABLE_LIBEDIT=OFF \
        -DLLDB_ENABLE_CURSES=OFF \
        -DLLDB_BUILD_FRAMEWORK=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLVM_TARGETS_TO_BUILD=X86 \
        -DLLVM_ENABLE_PROJECTS="clang;lldb"

~: cmake --build ./ -- llvm-tblgen clang-tblgen
```
Let's start out with the most naive approach: Blindly compiling and see how far we can get.
```
~: cd ..
~: mkdir build_wasm && cd build_wasm
~: emcmake cmake -G Ninja \
        -S ../llvm/ \
        -B ./ \
        -DLLVM_BUILD_TOOLS=OFF \
        -DLLVM_ENABLE_THREADS=OFF \
        -DLLVM_INCLUDE_TESTS=OFF \
        -DLLVM_INCLUDE_BENCHMARKS=OFF \
        -DLLDB_ENABLE_PYTHON=OFF \
        -DLLDB_ENABLE_LIBEDIT=OFF \
        -DLLDB_ENABLE_CURSES=OFF \
        -DLLDB_BUILD_FRAMEWORK=OFF \
        -DCMAKE_BUILD_TYPE=Release \
        -DLLVM_TARGETS_TO_BUILD=X86 \
        -DLLVM_ENABLE_PROJECTS="clang;lldb" \
        -DLLVM_TABLEGEN=../build_native/bin/llvm-tblgen \
        -DCLANG_TABLEGEN=../build_native/bin/clang-tblgen \

~: ninja lldb
```
Surprisingly, we reach all the way to the linking stage right away, which errors with
`wasm-ld: error: initial memory too small, 17078320 bytes needed`
So let's increase the memory of our wasm module!
`~: EMCC_CFLAGS=-sTOTAL_MEMORY=268435456 ninja lldb`
This yields lots of errors about undefined symbols:
```
error: undefined symbol: _ZN12lldb_private13HostInfoLinux10InitializeEPFvRNS_8FileSpecEE (referenced by top-level compiled C/C++ code)
warning: Link with `-sLLD_REPORT_UNDEFINED` to get more information on undefined symbols
warning: To disable errors for undefined symbols use `-sERROR_ON_UNDEFINED_SYMBOLS=0`
[...]
Error: Aborting compilation due to previous errors
```

We do as advised and try again with the `sERROR_ON_UNDEFINED_SYMBOLS=0` linker flag.
`~: EMCC_CFLAGS="-sTOTAL_MEMORY=268435456 -sERROR_ON_UNDEFINED_SYMBOLS=0" ninja lldb`
**Please note**: While we can pass the linking stage with this flag, we have to be very cautious. Should we invoke one of these symbols during execution, we will abort with a runtime error. We'll need to come back to this very shortly.

As you can see in the `build_wasm/bin` folder, we have indeed generated an *lldb.js* and an *lldb.wasm* file. How can we make use of those now? Let us write a small example API that lets us execute commands with the help of the *CommandInterpreter*. Navigate to llvm-project/lldb/tools/driver and open *Driver.cpp*. Find the *main* function and insert `#ifndef __EMSCRIPTEN__` above it. Now, scroll to the end of the main function and add
```
} // Closing bracket of old main()
#else
#include "PATH/TO/EMSDK/emsdk/upstream/emscripten/cache/sysroot/include/emscripten/emscripten.h"
#include <iostream>

using namespace std;

extern "C" {
    EMSCRIPTEN_KEEPALIVE const char* execute_command(const char* input);
}

class LLDBSentry {
public:
  LLDBSentry() {
    // Initialize LLDB
    SBDebugger::Initialize();
  }
  ~LLDBSentry() {
    // Terminate LLDB
    SBDebugger::Terminate();
  }
};

static SBDebugger g_debugger;
static LLDBSentry sentry;


int main() {
    cout << "LLDB WASM call - " << __FUNCTION__ << "\n";

    // Create debugger instance
    g_debugger = SBDebugger::Create(false);
    if (!g_debugger.IsValid())
        fprintf(stderr, "error: failed to create a debugger object\n");
    g_debugger.SetAsync(false);

    return 0;
}

// API
const char* execute_command(const char* command) {
    cout << "LLDB WASM call - " << __FUNCTION__ << " command: " << command << "\n";

    SBCommandReturnObject result;
    SBCommandInterpreter sb_interpreter = g_debugger.GetCommandInterpreter();
    sb_interpreter.HandleCommand(command, result, false);
    cout << "result: " << result.GetOutput() << "\n";
    return strdup(result.GetOutput());
}
#endif
```
Please adjust the path to your local emsdk in the first include directive. (You can also find these changes within this [commit](https://github.com/jawilk/llvm-project/commit/12a948e45a38fd13a2324998e6478660263f24af)). We have now a new *main* function which creates an instance of the global debugger variable (note: to persist state between wasm calls we have to store data in global variables) and an *execute_command* function which will be called from the javascript side. To be able to call our function from javascript, we need to export [ccall](https://emscripten.org/docs/porting/connecting_cpp_and_javascript/Interacting-with-code.html#calling-compiled-c-functions-from-javascript-using-ccall-cwrap).
We compile again:
`~: EMCC_CFLAGS="-sTOTAL_MEMORY=268435456 -sERROR_ON_UNDEFINED_SYMBOLS=0 -sEXPORTED_RUNTIME_METHODS=ccall" ninja lldb`
If you swap the generated *lldb.js* and *lldb.wasm* for the original ones in the lldb2wasm repo and start the server, you will be greeted with
```
missing function: _ZN12lldb_private13HostInfoLinux10InitializeEPFvRNS_8FileSpecEE [lldb.js:1:12850]

20:28:24.064 Aborted(-1) [lldb.js:1:9214]

20:28:24.064 Uncaught (in promise) RuntimeError: Aborted(-1). Build with -sASSERTIONS for more info.
```
in your browser's console. Apparently, blindly skipping all of the undefined symbol warnings wasn't the best idea and we got hit with the consequences right away. After some digging in the code base, the culprit seems to be the *CMakeLists.txt* file in *llvm-project/lldb/source/Host*.
We can see that each OS has their own *Host.cpp* file eg
`  elseif (CMAKE_SYSTEM_NAME MATCHES "Linux|Android")`, but none for Emscripten exists.  Fix it with
`elseif (CMAKE_SYSTEM_NAME MATCHES "Linux|Android|Emscripten")`
and compile again (we just reuse the linux files). Now we get this:
```
wasm-ld: error: duplicate symbol: lldb_private::Host::FindProcessThreads(unsigned long long, std::__2::map<unsigned long long, bool, std::__2::less<unsigned long long>, std::__2::allocator<std::__2::pair<unsigned long long const, bool> > >&)
```
ripgrep for `FindProcessThreads` results in
```
#if !defined(__linux__)
bool Host::FindProcessThreads(const lldb::pid_t pid, TidMap &tids_to_attach) {
  return false;
}
#endif
```
in *llvm-project/lldb/source/Host/common/Host.cpp*. The reason for this error is that the same function is also defined in *llvm-project/lldb/source/Host/linux/Host.cpp*, which we just included in the previous step. Change the first line of the snippet above to
`#if !defined(__linux__) && !defined(__EMSCRIPTEN__)` ([commit](https://github.com/jawilk/llvm-project/commit/5a88e032506dc447ed4eb196e2b7f30da4b82470) for both changes).
After compiling anew and copying the resulting lldb.js/lldb.wasm into the lldb2wasm repo, we can finally use a first version of our lldb wasm build in the browser. Try it out by hitting *New
Command* and type `version`.
### Loading an executable
Let us define a new API endpoint *create_target* ([commit](https://github.com/jawilk/llvm-project/commit/7a14bd20fdc97d24644601d109b416f3f4812bdc))
```
extern "C" {
    EMSCRIPTEN_KEEPALIVE const char* execute_command(const char* input);
    EMSCRIPTEN_KEEPALIVE void create_target(const char* path);
}

[...]

void create_target(const char* path) {
    cout << "LLDB WASM call - " << __FUNCTION__ << " path: " << path << "\n";

    SBError error;
    const char *arch = NULL;
    const char *platform = NULL;
    const bool add_dependent_libs = false;
    g_debugger.CreateTarget(path, arch, platform, add_dependent_libs, error);
}
```
Compile again, this time also exporting `FS` and `UTF8ToString`:
`~: EMCC_CFLAGS="-sTOTAL_MEMORY=268435456 -sERROR_ON_UNDEFINED_SYMBOLS=0 -sEXPORTED_RUNTIME_METHODS=ccall,FS,UTF8ToString" ninja lldb`
We will try to load a simple executable.  Compile a hello world C program *hello.c*:
```
#include <stdio.h>

int main() {
	printf("Hello World\n");
	return 0;
}
```
`~: gcc hello.c -o hello.o`
Now try to load *hello.o* via the first button on the page. Afterwards, execute `target list` via the second button. Unfortunately, this doesn't work out of the box and errors with:
`Uncaught (in promise) RuntimeError: indirect call to null`

If lldb loads a new target, it has to determine the architecture (target triple) and host platform of the executable. We need to account for emscripten here as well and add a default platform. Add
`#if (defined(__linux__) || defined(__EMSCRIPTEN__)) && !defined(__ANDROID__)` in  *llvm-project/lldb/source/Plugins/Platform/Linux/PlatformLinux.cpp* ([commit](https://github.com/jawilk/llvm-project/commit/45931bc8c37338773822abfb2818cb3f451d1571)).
Afterwards try to load the target again.
`target list` should now report:
```
Current targets:
* target #0: /hello.o ( arch=x86_64-*-linux, platform=remote-linux )
```


### Adjust networking logic
We need to tweak the original tcp sockets logic to allow for connect/recv/send emulation through websockets. To get updates from the websockets we make use of [asyncify](https://emscripten.org/docs/porting/asyncify.html). A good starting example can be found in the [test dir](https://github.com/emscripten-core/emscripten/blob/main/test/sockets/test_sockets_echo_client.c) of the emscripten repo. Please see this [commit](https://github.com/jawilk/llvm-project/commit/efc49e1d8351162c70e1f7926f6cbfa52dd87d38) for the relevant patches. Compile with 
`EMCC_CFLAGS="-Os -sSOCKET_DEBUG -sASYNCIFY_STACK_SIZE=8192 -sASYNCIFY -sTOTAL_MEMORY=268435456 -sERROR_ON_UNDEFINED_SYMBOLS=0 -sEXPORTED_RUNTIME_METHODS=ccall,FS,UTF8ToString -sEXPORTED_FUNCTIONS=_main,_fre\" ninja lldb`



### Remove threads

Since we plan to only debug single threaded applications, we can reduce some of lldb's internal complexity by disallowing the spawning of additional threads. The reason is that at any given time we only need to keep track of one thread and we don't mind blocking on certain actions (eg waiting for a gdb-remote packet) since lldb is our main application. As an overview, for the actions we are interested in (step-into, step-over (next), continue), lldb spawns the following threads:
- gdb-remote async thread (ProcessGDBRemote::AsyncThread)
- PrivateStateThread (Process::RunPrivateStateThread)
-  (EventHandlerThread (Debugger::EventHandlerThread))

The general idea is to call the handling function (previously a thread) directly after a state change/event got broadcasted. This way, we can avoid compiling with pthreads/webworker support. The state propagation can be sketched like this:

Please see this [commit](https://github.com/jawilk/llvm-project/commit/b56dd3c15baf2ed3fb7a239ce9bc401736f38950) for the relevant patches.

![Alt text](/media/lldb_threads_.png "title")


### Websockify setup
First we need to install websockify
```
~: git clone https://github.com/novnc/websockify
~: cd websockify
~: python3 setup.py install
```
Now we can listen for connections from the browser on PORT_1 and proxy to the vm/process on PORT_2 `websockify :PORT_1  :PORT_2`  (assuming both are localhost).
Start a new lldb-server instance: `lldb-server g :PORT_1 ./hello.o`
Then you can connect from the browser by entering `gdb-remote PORT_1` in the *New Command* button's prompt.

### Writing a vscode web inline debugger extension
Part 2 tbd

![Alt text](/media/vscode_extension.png "title")
