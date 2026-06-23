from ms_agent.plugins.config_manager import PluginConfigManager
from ms_agent.plugins.types import PluginRecord


def test_config_manager_crud_and_project_override(tmp_path):
    global_dir = tmp_path / '.ms_agent'
    project = tmp_path / 'project'
    project.mkdir()
    manager = PluginConfigManager(global_dir=global_dir, project_root=project)

    global_record = PluginRecord(
        id='demo',
        path=str(global_dir / 'plugins' / 'demo'),
        enabled=True,
        format='claude',
        manifest_path='.claude-plugin/plugin.json',
    )
    project_record = PluginRecord(
        id='demo',
        path=str(project / '.ms-agent' / 'plugins' / 'demo'),
        enabled=False,
        format='claude',
        manifest_path='.claude-plugin/plugin.json',
    )

    manager.upsert(global_record, scope='global')
    manager.upsert(project_record, scope='project')

    merged = manager.list('merged')
    assert len(merged) == 1
    assert merged[0].enabled is False
    assert merged[0].scope == 'project'

    manager.set_enabled('demo', True, scope='project')
    assert manager.get('demo', scope='project').enabled is True

    manager.remove('demo', scope='project')
    assert manager.get('demo', scope='merged').scope == 'global'
