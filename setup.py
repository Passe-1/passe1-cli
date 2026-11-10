"""O wheel contém o motor nativo compilado para a plataforma de build."""
import os
import sys
from pathlib import Path

from setuptools import setup
from wheel.bdist_wheel import bdist_wheel


class WheelNativo(bdist_wheel):
    def finalize_options(self):
        super().finalize_options()
        self.root_is_pure = False

    def get_tag(self):
        _, _, plataforma = super().get_tag()
        return "py3", "none", plataforma


if "bdist_wheel" in sys.argv and not Path("passe1_engine/bin", "opencode.exe" if os.name == "nt" else "opencode").is_file():
    raise RuntimeError("Compile o motor OpenCode antes de construir o wheel.")

setup(cmdclass={"bdist_wheel": WheelNativo})
