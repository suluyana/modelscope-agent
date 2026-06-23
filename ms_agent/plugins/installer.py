from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from ms_agent.plugins.config_manager import PluginConfigManager
from ms_agent.plugins.manifest import PluginManifest
from ms_agent.plugins.types import InstallSource, PluginRecord

try:
    from modelscope import snapshot_download
except ImportError:  # pragma: no cover - optional dependency path
    snapshot_download = None


class UnsupportedPluginSource(ValueError):
    """Raised for install sources outside the current local Phase 0 scope."""


_MARKETPLACE_REPOS: dict[str, str] = {
    'claude-plugins-official': 'anthropics/claude-plugins-official',
}
_MARKETPLACE_ALIAS_RE = re.compile(
    r'^[a-z0-9][a-z0-9._-]*@[a-z0-9][a-z0-9._-]*$',
    re.IGNORECASE,
)


class PluginInstaller:
    """Install plugins into the MS-Agent-owned plugin cache."""

    def __init__(
        self,
        config_manager: PluginConfigManager | None = None,
        *,
        global_root: str | Path = '~/.ms_agent',
        project_root: str | Path | None = None,
    ) -> None:
        self.config_manager = config_manager or PluginConfigManager(
            global_root, project_root)
        self.global_root = Path(global_root).expanduser()
        self.project_root = (
            Path(project_root).expanduser() if project_root else None
        )

    def install(
        self,
        source: str,
        *,
        scope: str = 'global',
        project_path: str | Path | None = None,
        link: bool = False,
        force: bool = False,
        format_hint: str | None = None,
        enabled: bool | None = None,
    ) -> PluginManifest:
        requested_source = source
        source = normalize_install_source(source)
        with self._fetch_source(source) as fetched:
            source_path = fetched.path
            staged_manifest = PluginManifest.parse(
                source_path,
                format_hint=format_hint,
            )
            target = self._target_dir(
                staged_manifest.plugin_id,
                scope=scope,
                project_path=project_path,
            )

            if target.exists() or target.is_symlink():
                if not force:
                    # Idempotent reinstall: keep the existing managed copy and only
                    # refresh plugins.json from its locked manifest.
                    existing = PluginManifest.parse(
                        target,
                        format_hint=staged_manifest.format,
                    )
                    return self._write_record(
                        existing,
                        source=requested_source,
                        fetch_source=source,
                        scope=scope,
                        enabled=enabled,
                        project_path=project_path,
                        resolved_sha=fetched.resolved_sha,
                        record_path=target,
                    )

            install_path = self._stage_install_tree(
                source_path,
                target,
                link=link and fetched.type == 'local',
            )
            manifest = PluginManifest.parse(
                install_path,
                format_hint=staged_manifest.format,
            )
            self._publish_staged_install(install_path, target)
            manifest = PluginManifest.parse(target, format_hint=staged_manifest.format)
            return self._write_record(
                manifest,
                source=requested_source,
                fetch_source=source,
                scope=scope,
                enabled=enabled,
                project_path=project_path,
                resolved_sha=fetched.resolved_sha,
                record_path=target,
            )

    @staticmethod
    def _stage_install_tree(source_path: Path, target: Path, *, link: bool) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging_root = target.parent / '.staging'
        staging_root.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(
            prefix=f'{target.name}_',
            dir=staging_root,
        ))
        shutil.rmtree(staged)
        if link:
            staged.symlink_to(source_path, target_is_directory=True)
        else:
            shutil.copytree(
                source_path,
                staged,
                ignore=shutil.ignore_patterns('.git'),
            )
        return staged

    @staticmethod
    def _publish_staged_install(staged: Path, target: Path) -> None:
        backup: Path | None = None
        if target.exists() or target.is_symlink():
            backup = Path(tempfile.mkdtemp(
                prefix=f'{target.name}_backup_',
                dir=target.parent / '.staging',
            ))
            shutil.rmtree(backup)
            target.rename(backup)
        try:
            staged.rename(target)
        except Exception:
            if backup is not None and (backup.exists() or backup.is_symlink()):
                backup.rename(target)
            raise
        else:
            if backup is not None:
                if backup.is_symlink() or backup.is_file():
                    backup.unlink(missing_ok=True)
                elif backup.is_dir():
                    shutil.rmtree(backup)

    def _write_record(
        self,
        manifest: PluginManifest,
        *,
        source: str,
        fetch_source: str | None = None,
        scope: str,
        enabled: bool | None,
        project_path: str | Path | None,
        resolved_sha: str | None = None,
        record_path: Path | None = None,
    ) -> PluginManifest:
        source_type = _source_type(fetch_source or source)
        record = PluginRecord(
            id=manifest.plugin_id,
            enabled=manifest.enabled if enabled is None else enabled,
            managed_by='ms-agent',
            format=manifest.format,
            manifest_path=manifest.manifest_path,
            source=InstallSource(
                type=source_type,
                uri=source,
                resolved_sha=resolved_sha,
            ),
            path=str(record_path or manifest.root),
            installed_at=datetime.now(timezone.utc).isoformat(),
        )
        manager = self._manager_for_project(project_path)
        manager.upsert(record, scope=scope)  # type: ignore[arg-type]
        return PluginManifest.parse(manifest.root, record=record)

    def _manager_for_project(
        self,
        project_path: str | Path | None,
    ) -> PluginConfigManager:
        if project_path is not None and self.config_manager.project_root is None:
            return PluginConfigManager(self.global_root, project_path)
        return self.config_manager

    def _target_dir(
        self,
        plugin_id: str,
        *,
        scope: str,
        project_path: str | Path | None,
    ) -> Path:
        if scope == 'project':
            root = Path(project_path or self.project_root or '')
            if not str(root):
                raise ValueError('project_path is required for project plugin install')
            return root / '.ms-agent' / 'plugins' / plugin_id
        return self.global_root / 'plugins' / plugin_id

    @staticmethod
    def _resolve_local_source(source: str) -> Path:
        if source.startswith('ms-agent://'):
            raise UnsupportedPluginSource(
                f'MS-Agent plugin URI dispatch is not implemented yet: {source}')
        if source.startswith('file://'):
            parsed = urlparse(source)
            return Path(parsed.path).expanduser().resolve()
        return Path(source).expanduser().resolve()

    def _fetch_source(self, source: str) -> '_FetchedSource':
        if source.startswith('github://'):
            return _fetch_github(source)
        if source.startswith('modelscope://'):
            return _fetch_modelscope(source)
        return _FetchedSource(
            path=self._resolve_local_source(source),
            source=source,
            type='local',
        )


