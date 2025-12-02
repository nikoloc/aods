import sys
import json
import subprocess
import os
from enum import Enum


class ProjectType(Enum):
    EXECUTABLE = 0
    SHARED_LIBRARY = 1
    STATIC_LIBRARY = 2


class Context:
    def __init__(self, name: str, compiler: str, build_dir: str):
        self._sources: list[str] = []
        self._flags: list[str] = []

        self._project_type = (
            ProjectType.SHARED_LIBRARY
            if name.endswith(".so")
            else (
                ProjectType.STATIC_LIBRARY
                if name.endswith(".a")
                else ProjectType.EXECUTABLE
            )
        )

        self.name = name
        self.compiler = compiler
        self.build_dir = build_dir

        # used when building multiple projects with `Context.build_multiple()`
        self._index = -1

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value: str):
        self._name = value

    @property
    def compiler(self):
        return self._compiler

    @compiler.setter
    def compiler(self, value: str):
        self._compiler = value

    @property
    def build_dir(self) -> str:
        return self._build_dir

    @build_dir.setter
    def build_dir(self, value: str):
        make_dir(value)
        make_dir(value + "/deps")

        self._build_dir = value

    def add_source(self, source: str | list[str]):
        if isinstance(source, str):
            source = [source]

        self._sources.extend(source)

    def add_include(self, include: str | list[str]):
        if isinstance(include, str):
            include = [include]

        for i in include:
            add_flag(self, " -I" + i)

    def add_dependency(self, dep: str | list[str]):
        if isinstance(dep, str):
            dep = [dep]

        for d in dep:
            assert_installed(d)
            # we cannot add them as usual since they might have some spaces if multiple arguments. i am counting on pkg-config to handle that anyway
            self._flags.append(f"{pkgconfig_cflags(d)}"))
            add_flag(self, f"{pkgconfig_libs(d)}")

    def add_flag(self, flag: str | list[str]):
        if isinstance(flag, str):
            flag = [flag]

        for f in flag:
            add_flag(self, f)

    def build(self):
        objects = [mk_object(self, source) for source in self._sources]

        with open("Makefile", "w") as mk:
            mk.write(mk_phony())

            header, cmd, _ = mk_target(self)
            mk.write(f"{header}\n\t{cmd}\n")

            for header, cmd, _, _ in objects:
                mk.write(f"{header}\n\t{cmd}\n")

            mk.write(mk_clean(self))

        with open(f"{self.build_dir}/compile_commands.json", "w") as j:
            data = [
                {
                    "directory": root_dir(),
                    "command": cmd,
                    "file": source,
                    "output": output,
                }
                for _, cmd, source, output in objects
            ]

            j.write(json.dumps(data, indent=4))

    @classmethod
    def default(cls, name):
        compiler = get_c_compiler()

        return cls(name, compiler, "build")

    @classmethod
    def build_multiple(cls, ctxs: list["Context"]):
        targets: list[tuple] = []
        objects: list[tuple] = []

        for index, ctx in enumerate(ctxs):
            ctx._index = index

            objects.extend([mk_object(ctx, source) for source in ctx._sources])
            targets.append(mk_target(ctx))

        with open("Makefile", "w") as mk:
            mk.write(f"{mk_phony()}\n")

            mk.write(f"all: {' '.join([output for _, _, output in targets])}\n")

            for header, cmd, _ in targets:
                mk.write(f"{header}\n\t{cmd}\n")

            for header, cmd, _, _ in objects:
                mk.write(f"{header}\n\t{cmd}\n")

            mk.write(mk_clean(ctxs[0]))

        with open(f"{ctxs[0].build_dir}/compile_commands.json", "w") as j:
            data = [
                {
                    "directory": root_dir(),
                    "command": cmd,
                    "file": source,
                    "output": output,
                }
                for _, cmd, source, output in objects
            ]

            j.write(json.dumps(data, indent=4))


def add_flag(ctx: Context, flag: str):
    ctx._flags.append(f'"{flag}"')


def pkgconfig_cflags(dep: str):
    cmd = ["pkg-config", "--cflags", dep]

    ok, flags = run(cmd)
    if not ok:
        raise Exception(f"`pkg-config --cflags` failed on `{dep}`")

    return flags


def pkgconfig_libs(dep: str):
    ok, libs = run(["pkg-config", "-libs", dep])
    if not ok:
        raise Exception(f"pkg-config --libs failed on `{dep}`")

    return libs


def object_dest(ctx: Context, source: str):
    base = file_name_no_extension(source)
    if ctx._index != -1:
        base = f"{ctx._index}_{base}"

    return f"{ctx.build_dir}/{base}.o"


def mk_phony():
    return f".PHONY: clean\n"


def mk_clean(ctx: Context):
    return f'clean: {ctx.build_dir} Makefile\n\trm -rf "{ctx.build_dir}" Makefile\n'


def create_object_cmd(ctx: Context, source: str):
    dest = object_dest(ctx, source)

    flags = ctx._flags
    if ctx._project_type == ProjectType.SHARED_LIBRARY:
        flags += " -fPIC"

    cmd = f'{ctx.compiler} -c {ctx._flags} -o {dest} -MMD -MP -MF "{ctx._build_dir}/deps/{source}.d" {source}'
    return cmd


def create_target_makefile_entry(ctx: Context, objects: list[str]):
    output = f"{ctx.build_dir}/{ctx.name}"
    expanded = " ".join(f'"{o}"' for o in objects)

    header = f"{ctx.build_dir}/{ctx.name}: {expanded}"

    if(ctx._project_type == ProjectType.SHARED_LIBRARY):
        cmd = f'"{ctx.compiler}" "-shared" {ctx._flags} -o "{output}" {expanded}'
    else:
        cmd = f'"{ctx.compiler}" {ctx._flags} -o "{output}" {expanded}'

    return f"{header}: {expanded}\n\t{cmd}\n"


def root_dir():
    path = sys.modules["__main__"].__file__
    if path == None:
        raise Exception("couldn't get this scripts name!")

    return path[: path.rindex("/")]


def run(cmd: list[str]):
    info = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return (
        info.returncode == 0,
        info.stdout.strip() if info.returncode == 0 else info.stderr.strip(),
    )


def get_c_compiler():
    ok, _ = run(["which", "cc"])
    if ok:
        return "cc"

    # fallback to gcc or clang if cc is not defined
    ok, _ = run(["which", "gcc"])
    if ok:
        return "gcc"

    ok, _ = run(["which", "clang"])
    if ok:
        return "clang"

    raise Exception("no c compiler found")


def make_dir(dir: str):
    try:
        os.mkdir(dir)
    except:
        return


def file_name_no_extension(path: str):
    return os.path.basename(path).split(".")[0]


def pkgconfig_is_installed(pkg: str):
    ok, _ = run(["pkg-config", "--exists", pkg])

    return ok


def assert_installed(pkg: str):
    if not pkgconfig_is_installed(pkg):
        raise Exception(f"`{pkg}` not found with pkgconfig!")


def pkgconfig_get_variable(pkg: str, var: str):
    ok, output = run(["pkg-config", f"--variable={var}", pkg])
    if not ok:
        raise Exception(f"no variable `{var}` for package `{pkg}`")

    return output


def default_debug_flags():
    return [
        "-g",
        "-O0",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-Wnull-dereference",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
    ]


def default_release_flags():
    return [
        "-O2",
        "-march=native",
        "-flto",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        "-fstack-protector-strong",
        "-D_FORTIFY_SOURCE=2",
        "-Wformat",
        "-Wformat-security",
    ]
