import sys
import json
import subprocess
import os
import shlex
from enum import Enum


class ProjectType(Enum):
    EXECUTABLE = 0
    SHARED_LIBRARY = 1
    STATIC_LIBRARY = 2


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


def create_build_dir(path: str):
    try:
        os.mkdir(path)
        os.mkdir(f"{path}/_deps")
    except:
        raise Exception(
            f"couldn't make a build directory `{path}`!\nmaybe the project is already setup?\nrun `make clean` if you wish to reinitialize it!"
        )


class Context:
    def __init__(
        self,
        name: str,
        *,
        build_dir: str = "build",
        project_type: ProjectType = ProjectType.EXECUTABLE,
        compiler: str = get_c_compiler(),
        archiver: str = "ar",
    ):
        self._sources: list[str] = []
        self._flags: list[str] = []

        self._name = name
        self._build_dir = build_dir
        self._project_type = project_type
        self._compiler = compiler
        self._archiver = archiver

        ok, _ = run(["which", self._compiler])
        if not ok:
            raise Exception("no compiler found!")

        if self._project_type == ProjectType.STATIC_LIBRARY:
            ok, _ = run(["which", self._archiver])
            if not ok:
                raise Exception("building a static library but no archiver found!")

        # used when building multiple projects with `Context.build_multiple()`, value of -1 means we are not
        self._index = -1

    def add_source(self, source: str | list[str]):
        if isinstance(source, str):
            source = [source]

        self._sources.extend(source)

    def add_include(self, include: str | list[str]):
        if isinstance(include, str):
            include = [include]

        for i in include:
            self._flags.append(f"-I{i}")

    def add_dependency(self, dep: str | list[str]):
        if isinstance(dep, str):
            dep = [dep]

        for d in dep:
            assert_installed(d)

        flags = pkgconfig_cflags(dep)
        libs = pkgconfig_libs(dep)

        # now we need to split these so they are passed as seperate arguments
        # note: what if some of them have spaces so they are escaped with quotes?
        # e.g. "-lsome library with spaces". is that even valid?
        parts = flags.split()
        self._flags.extend(parts)

        parts = libs.split()
        self._flags.extend(parts)

    def add_flag(self, flag: str | list[str]):
        if isinstance(flag, str):
            flag = [flag]

        self._flags.extend(flag)

    def build(self):
        create_build_dir(self._build_dir)

        objects = [
            create_object_makefile_entry(self, source) for source in self._sources
        ]

        with open("Makefile", "w") as mk:
            mk.write(create_phony_list())

            # keep this first so it is a default target
            target = create_target_makefile_entry(
                self, [object["dest"] for object in objects]
            )
            mk.write(target["entry"])

            mk.write(create_clean_makefile_entry([self._build_dir]))

            for object in objects:
                mk.write(object["entry"])

        with open(f"{self._build_dir}/compile_commands.json", "w") as j:
            root = get_root_dir()

            data = [
                {
                    "directory": root,
                    "command": object["cmd"],
                    "file": object["source"],
                    "output": object["dest"],
                }
                for object in objects
            ]

            j.write(json.dumps(data, indent=4))

    @classmethod
    def build_multiple(cls, ctxs: list["Context"]):
        if len(ctxs) == 0:
            return

        targets: list[dict] = []
        objects: list[dict] = []

        build_dirs: list[str] = []

        for index, ctx in enumerate(ctxs):
            if not ctx._build_dir in build_dirs:
                create_build_dir(ctx._build_dir)
                build_dirs.append(ctx._build_dir)

            ctx._index = index

            target_objects = [
                create_object_makefile_entry(ctx, source) for source in ctx._sources
            ]

            objects.extend(target_objects)

            targets.append(
                create_target_makefile_entry(
                    ctx, [object["dest"] for object in target_objects]
                )
            )

        with open("Makefile", "w") as mk:
            mk.write(create_phony_list())

            # keep this first so it is a default target
            mk.write(
                create_header("all", [target["dest"] for target in targets]) + "\n"
            )

            mk.write(create_clean_makefile_entry(build_dirs))

            for target in targets:
                mk.write(target["entry"])

            for object in objects:
                mk.write(object["entry"])

        # we just dump it in the first build directory, it is not really important to separate them
        with open(f"{ctxs[0]._build_dir}/compile_commands.json", "w") as j:
            root = get_root_dir()

            data = [
                {
                    "directory": root,
                    "command": object["cmd"],
                    "file": object["source"],
                    "output": object["dest"],
                }
                for object in objects
            ]

            j.write(json.dumps(data, indent=4))


