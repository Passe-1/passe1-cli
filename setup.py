"""O wheel contém o motor nativo compilado para a plataforma de build."""
import os
import platform
import sys
from pathlib import Path
from runpy import run_path

from setuptools import setup
from wheel.bdist_wheel import bdist_wheel

ferramentas_tag = run_path("scripts/wheel_tag.py")


class WheelNativo(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        _, _, plataforma = super().get_tag()
        if sys.platform == "darwin":
            binarios = Path("passe1_engine/bin")
            minimos = [ferramentas_tag["minimo_binario"](binarios / nome) for nome in ("opencode", "rg")]
            plataforma = ferramentas_tag["tag_macos"](plataforma, platform.machine(), minimos)
        return "py3", "none", plataforma


if "bdist_wheel" in sys.argv and not Path("passe1_engine/bin", "opencode.exe" if os.name == "nt" else "opencode").is_file():
    raise RuntimeError("Compile o motor OpenCode antes de construir o wheel.")
if "bdist_wheel" in sys.argv and not Path("passe1_engine/bin", "rg.exe" if os.name == "nt" else "rg").is_file():
    raise RuntimeError("Instale o ripgrep verificado antes de construir o wheel.")

setup(cmdclass={"bdist_wheel": WheelNativo})
