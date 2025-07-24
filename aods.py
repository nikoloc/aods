import sys
import json
import subprocess
import os
from enum import Enum


class Context:
    def __init__(
        self,
        name: str,
        compiler: str,
        build_dir: str,
    ):
        self._sources: list[str] = []

        self._includes: list[str] = []

        self._dependencies: list[str] = []
        self._flags = ""
        self._libs = ""

        self.name = name
        self.compiler = compiler
        self.build_dir = build_dir

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

        self._build_dir = value

    def add_source(self, source: str | list[str]):
        if isinstance(source, str):
            source = [source]

        self._sources.extend(source)

    def add_include(self, include: str | list[str]):
        if isinstance(include, str):
            include = [include]

        for i in include:
            self._includes.append(i)

    def add_dependency(self, dep: str | list[str]):
        if isinstance(dep, str):
            dep = [dep]

        self._dependencies.extend(dep)

        for d in dep:
            assert_installed(d)

        if self._flags != "":
            self._flags += " "

        self._flags += pkgconfig_cflags(dep)
        self._libs += pkgconfig_libs(dep)

    def add_flag(self, flag: str | list[str]):
        if isinstance(flag, str):
            flag = [flag]

        for f in flag:
            if self._flags != "":
                self._flags += " "

            self._flags += f

    def build(self):
        objects = [mk_object(self, source) for source in self._sources]

        with open("Makefile", "w") as mk:
            exec = mk_executable(self)
            mk.write(f"{exec[0]}\n\t{exec[1]}\n")

            for object in objects:
                mk.write(f"{object[0]}\t{object[1]}\n")

        with open(f"{self.build_dir}/compile_commands.json", "w") as j:
            data = [
                {
                    "directory": root_dir(),
                    "command": object[1],
                    "file": object[2],
                }
                for object in objects
            ]

            j.write(json.dumps(data, indent=4))

    @classmethod
    def default(cls, name):
        compiler = get_c_compiler()

        return cls(name, compiler, "build")


def pkgconfig_cflags(deps: list[str]):
    flags = run(["pkg-config", "-cflags"] + deps)
    if flags.returncode != 0:
        raise Exception(f"`pkg-config -cflags` failed")

    return flags.stdout.strip()


def pkgconfig_libs(deps: list[str]):
    libs = run(["pkg-config", "-libs"] + deps)
    if libs.returncode != 0:
        raise Exception(f"pkg-config -libs failed")

    return libs.stdout.strip()


def object_name(ctx: Context, source: str):
    base = base_name(file_name(source))
    return f"{ctx.build_dir}/{base}.o"


def mk_object(ctx: Context, source: str):
    header = run(
        [ctx.compiler]
        + [f"-I{i}" for i in ctx._includes]
        + ["-MT", object_name(ctx, source), "-MM", source]
    )

    if header.returncode != 0:
        raise Exception(
            f"failed making a makefile entry for `{source}`:\n{header.stderr}"
        )

    cmd = f"{ctx.compiler} -c {ctx._flags} {' '.join([f"-I{i}" for i in ctx._includes])} -o {object_name(ctx, source)} {source}"

    return (header.stdout, cmd, source)


def mk_executable(ctx: Context):
    objects = [object_name(ctx, source) for source in ctx._sources]

    header = f"{ctx.build_dir}/{ctx.name}: {' '.join(objects)}"
    cmd = f"{ctx.compiler} {ctx._flags} {ctx._libs} -o {f'{ctx.build_dir}/{ctx.name}'} {' '.join(objects)}"

    return (header, cmd)


def root_dir():
    path = sys.modules["__main__"].__file__
    if path == None:
        raise Exception("couldn't get this scripts name")

    return path


class BuildType(Enum):
    DEBUG = 0
    RELEASE = 1


def run(cmd: list[str]):
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result


def get_c_compiler():
    result = run(["which", "cc"])
    if result.returncode == 0:
        return "cc"

    # fallback to gcc or clang if cc is not defined
    result = run(["which", "gcc"])
    if result.returncode == 0:
        return "gcc"

    result = run(["which", "clang"])
    if result.returncode == 0:
        return "clang"

    raise Exception("no c compiler found")


def make_dir(dir: str):
    try:
        os.mkdir(dir)
    except:
        return


def base_name(name: str):
    return name.split(".")[0]


def file_name(path: str):
    return os.path.basename(path)


def pkgconfig_is_installed(pkg: str):
    result = run(["pkg-config", "--exists", pkg])

    return result.returncode == 0


def assert_installed(pkg: str):
    if not pkgconfig_is_installed(pkg):
        raise Exception(f"`{pkg}` not installed!")


def pkgconfig_get_variable(pkg: str, var: str):
    result = run(["pkg-config", f"--variable={var}", pkg])
    if result.returncode != 0:
        raise Exception(f"no variable {var} for package {pkg}")

    return result.stdout.strip()


def default_flags(type: BuildType):
    if type == BuildType.RELEASE:
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
    else:
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
