"""Host Jail and fail-closed Docker execution, with no Docker Python dependency."""

from importlib import resources
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import uuid

from . import memory, skills
from .tools import Tool, ToolResult, core_tools

DEFAULT_IMAGE = "chiikawa-sandbox:1"
HIDDEN_DIRS = {".chiikawa", ".codex", ".agents", ".aws", ".ssh"}
WORKER_FILES = ("sandbox_worker.py", "tools.py", "memory.py", "skills.py")


def docker(args, **kwargs):
    try:
        result = subprocess.run(["docker", *args], text=True, capture_output=True,
                                timeout=kwargs.pop("timeout", 30), **kwargs)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"Docker failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def setup_image(image=DEFAULT_IMAGE):
    """Explicit setup uses only bundled build sources, including from a zipapp."""
    with tempfile.TemporaryDirectory(prefix="chiikawa-build-") as temporary:
        build = Path(temporary)
        (build / "Dockerfile").write_bytes(resources.files("chiikawa").joinpath("Sandbox.Dockerfile").read_bytes())
        # Stream download/build progress; the project directory is never a build context.
        result = subprocess.run(["docker", "build", "--tag", image, str(build)])
        if result.returncode:
            raise RuntimeError("Sandbox image build failed.")


def validate_session(root, path=None):
    """Sandbox journals must live behind the masked, non-symlink session directory."""
    root = Path(root).resolve()
    directory = root / ".chiikawa" / "sessions"
    selected = Path(path).absolute() if path is not None else directory
    if not selected.is_relative_to(directory) or ".." in selected.parts:
        raise ValueError("Sandbox sessions must be inside .chiikawa/sessions.")
    for part in (selected, *selected.parents):
        if part == root:
            break
        if part.is_symlink() or (part.is_file() and part.stat().st_nlink > 1):
            raise ValueError(f"Unsafe sandbox session path: {part}")
    return directory


def protected_file(name):
    if name == "credential.md":
        return True
    return (name == ".env" or name.startswith(".env.")) and not any(
        part in {"example", "sample"} for part in name.split(".")[2:])


def protected_paths(root):
    """Inspect metadata only. Reject aliasing and host sockets before every mount."""
    masked = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in dirs + files:
            path = parent / name
            relative = path.relative_to(root)
            info = path.lstat()
            hidden = (len(relative.parts) == 1 and name in HIDDEN_DIRS) or protected_file(name)
            git = len(relative.parts) == 1 and name == ".git"
            if (hidden or git) and stat.S_ISLNK(info.st_mode):
                raise ValueError(f"Protected path cannot be a symlink: {relative}")
            if git and not stat.S_ISDIR(info.st_mode):
                raise ValueError("Sandbox v1 does not support linked worktrees/external Git metadata.")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise ValueError(f"Sandbox cannot safely mount hard-linked files: {relative}")
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise ValueError(f"Sandbox cannot mount sockets or special files: {relative}")
            if hidden and not any(path.is_relative_to(existing) for existing, _ in masked):
                masked.append((path, stat.S_ISDIR(info.st_mode)))
    return masked


class JailRuntime:
    isolation = "jail"

    def __init__(self, root):
        self.root = root

    def environment(self):
        return {"catalog": skills.catalog_prompt(self.root), "memory": memory.read_memory(self.root)}

    def tools(self):
        return core_tools(self.root)

    def call(self, name, args):
        if name == "remember":
            return memory.remember(self.root, **args)
        if name == "use_skill":
            return skills.read_skill(self.root, **args)
        raise ValueError(name)


