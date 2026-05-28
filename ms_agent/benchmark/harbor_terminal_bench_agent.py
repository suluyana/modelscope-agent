# Copyright (c) ModelScope Contributors. All rights reserved.
"""
Harbor BaseAgent for Terminal-Bench (EvalScope + harbor==0.1.x).

Installs ms-agent **inside the task container**, uploads a generated ``agent.yaml``
(keys from the host environment), then runs ``python -m ms_agent.cli.cli run``.

**Using unpublished / local changes** (recommended for development):

Set ``MS_AGENT_SOURCE_ROOT`` to the host path of the ms-agent repo root (directory
containing ``setup.py``). That tree is packed into a tarball (common bulky dirs
skipped), uploaded into the container, and installed with ``pip install`` so the
benchmark sees your working tree — **no PyPI release required**.

Fallback when ``MS_AGENT_SOURCE_ROOT`` is unset: ``MS_AGENT_PIP_INSTALL_SPEC`` or
the default PyPI spec (upstream releases named ``ms-agent`` on PyPI).

``loguru`` is always installed alongside: some releases omit it from
``install_requires`` while ``ms_agent`` imports still pull it in.

**Setup timeout:** installing from a local source tree often exceeds 20 minutes (heavy
deps such as ``sentence-transformers``). Set ``MS_AGENT_HARBOR_SETUP_TIMEOUT_SEC``
(default 7200) if needed.

**Optional speed-ups (local ``MS_AGENT_SOURCE_ROOT`` only):**

- ``MS_AGENT_HARBOR_PIP_STRATEGY``: when ``MS_AGENT_SOURCE_ROOT`` is set, defaults to
  ``pypi_then_local``: ``pip install --no-deps`` PyPI ``ms-agent``, then
  ``requirements/harbor.txt`` (TB runtime without ``mcp``), then ``--no-deps`` local
  source overlay. Set to ``local`` for the same harbor deps list after a ``--no-deps``
  install of the uploaded tree.
- ``MS_AGENT_HARBOR_TARBALL_CACHE_DIR=/path``: reuse a host-cached ``.tar.gz`` keyed by
  ``git rev-parse HEAD`` plus a short hash of ``git status --porcelain`` (dirty trees),
  skipping repeated tar packaging between bench runs. Does not skip container install.
"""
from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from ms_agent.version import __version__

# PyPI distribution name is ``ms-agent`` (not ``modelscope-agent``).
_DEFAULT_PIP_SPEC = 'ms-agent>=1.6.0'
_MS_AGENT_TIMEOUT_ENV = 'MS_AGENT_HARBOR_TIMEOUT_SEC'
_MS_AGENT_SETUP_TIMEOUT_ENV = 'MS_AGENT_HARBOR_SETUP_TIMEOUT_SEC'
_DEFAULT_SETUP_TIMEOUT_SEC = 7200
_MS_AGENT_PIP_SPEC_ENV = 'MS_AGENT_PIP_INSTALL_SPEC'
_MS_AGENT_SOURCE_ROOT_ENV = 'MS_AGENT_SOURCE_ROOT'
_MS_AGENT_PIP_STRATEGY_ENV = 'MS_AGENT_HARBOR_PIP_STRATEGY'
_MS_AGENT_TARBALL_CACHE_DIR_ENV = 'MS_AGENT_HARBOR_TARBALL_CACHE_DIR'
_CONTAINER_CONFIG_PATH = '/tmp/ms_agent_terminal_bench.yaml'
_VENV_DIR = '/tmp/ms_agent_tb_venv'
_CONTAINER_TARBALL_PATH = '/tmp/ms_agent_source_bundle.tar.gz'
_CONTAINER_PKG_DIR = '/tmp/ms_agent_pkg'
_HARBOR_REQUIREMENTS = 'requirements/harbor.txt'
_TARBALL_TOPDIR = 'ms_agent_pkg'