class _FetchedSource:
    def __init__(
        self,
        *,
        path: Path,
        source: str,
        type: str,
        cleanup: tempfile.TemporaryDirectory | None = None,
        resolved_sha: str | None = None,
    ) -> None:
        self.path = path
        self.source = source
        self.type = type
        self.cleanup = cleanup
        self.resolved_sha = resolved_sha

    def __enter__(self) -> '_FetchedSource':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.cleanup is not None:
            self.cleanup.cleanup()


def normalize_install_source(source: str) -> str:
    """Resolve marketplace aliases such as ``hookify@claude-plugins-official``."""
    if source.startswith(('github://', 'modelscope://', 'file://', 'ms-agent://')):
        return source
    if '/' in source or source.startswith('.'):
        return source
    if _MARKETPLACE_ALIAS_RE.match(source):
        plugin_name, marketplace = source.rsplit('@', 1)
        return resolve_marketplace_plugin_uri(plugin_name, marketplace)
    return source


def resolve_marketplace_plugin_uri(
    plugin_name: str,
    marketplace: str,
    *,
    ref: str = 'main',
) -> str:
    repo = _MARKETPLACE_REPOS.get(marketplace)
    if repo is None:
        raise UnsupportedPluginSource(f'Unknown marketplace: {marketplace}')
    subdir = _lookup_marketplace_plugin_path(repo, plugin_name, ref=ref)
    ref_part = f'@{ref}' if ref else ''
    return f'github://{repo}{ref_part}#{subdir}'


