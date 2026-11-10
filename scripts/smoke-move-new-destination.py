"""Executa o smoke de move/recovery com destino inicialmente ausente."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("smoke_move_recovery", Path(__file__).with_name("smoke-move-recovery.py"))
modulo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modulo)

if __name__ == "__main__":
    modulo.main(destino_existe=False)
