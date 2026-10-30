"""Keep files and editable projects together, including sequence parts."""
import copy
from pathlib import Path
import shutil

MAX_SEQUENCE_STEPS = 32


def animation_parts(spec):
    """Yield the file-backed descriptions inside an animation."""
    if not isinstance(spec, dict):
        raise ValueError('Неверное описание анимации.')
    if 'sequence' not in spec:
        yield '', spec
        return
    steps = spec['sequence']
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_SEQUENCE_STEPS:
        raise ValueError(f'В сценарии должно быть от 1 до {MAX_SEQUENCE_STEPS} частей.')
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get('animation'), dict):
            raise ValueError(f'Часть {index + 1}: выберите анимацию.')
        if 'sequence' in step['animation']:
            raise ValueError('Часть сценария должна быть отдельной анимацией.')
        yield f'_step_{index + 1}', step['animation']


def resolve_animation_assets(spec, base):
    output = copy.deepcopy(spec)
    for _suffix, part in animation_parts(output):
        for key in ('file', 'project'):
            if key == 'file' or key in part:
                part[key] = str((base / Path(part[key])).resolve())
    return output


def copy_animation_assets(spec, folder, stem, *, relative_to=None, only_from=None):
    output = copy.deepcopy(spec)
    for suffix, part in animation_parts(output):
        source = Path(part['file']).resolve()
        if only_from is not None and source.parent != only_from:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (stem + suffix + source.suffix.lower())
        if source != target.resolve():
            shutil.copy2(source, target)
        part['file'] = target.relative_to(relative_to).as_posix() if relative_to else str(target)
        if part.get('project'):
            project = folder / (stem + suffix + '.pixel.json')
            if Path(part['project']).resolve() != project.resolve():
                shutil.copy2(part['project'], project)
            part['project'] = project.relative_to(relative_to).as_posix() if relative_to else str(project)
    return output