_TARBALL_EXCLUDE_DIRS = frozenset({
    '.git',
    '.venv',
    'venv',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    'dist',
    'build',
    '.eggs',
    'node_modules',
    '.tox',
    'htmlcov',
    '.cache',
    '.cursor',
    '.colima',
    '.xdg-cache',
})

# Only under *source_root* (depth 1). Keeps ``ms_agent/projects/...`` when present.
_TOPLEVEL_TARBALL_EXCLUDE_DIRS = frozenset({
    'projects',
    'build',
    'webui',
    'webui_backup',
    'output_video.bk',
    'output_video_china_bbk',
    'temp',
    'MagicMock',
    'output',
    'outputs',
    'docs',
})


def _prune_dirname(dirname: str) -> bool:
    """Return True if this directory must not be traversed or archived."""
    if dirname in _TARBALL_EXCLUDE_DIRS:
        return True
    if dirname.startswith('.venv'):
        return True
    if dirname.startswith('.claude'):
        return True
    if dirname.endswith('.egg-info'):
        return True
    return False


def _build_source_tarball(source_root: Path) -> Path:
    """Pack *source_root* into a gzipped tar; returns path to a temp file."""
    src_res = source_root.expanduser().resolve()
    if not (src_res / 'setup.py').is_file():
        raise FileNotFoundError(
            f'MS_AGENT_SOURCE_ROOT must point to ms-agent repo root with setup.py: '
            f'{src_res}'
        )
    fd, raw_path = tempfile.mkstemp(suffix='.tar.gz')
    os.close(fd)
    tar_path = Path(raw_path)
    try:
        with tarfile.open(tar_path, 'w:gz') as tar:
            for dirpath, dirnames, filenames in os.walk(
                src_res, topdown=True, followlinks=False
            ):
                dp = Path(dirpath).resolve()
                if dp == src_res:
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if d not in _TOPLEVEL_TARBALL_EXCLUDE_DIRS
                    ]
                dirnames[:] = [d for d in dirnames if not _prune_dirname(d)]
                for name in filenames:
                    if name.endswith('.pyc'):
                        continue
                    full = (Path(dirpath) / name).resolve()
                    if not full.is_file():
                        continue
                    try:
                        rel = full.relative_to(src_res)
                    except ValueError:
                        continue
                    if any(_prune_dirname(p) for p in rel.parts):
                        continue
                    arcname = Path(_TARBALL_TOPDIR) / rel
                    tar.add(full, arcname=arcname.as_posix())
    except BaseException:
        tar_path.unlink(missing_ok=True)
        raise
    return tar_path


def _tarball_cache_key(source_root: Path) -> str | None:
    """Return a filesystem-safe cache key, or None if git metadata is unavailable."""
    root = source_root.expanduser().resolve()
    if not (root / '.git').exists():
        return None
    try:
        head = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        if not head:
            return None
        st = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if st.returncode != 0:
            return None
        dirty = st.stdout.strip()
        if dirty:
            h = hashlib.sha256(dirty.encode('utf-8', errors='replace')).hexdigest()[:16]
            return f'{head}-d{h}'
        return head
    except (subprocess.CalledProcessError, FileNotFoundError, TimeoutError, OSError):
        return None


def _resolve_cached_or_build_tarball(
    source_root: Path,
    cache_dir_raw: str | None,
    logger,
) -> tuple[Path, bool]:
    """Return (tar_path, unlink_when_done)."""
    cache_key = _tarball_cache_key(source_root)
    if cache_dir_raw and cache_key:
        cache_dir = Path(cache_dir_raw).expanduser().resolve()
        cached = cache_dir / f'ms_agent_tb_{cache_key}.tar.gz'
        if cached.is_file() and cached.stat().st_size > 0:
            logger.info('Using tarball cache %s', cached)
            return cached, False

    tar_path = _build_source_tarball(source_root)
    if cache_dir_raw and cache_key:
        cache_dir = Path(cache_dir_raw).expanduser().resolve()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            dest = cache_dir / f'ms_agent_tb_{cache_key}.tar.gz'
            shutil.copy2(tar_path, dest)
            logger.info('Wrote tarball cache %s', dest)
        except OSError as e:
            logger.warning('Could not write tarball cache under %s: %s', cache_dir, e)
    return tar_path, True


