"""Native OS Jail worker for the managed runtime, offline by default.

macOS uses a deny-by-default Seatbelt profile; Linux requires bubblewrap user,
mount, PID, IPC and network namespaces. Failure never falls back to host tools.
The caller must enforce authenticated identity and IT policy before constructing
this runtime. This module does not grant network access or authenticate users.
"""

from importlib import resources
import json
import math
import os
from pathlib import Path
import platform
import pwd
import signal
import subprocess
import sys
import tempfile

from .runtime import WORKER_FILES, protected_paths, validate_session
from .tools import Tool, ToolResult, core_tools


def _quote(value):
    return json.dumps(str(value), ensure_ascii=False)


class EnterpriseJail:
    isolation = "jail"
    network = "deny"

    def __init__(self, root, uid, gid):
        self.root = Path(root).resolve()
        if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
            raise ValueError("Enterprise tools require an enrolled non-root OS identity.")
        if os.geteuid() not in (0, uid):
            raise PermissionError("Cannot launch tools as a different developer.")
        self.uid, self.gid = uid, gid
        home = Path(pwd.getpwuid(uid).pw_dir).resolve()
        if home.is_relative_to(self.root):
            raise ValueError("Enterprise workspace must not include the developer home directory.")
        if not self.root.is_dir():
            raise ValueError("Enterprise workspace must already exist.")
        self.system = platform.system()
        self.executable = Path("/usr/bin/sandbox-exec" if self.system == "Darwin" else "/usr/bin/bwrap")
        if self.system not in {"Darwin", "Linux"} or not self.executable.is_file():
            raise RuntimeError("IT must provision the native Jail runtime for this OS; host fallback is prohibited.")
        validate_session(self.root)
        protected_paths(self.root)

    def environment(self):
        response = self.call("environment", {})
        if not isinstance(response, dict) or any(not isinstance(response.get(key), str) for key in ("memory", "catalog")):
            raise RuntimeError("Native Jail worker returned an invalid environment.")
        return response

    def tools(self):
        return [Tool(item.name, item.spec, self._proxy(item.name)) for item in core_tools(self.root)]

    def _proxy(self, name):
        return lambda **args: self.call(name, args)

    def _mac_command(self, staging, worker, scratch, masks):
        profile = ["(version 1)", "(deny default)", "(allow process-exec process-fork)",
                   "(allow signal (target same-sandbox))", "(allow file-read-metadata)", "(allow sysctl-read)",
                   '(allow file-read* (literal "/"))']
        for path in ("/System", "/usr/lib", "/usr/share", "/usr/bin", "/usr/sbin", "/bin", "/sbin",
                     Path(sys.base_prefix).resolve(), Path(sys.executable).resolve().parent, worker):
            profile.append(f"(allow file-read* file-map-executable (subpath {_quote(path)}))")
        profile += [f"(allow file-read* file-write* file-map-executable (subpath {_quote(self.root)}) (subpath {_quote(scratch)}))",
                    '(allow file-read* file-write* (literal "/dev/null"))',
                    '(allow file-read* (literal "/dev/urandom") (literal "/dev/random"))']
        for path, is_dir in masks:
            profile.append(f"(deny file-read* file-write* ({'subpath' if is_dir else 'literal'} {_quote(path)}))")
        if (self.root / ".git").exists():
            profile.append(f"(deny file-write* (subpath {_quote(self.root / '.git')}))")
        # Networking, tracing other processes, Mach services, and arbitrary home
        # access are denied. Do not add broad allowances to silence sandbox errors.
        path = staging / "jail.sb"
        path.write_text("\n".join(profile) + "\n")
        path.chmod(0o444)
        return [str(self.executable), "-f", str(path), sys.executable, "-I", str(worker / "sandbox_worker.py"), str(self.root)]

    def _linux_command(self, worker, masks, empty_file, empty_dir):
        command = [str(self.executable), "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL"]
        paths = {Path(path) for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64") if Path(path).exists()}
        paths.add(Path(sys.base_prefix).resolve())
        paths.add(Path(sys.executable).resolve().parent)
        # Bind parent trees once. All system/interpreter trees are read-only.
        selected = []
        for path in sorted(paths, key=lambda item: len(item.parts)):
            if not any(path.is_relative_to(parent) for parent in selected):
                command += ["--ro-bind", str(path), str(path)]
                selected.append(path)
        command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                    "--bind", str(self.root), "/workspace", "--ro-bind", str(worker), "/opt/chiikawa-worker"]
        if (self.root / ".git").exists():
            command += ["--ro-bind", str(self.root / ".git"), "/workspace/.git"]
        for path, is_dir in masks:
            command += ["--ro-bind", str(empty_dir if is_dir else empty_file),
                        "/workspace/" + path.relative_to(self.root).as_posix()]
        command += ["--chdir", "/workspace", "--", sys.executable, "-I", "/opt/chiikawa-worker/sandbox_worker.py"]
        return command

    def call(self, name, args):
        seconds = float(args.get("timeout", "120")) if name == "bash" else 120
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Jail timeout must be finite and positive.")
        validate_session(self.root)
        masks = protected_paths(self.root)
        with tempfile.TemporaryDirectory(prefix="chiikawa-native-jail-") as temporary:
            staging = Path(temporary).resolve()
            staging.chmod(0o755)
            worker, scratch = staging / "worker", staging / "scratch"
            worker.mkdir(mode=0o755)
            scratch.mkdir(mode=0o700)
            if os.geteuid() == 0:
                os.chown(scratch, self.uid, self.gid)
            for filename in WORKER_FILES:
                path = worker / filename
                path.write_bytes(resources.files("chiikawa").joinpath(filename).read_bytes())
                path.chmod(0o444)
            empty_file, empty_dir = staging / "empty", staging / "empty-dir"
            empty_file.touch(mode=0o444)
            empty_dir.mkdir(mode=0o555)
            command = (self._mac_command(staging, worker, scratch, masks) if self.system == "Darwin"
                       else self._linux_command(worker, masks, empty_file, empty_dir))
            environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8",
                           "HOME": str(scratch) if self.system == "Darwin" else "/tmp",
                           "TMPDIR": str(scratch) if self.system == "Darwin" else "/tmp"}
            identities = {"user": self.uid, "group": self.gid, "extra_groups": []} if os.geteuid() == 0 else {}
            process = subprocess.Popen(command, cwd=self.root, env=environment, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True, **identities)
            try:
                output, error = process.communicate(json.dumps({"name": name, "args": args}), timeout=seconds + 10)
                if process.returncode:
                    raise RuntimeError(f"Native Jail failed ({process.returncode}): {error[:1000]}")
                response = json.loads(output)
                if not isinstance(response, dict) or "result" not in response:
                    raise RuntimeError(response.get("error", "Invalid native Jail response") if isinstance(response, dict)
                                       else "Invalid native Jail response")
                result = response["result"]
                return ToolResult(result, **response.get("details", {})) if isinstance(result, str) else result
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