def file_name_no_extension(path: str):
    return os.path.basename(path).split(".")[0]


def pkgconfig_cflags(dep: str | list[str]):
    if isinstance(dep, str):
        dep = [dep]

    cmd = ["pkg-config", "--cflags"] + dep

    ok, output = run(cmd)
    if not ok:
        raise Exception(f"`pkg-config --cflags` failed on `{dep}`")

    return output


def pkgconfig_libs(dep: str | list[str]):
    if isinstance(dep, str):
        dep = [dep]

    cmd = ["pkg-config", "--libs"] + dep

    ok, output = run(cmd)
    if not ok:
        raise Exception(f"pkg-config --libs failed on `{dep}`")

    return output


def create_object_name(ctx: Context, source: str):
    base = file_name_no_extension(source)
    if ctx._index != -1:
        base = f"{ctx._index}_{base}"

    return f"{ctx._build_dir}/{base}.o"


def create_dep_name(ctx: Context, source: str):
    base = file_name_no_extension(source)
    if ctx._index != -1:
        base = f"{ctx._index}_{base}"

    return f"{ctx._build_dir}/_deps/{base}.d"


def escape_spaces(s: str):
    return s.replace(" ", r"\ ")


def create_header(target: str, deps: list[str] = [], dir_deps: list[str] = []):
    return f"{escape_spaces(target)}: {' '.join(escape_spaces(dep) for dep in deps)} | {' '.join(escape_spaces(dep) for dep in dir_deps)}"


def create_shell(args: list[str]):
    return shlex.join(args)


def create_phony_list():
    return f".PHONY: clean\n"


def create_clean_makefile_entry(build_dirs: list[str]):
    header = create_header("clean", ["Makefile"], build_dirs)
    cmd = create_shell(["rm", "-rf", *build_dirs, "Makefile"])
    return f"{header}\n\t{cmd}\n"


def create_object_makefile_entry(ctx: Context, source: str):
    dest = create_object_name(ctx, source)
    dep = create_dep_name(ctx, source)

    flags = ctx._flags
    if ctx._project_type == ProjectType.SHARED_LIBRARY:
        flags.append("-fPIC")

    header = create_header(dest, [source], [ctx._build_dir, f"{ctx._build_dir}/_deps"])
    cmd = create_shell(
        [
            ctx._compiler,
            "-c",
            *flags,
            "-o",
            dest,
            "-MMD",
            "-MP",
            "-MF",
            dep,
            source,
        ]
    )

    entry = f"{header}\n\t{cmd}\n"
    entry += f"-include {escape_spaces(dep)}\n"

    return {
        "entry": entry,
        "source": source,
        "dest": dest,
        "cmd": cmd,
    }


def create_target_makefile_entry(ctx: Context, objects: list[str]):
    match ctx._project_type:
        case ProjectType.EXECUTABLE:
            dest = f"{ctx._build_dir}/{ctx._name}"
            header = create_header(dest, objects, [ctx._build_dir])

            cmd = create_shell(
                [
                    ctx._compiler,
                    *ctx._flags,
                    "-o",
                    dest,
                    *objects,
                ]
            )
        case ProjectType.SHARED_LIBRARY:
            dest = f"{ctx._build_dir}/lib{ctx._name}.so"
            header = create_header(dest, objects, [ctx._build_dir])

            cmd = create_shell(
                [
                    ctx._compiler,
                    "-shared",
                    *ctx._flags,
                    "-o",
                    dest,
                    *objects,
                ]
            )
        case ProjectType.STATIC_LIBRARY:
            dest = f"{ctx._build_dir}/lib{ctx._name}.a"
            header = create_header(dest, objects, [ctx._build_dir])

            cmd = create_shell(
                [
                    ctx._archiver,
                    "rcs",
                    dest,
                    *objects,
                ]
            )

    return {
        "entry": f"{header}\n\t{cmd}\n",
        "dest": dest,
        "cmd": cmd,
    }


def get_root_dir():
    path = sys.modules["__main__"].__file__
    if path == None:
        raise Exception("couldn't get this scripts name!")

    return path[: path.rindex("/")]


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


def debug_flags():
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


def release_flags():
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