def _lookup_marketplace_plugin_path(
    repo: str,
    plugin_name: str,
    *,
    ref: str = 'main',
) -> str:
    url = (
        f'https://raw.githubusercontent.com/{repo}/{ref}'
        f'/.claude-plugin/marketplace.json'
    )
    try:
        with urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except Exception as exc:
        raise UnsupportedPluginSource(
            f'Failed to load marketplace index for {repo}: {exc}') from exc

    for plugin in data.get('plugins', []):
        if plugin.get('name') != plugin_name:
            continue
        source = plugin.get('source')
        if isinstance(source, str):
            return source.lstrip('./')
        if isinstance(source, dict):
            subdir = source.get('path') or source.get('subdir')
            if isinstance(subdir, str) and subdir:
                return subdir.lstrip('./')
        break
    raise UnsupportedPluginSource(
        f'Plugin {plugin_name!r} not found in marketplace {repo}')


def _source_type(source: str) -> str:
    if source.startswith('github://'):
        return 'github'
    if source.startswith('modelscope://'):
        return 'modelscope'
    if _MARKETPLACE_ALIAS_RE.match(source):
        return 'github'
    return 'local'


def _fetch_github(source: str) -> _FetchedSource:
    repo, ref, subdir = _parse_github_uri(source)
    tmp = tempfile.TemporaryDirectory(prefix='ms_agent_plugin_git_')
    clone_dir = Path(tmp.name) / 'repo'
    clone_cmd = [
        'git',
        'clone',
        '--depth',
        '1',
        '--filter=blob:none',
    ]
    if ref:
        clone_cmd.extend(['--branch', ref])
    clone_cmd.extend([f'https://github.com/{repo}.git', str(clone_dir)])
    subprocess.run(clone_cmd, check=True, capture_output=True, text=True)
    if subdir:
        subprocess.run(
            ['git', '-C', str(clone_dir), 'sparse-checkout', 'set', subdir],
            check=True,
            capture_output=True,
            text=True,
        )
    sha = subprocess.run(
        ['git', '-C', str(clone_dir), 'rev-parse', 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return _FetchedSource(
        path=clone_dir / subdir if subdir else clone_dir,
        source=source,
        type='github',
        cleanup=tmp,
        resolved_sha=sha or None,
    )


def _parse_github_uri(source: str) -> tuple[str, str | None, str | None]:
    body = source[len('github://'):]
    repo_part, _, subdir = body.partition('#')
    repo, _, ref = repo_part.partition('@')
    if repo.count('/') != 1:
        raise UnsupportedPluginSource(f'Invalid github plugin URI: {source}')
    return repo, ref or None, subdir or None


def _fetch_modelscope(source: str) -> _FetchedSource:
    if snapshot_download is None:
        raise UnsupportedPluginSource(
            'modelscope is required for modelscope:// plugin install')
    repo, ref, subdir = _parse_modelscope_uri(source)
    local_path = Path(snapshot_download(repo, revision=ref)).expanduser().resolve()
    return _FetchedSource(
        path=local_path / subdir if subdir else local_path,
        source=source,
        type='modelscope',
    )


def _parse_modelscope_uri(source: str) -> tuple[str, str | None, str | None]:
    body = source[len('modelscope://'):]
    repo_part, _, subdir = body.partition('#')
    repo, _, ref = repo_part.partition('@')
    if not repo or '/' not in repo:
        raise UnsupportedPluginSource(f'Invalid modelscope plugin URI: {source}')
    return repo, ref or None, subdir or None
