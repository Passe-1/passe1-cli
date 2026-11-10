import importlib.util
from pathlib import Path


def _normalizar(tag, maquina, minimos=()):
    caminho = Path(__file__).resolve().parents[1] / 'scripts' / 'wheel_tag.py'
    spec = importlib.util.spec_from_file_location('wheel_tag', caminho)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.tag_macos(tag, maquina, minimos)


def test_wheel_arm_nao_e_anunciado_como_universal():
    assert _normalizar('macosx_10_13_universal2', 'arm64') == 'macosx_11_0_arm64'


def test_wheel_intel_nao_e_anunciado_como_universal():
    assert _normalizar('macosx_10_13_universal2', 'x86_64') == 'macosx_10_13_x86_64'


def test_wheel_preserva_minimo_mais_recente():
    assert _normalizar('macosx_14_0_arm64', 'arm64') == 'macosx_14_0_arm64'


def test_wheel_recusa_arquitetura_desconhecida():
    import pytest
    with pytest.raises(ValueError):
        _normalizar('macosx_10_13_universal2', 'desconhecida')


def test_wheel_respeita_minimo_dos_binarios():
    assert _normalizar("macosx_11_0_arm64", "arm64", [(13, 0), (11, 0)]) == "macosx_13_0_arm64"


def test_minimo_binario_arredonda_patch(monkeypatch):
    import runpy
    modulo = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "wheel_tag.py"))
    for comando, campo in (("LC_BUILD_VERSION", "minos"), ("LC_VERSION_MIN_MACOSX", "version")):
        saida = f"Load command 1\n cmd {comando}\n {campo} 13.2.1\n"
        monkeypatch.setattr(modulo["subprocess"], "check_output", lambda *args, saida=saida, **kwargs: saida)
        assert modulo["minimo_binario"]("opencode") == (13, 3)
