"""Convert-to-ms-agent project memory landing (TUI / WebUI same file).

Inbound MEMORY.md must land at ``<work>/.ms_agent/memory/MEMORY.md`` — the
path both TUI (``apply_project_memory`` + FileBasedBackend) and WebUI
(``GET /api/projects/{id}/memory/doc``) read. Disk is the source of truth.
"""
from __future__ import annotations

from pathlib import Path

from omegaconf import OmegaConf

from ms_agent.agent_hub._commands import build_spec, cmd_convert
from ms_agent.personalization.memory_apply import apply_project_memory
from ms_agent.project.manager import ProjectManager
from ms_agent.project.paths import memory_dir
from ms_agent.tui.app import TuiApp

from tests.e2e.helpers import runtime_for

MARKER = 'E2E_CONVERT_MEM_MARKER'


def _convert_openclaw_memory(work: Path, home_files: Path) -> int:
    src = work.parent / 'openclaw_src'
    src.mkdir(exist_ok=True)
    root = build_spec('openclaw', 'default', str(src)).workspace_root
    root.mkdir(parents=True, exist_ok=True)
    (root / 'SOUL.md').write_text('# Soul\nconverted persona.\n', encoding='utf-8')
    (root / 'MEMORY.md').write_text(
        f'# Memory\n{MARKER}\n', encoding='utf-8')
    return cmd_convert(
        source_fw='openclaw',
        target_fw='ms-agent',
        from_name='default',
        local_dir=str(src),
        out_dir=str(home_files),
        work_dir=str(work),
    )


class TestConvertMemoryLandsInOpenedWork:
    def test_file_on_disk_and_project_memory_enabled(
            self, isolated_home, work_dir):
        rc = _convert_openclaw_memory(work_dir, work_dir.parent / 'ms_home_files')
        assert rc == 0
        mem = memory_dir(work_dir) / 'MEMORY.md'
        assert mem.is_file()
        assert MARKER in mem.read_text(encoding='utf-8')
        pm = ProjectManager(base_dir=str(isolated_home))
        proj = pm.find_by_path(str(work_dir))
        assert proj is not None
        assert proj.memory_enabled is True
        assert (proj.memory_backend or 'file') == 'file'

    def test_webui_adapter_and_http_read_the_same_file(
            self, isolated_home, work_dir, webui, webui_client):
        rc = _convert_openclaw_memory(work_dir, work_dir.parent / 'ms_home_files')
        assert rc == 0
        pm = ProjectManager(base_dir=str(isolated_home))
        proj = pm.find_by_path(str(work_dir))
        assert proj is not None

        from app.backends.ms_agent import memory as webui_memory
        doc = webui_memory.get_doc(proj.id)
        assert MARKER in (doc.content or '')

        listed = webui.projects.get_project(proj.id)
        assert listed.memory_enabled is True

        resp = webui_client.get(f'/api/projects/{proj.id}/memory/doc')
        assert resp.status_code == 200
        body = resp.json()
        assert body['code'] == 0
        assert MARKER in body['data']['content']

        mem_path = memory_dir(work_dir) / 'MEMORY.md'
        assert MARKER in mem_path.read_text(encoding='utf-8')
        assert Path(proj.path).resolve() == work_dir.resolve()

    def test_tui_open_points_runtime_at_the_same_file(
            self, isolated_home, work_dir):
        from ms_agent.memory.unified.config import MemoryConfig
        from ms_agent.memory.unified.storage.file_storage import (
            FileMemoryStorage,
        )

        rc = _convert_openclaw_memory(work_dir, work_dir.parent / 'ms_home_files')
        assert rc == 0
        tui_proj = TuiApp._open_project(str(work_dir))
        assert tui_proj.memory_enabled is True
        rt = runtime_for(work_dir)
        kind = apply_project_memory(rt.config, tui_proj)
        assert kind == 'file'
        node = OmegaConf.select(rt.config, 'memory.unified_memory')
        assert node is not None
        storage = FileMemoryStorage(
            MemoryConfig(base_dir=str(memory_dir(work_dir))))
        assert MARKER in storage.get_content()
        pm = ProjectManager(base_dir=str(isolated_home))
        registered = pm.find_by_path(str(work_dir))
        assert registered is not None
        assert tui_proj.id == registered.id