class MsAgentTerminalBenchAgent(BaseAgent):
    """Runs the ``ms-agent`` CLI inside the Harbor task container."""

    SUPPORTS_ATIF: bool = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger=None,
        **kwargs,
    ):
        kwargs.pop('llm', None)
        super().__init__(logs_dir, model_name, logger)

        raw_timeout = os.environ.get(_MS_AGENT_TIMEOUT_ENV)
        if raw_timeout:
            try:
                self._run_timeout_sec = int(raw_timeout)
            except ValueError:
                self._run_timeout_sec = None
        else:
            self._run_timeout_sec = None

        raw_src = os.environ.get(_MS_AGENT_SOURCE_ROOT_ENV)
        self._source_root = (
            Path(raw_src).expanduser().resolve()
            if (raw_src and raw_src.strip())
            else None
        )

        self._pip_spec = os.environ.get(_MS_AGENT_PIP_SPEC_ENV, _DEFAULT_PIP_SPEC)
        self._install_record = (
            f'local:{self._source_root}'
            if self._source_root is not None
            else self._pip_spec
        )

        raw_setup_timeout = os.environ.get(_MS_AGENT_SETUP_TIMEOUT_ENV)
        if raw_setup_timeout:
            try:
                self._setup_timeout_sec = max(60, int(raw_setup_timeout))
            except ValueError:
                self._setup_timeout_sec = _DEFAULT_SETUP_TIMEOUT_SEC
        else:
            self._setup_timeout_sec = _DEFAULT_SETUP_TIMEOUT_SEC

        _default_pip_strategy = (
            'pypi_then_local' if self._source_root is not None else 'local'
        )
        self._pip_strategy = os.environ.get(
            _MS_AGENT_PIP_STRATEGY_ENV, _default_pip_strategy
        ).strip().lower()
        if self._pip_strategy not in ('local', 'pypi_then_local'):
            self._pip_strategy = _default_pip_strategy
        raw_tar_cache = os.environ.get(_MS_AGENT_TARBALL_CACHE_DIR_ENV, '').strip()
        self._tarball_cache_dir = raw_tar_cache or None

    @staticmethod
    def name() -> str:
        return 'ms-agent-terminal-bench'

    def version(self) -> str | None:
        return __version__

    def _render_agent_yaml(self) -> str:
        """Build YAML using credentials present on the host running Harbor."""
        extra_model = os.environ.get('TERMINAL_BENCH_MODEL')

        if os.environ.get('DASHSCOPE_API_KEY'):
            model = extra_model or self.model_name or 'qwen-plus'
            key = os.environ['DASHSCOPE_API_KEY']
            base = os.getenv(
                'DASHSCOPE_API_BASE',
                'https://dashscope.aliyuncs.com/compatible-mode/v1',
            )
            return (
                'llm:\n'
                '  service: dashscope\n'
                f'  model: {model}\n'
                f'  dashscope_api_key: {repr(key)}\n'
                f'  dashscope_base_url: {repr(base)}\n'
                'generation_config:\n'
                '  temperature: 0.2\n'
                '  stream: false\n'
                'prompt:\n'
                '  system: |\n'
                '    You are an autonomous coding agent in a Linux terminal.\n'
                '    Use tools as needed and complete the task instructions.\n'
                'max_chat_round: 30\n'
                'tool_call_timeout: 120\n'
                'tool_call_timeout_max: 600\n'
                'tools:\n'
                '  workspace_policy:\n'
                '    mcp: false\n'
                '    allow_roots:\n'
                '      - /app\n'
                '  file_system:\n'
                '    mcp: false\n'
                '    include:\n'
                '      - write_file\n'
                '      - read_file\n'
                '      - edit_file\n'
                '      - grep\n'
                '      - glob\n'
                '  code_executor:\n'
                '    mcp: false\n'
                '    implementation: python_env\n'
                '    include:\n'
                '      - shell_executor\n'
                '  web_search:\n'
                '    mcp: false\n'
                '    engine: arxiv\n'
                '    fetcher: jina_reader\n'
                '    fetch_content: true\n'
                '    include:\n'
                '      - web_search\n'
                '      - fetch_page\n'
            )

        if os.environ.get('OPENAI_API_KEY'):
            model = extra_model or self.model_name or 'gpt-4.1-mini'
            key = os.environ['OPENAI_API_KEY']
            base = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
            return (
                'llm:\n'
                '  service: openai\n'
                f'  model: {model}\n'
                f'  openai_api_key: {repr(key)}\n'
                f'  openai_base_url: {repr(base)}\n'
                'generation_config:\n'
                '  temperature: 0.2\n'
                '  stream: false\n'
                'prompt:\n'
                '  system: |\n'
                '    You are an autonomous coding agent in a Linux terminal.\n'
                '    Use tools as needed and complete the task instructions.\n'
                'max_chat_round: 30\n'
                'tool_call_timeout: 120\n'
                'tool_call_timeout_max: 600\n'
                'tools:\n'
                '  workspace_policy:\n'
                '    mcp: false\n'
                '    allow_roots:\n'
                '      - /app\n'
                '  file_system:\n'
                '    mcp: false\n'
                '    include:\n'
                '      - write_file\n'
                '      - read_file\n'
                '      - edit_file\n'
                '      - grep\n'
                '      - glob\n'
                '  code_executor:\n'
                '    mcp: false\n'
                '    implementation: python_env\n'
                '    include:\n'
                '      - shell_executor\n'
                '  web_search:\n'
                '    mcp: false\n'
                '    engine: arxiv\n'
                '    fetcher: jina_reader\n'
                '    fetch_content: true\n'
                '    include:\n'
                '      - web_search\n'
                '      - fetch_page\n'
            )

        raise RuntimeError(
            'No LLM credentials found for MsAgentTerminalBenchAgent: set '
            'DASHSCOPE_API_KEY or OPENAI_API_KEY in the host environment.'
        )

    async def setup(self, environment: BaseEnvironment) -> None:
        vdir = _VENV_DIR
        tar_host: Path | None = None
        unlink_tarball = False

        try:
            if self._source_root is not None:
                tar_host, unlink_tarball = _resolve_cached_or_build_tarball(
                    self._source_root,
                    self._tarball_cache_dir,
                    self.logger,
                )
                await environment.upload_file(tar_host, _CONTAINER_TARBALL_PATH)
                harbor_req = f'"{_CONTAINER_PKG_DIR}/{_HARBOR_REQUIREMENTS}"'
                if self._pip_strategy == 'pypi_then_local':
                    pip_spec_q = shlex.quote(self._pip_spec)
                    pip_install = (
                        f'rm -rf "{_CONTAINER_PKG_DIR}" && mkdir -p /tmp && '
                        f'tar xzf "{_CONTAINER_TARBALL_PATH}" -C /tmp && '
                        'python -m pip install --no-cache-dir -U --no-deps '
                        f'{pip_spec_q} loguru arxiv && '
                        f'python -m pip install --no-cache-dir -U -r {harbor_req} && '
                        'python -m pip install --no-cache-dir --no-deps -U '
                        f'"{_CONTAINER_PKG_DIR}"'
                    )
                else:
                    pip_install = (
                        f'rm -rf "{_CONTAINER_PKG_DIR}" && mkdir -p /tmp && '
                        f'tar xzf "{_CONTAINER_TARBALL_PATH}" -C /tmp && '
                        'python -m pip install --no-cache-dir --no-deps -U '
                        f'"{_CONTAINER_PKG_DIR}" loguru arxiv && '
                        f'python -m pip install --no-cache-dir -U -r {harbor_req}'
                    )
            else:
                pip_spec_q = shlex.quote(self._pip_spec)
                pip_install = (
                    'python -m pip install --no-cache-dir -U --no-deps '
                    f'{pip_spec_q} loguru arxiv'
                )

            install_cmd = (
                'set -euo pipefail; '
                'if command -v apt-get >/dev/null 2>&1; then '
                '  export DEBIAN_FRONTEND=noninteractive; '
                '  apt-get update -qq && apt-get install -y -qq python3-venv python3-pip || true; '
                'fi; '
                'if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then '
                '  if command -v apk >/dev/null 2>&1; then '
                '    apk add --no-cache python3 py3-pip; '
                '  elif command -v yum >/dev/null 2>&1; then '
                '    yum install -y python3 python3-pip; '
                '  else '
                '    echo "Cannot bootstrap Python in this image" >&2; exit 127; '
                '  fi; '
                'fi; '
                'PY="$(command -v python3 || command -v python)"; '
                f'rm -rf "{vdir}"; '
                f'"$PY" -m venv "{vdir}" || "$PY" -m venv --without-pip "{vdir}" || true; '
                f'if [ ! -f "{vdir}/bin/activate" ]; then echo "Failed to create venv"; exit 1; fi; '
                f'. "{vdir}/bin/activate"; '
                'if ! command -v pip >/dev/null 2>&1; then '
                '  PYVER="$(python -c \'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")\')"; '
                '  case "$PYVER" in '
                '    3.8) GET_PIP_URL="https://bootstrap.pypa.io/pip/3.8/get-pip.py" ;; '
                '    3.9) GET_PIP_URL="https://bootstrap.pypa.io/pip/3.9/get-pip.py" ;; '
                '    *) GET_PIP_URL="https://bootstrap.pypa.io/get-pip.py" ;; '
                '  esac; '
                '  wget -qO /tmp/get-pip.py "$GET_PIP_URL" && python /tmp/get-pip.py; '
                'fi; '
                'python -m pip install --no-cache-dir -U pip setuptools wheel; '
                f'{pip_install}; '
                f'"{vdir}/bin/python" -c "import ms_agent.version; print(\\"ms-agent-import-ok\\")"'
            )
            result = await environment.exec(
                install_cmd, timeout_sec=self._setup_timeout_sec
            )
            if result.return_code != 0:
                msg = (
                    f'ms-agent install failed (exit {result.return_code}). '
                    f'stdout={result.stdout!r} stderr={result.stderr!r}'
                )
                self.logger.error(msg)
                raise RuntimeError(msg)

            cfg_text = self._render_agent_yaml()
            with tempfile.NamedTemporaryFile(
                mode='w',
                suffix='.yaml',
                delete=False,
                encoding='utf-8',
            ) as tmp:
                tmp.write(cfg_text)
                tmp_path = Path(tmp.name)

            try:
                await environment.upload_file(tmp_path, _CONTAINER_CONFIG_PATH)
            finally:
                tmp_path.unlink(missing_ok=True)
        finally:
            if tar_host is not None and unlink_tarball:
                tar_host.unlink(missing_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        quoted_instr = shlex.quote(instruction)
        py = f'"{_VENV_DIR}/bin/python"'
        inner = (
            f'{py} -m ms_agent.cli.cli run --trust_remote_code true '
            f'--config {_CONTAINER_CONFIG_PATH} --query {quoted_instr}'
        )
        cmd = f'bash -lc {shlex.quote(inner)}'
        result = await environment.exec(cmd, timeout_sec=self._run_timeout_sec)
        meta = {
            'stdout': result.stdout,
            'stderr': result.stderr,
            'return_code': result.return_code,
            'pip_spec': self._install_record,
            'config_path': _CONTAINER_CONFIG_PATH,
        }
        context.metadata = meta