class SandboxRuntime:
    isolation = "sandbox"

    def __init__(self, root, image=DEFAULT_IMAGE, network="deny"):
        self.root, self.network = Path(root).resolve(), network
        if Path.home().resolve().is_relative_to(self.root):
            raise ValueError("Sandbox workspace must not include the host home directory.")
        if network not in {"deny", "allow"}:
            raise ValueError("sandbox_network must be deny or allow")
        # Pin the locally inspected image ID; run never pulls or resolves a changed tag.
        context_name = os.environ.get("DOCKER_CONTEXT")
        endpoint = os.environ.get("DOCKER_HOST") if not context_name else None
        if not endpoint:
            endpoint = docker(["context", "inspect", *([context_name] if context_name else []),
                               "--format", "{{.Endpoints.docker.Host}}"]).strip()
        if not endpoint.startswith("unix://"):
            raise ValueError("Sandbox requires a local Docker daemon using a Unix socket.")
        if docker(["info", "--format", "{{.OSType}}"]).strip() != "linux":
            raise ValueError("Sandbox requires Linux containers.")
        self.image = docker(["image", "inspect", "--format", "{{.Id}}", image]).strip()
        directory = validate_session(self.root)
        protected_paths(self.root)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def environment(self):
        result = self.call("environment", {})
        if not isinstance(result, dict) or not all(isinstance(result.get(key), str) for key in ("catalog", "memory")):
            raise RuntimeError("Sandbox worker returned an invalid environment response.")
        return result

    def tools(self):
        return [Tool(item.name, item.spec, self._proxy(item.name)) for item in core_tools(self.root)]

    def _proxy(self, name):
        return lambda **args: self.call(name, args)

    def call(self, name, args):
        seconds = float(args.get("timeout", "120")) if name == "bash" else 120
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Sandbox timeout must be finite and positive")
        validate_session(self.root)
        masks = protected_paths(self.root)
        container = "chiikawa-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="chiikawa-worker-") as temporary:
            staging = Path(temporary)
            staging.chmod(0o755)
            worker = staging / "worker"
            worker.mkdir(mode=0o755)
            for filename in WORKER_FILES:
                target = worker / filename
                target.write_bytes(resources.files("chiikawa").joinpath(filename).read_bytes())
                target.chmod(0o444)
            empty_file, empty_dir = staging / "empty-file", staging / "empty-dir"
            empty_file.touch(mode=0o444)
            empty_dir.mkdir(mode=0o555)

            def mount(source, destination, readonly=False):
                # Docker --mount has its own CSV syntax. Reject ambiguous paths.
                if any(c in str(source) + destination for c in (",", "\n", "\r", '"')):
                    raise ValueError("Sandbox mount paths cannot contain commas, quotes or newlines")
                return ["--mount", f"type=bind,src={source},dst={destination}" + (",readonly" if readonly else "")]

            command = ["create", "--name", container, "--rm", "--pull=never", "--init", "--interactive",
                       "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                       "--cpus=2", "--memory=2g", "--memory-swap=2g", "--pids-limit=256",
                       "--network=" + ("none" if self.network == "deny" else "bridge"),
                       "--user", f"{os.getuid() or 65532}:{os.getgid() or 65532}",
                       "--workdir=/workspace", "--env=HOME=/tmp", "--tmpfs=/tmp:rw,nosuid,nodev,size=512m,mode=1777"]
            command += mount(self.root, "/workspace") + mount(worker, "/opt/chiikawa-worker", True)
            if (self.root / ".git").exists():
                command += mount(self.root / ".git", "/workspace/.git", True)
            for path, is_dir in masks:
                command += mount(empty_dir if is_dir else empty_file, "/workspace/" + path.relative_to(self.root).as_posix(), True)
            command += ["--entrypoint=python3", self.image, "-I", "/opt/chiikawa-worker/sandbox_worker.py"]
            try:
                # Finish creation before starting: an interrupted launch cannot race cleanup
                # by starting a previously unknown container after its client has exited.
                docker(command)
                output = docker(["start", "--attach", "--interactive", container],
                                input=json.dumps({"name": name, "args": args}), timeout=seconds + 10)
                response = json.loads(output)
                if not isinstance(response, dict):
                    raise RuntimeError("Sandbox worker returned an invalid JSON response.")
                if "error" in response:
                    raise RuntimeError(response["error"])
                if "result" not in response or not isinstance(response.get("details", {}), dict):
                    raise RuntimeError("Sandbox worker returned an invalid result.")
                result = response["result"]
                return ToolResult(result, **response.get("details", {})) if isinstance(result, str) else result
            finally:
                # Removing this exact container kills every process in its PID namespace.
                # Even if the client was interrupted, the daemon may have started it.
                try:
                    result = subprocess.run(["docker", "rm", "--force", container], capture_output=True, text=True, timeout=30)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RuntimeError(f"Sandbox cleanup failed for {container}: {exc}") from exc
                if result.returncode and "No such container" not in result.stderr:
                    raise RuntimeError(f"Sandbox cleanup failed for {container}: {result.stderr.strip()}")
