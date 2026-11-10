"""Cria servidor em Job Object de forma atômica, sem filhos órfãos no Windows."""
import ctypes
import locale
import os
import subprocess
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
        ("max_working", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IOCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
        ("title", wintypes.LPWSTR), ("x", wintypes.DWORD), ("y", wintypes.DWORD),
        ("xsize", wintypes.DWORD), ("ysize", wintypes.DWORD), ("xchars", wintypes.DWORD),
        ("ychars", wintypes.DWORD), ("fill", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("show", wintypes.WORD), ("reserved2_size", wintypes.WORD),
        ("reserved2", ctypes.POINTER(ctypes.c_byte)), ("stdin", wintypes.HANDLE),
        ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", _StartupInfo), ("attributes", ctypes.c_void_p)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", wintypes.HANDLE), ("thread", wintypes.HANDLE),
        ("pid", wintypes.DWORD), ("tid", wintypes.DWORD),
    ]


def _api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ((ctypes.c_void_p, wintypes.LPCWSTR), wintypes.HANDLE),
        "SetInformationJobObject": ((wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD), wintypes.BOOL),
        "InitializeProcThreadAttributeList": ((ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)), wintypes.BOOL),
        "UpdateProcThreadAttribute": ((ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p), wintypes.BOOL),
        "DeleteProcThreadAttributeList": ((ctypes.c_void_p,), None),
        "CreateProcessW": ((wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo)), wintypes.BOOL),
        "ResumeThread": ((wintypes.HANDLE,), wintypes.DWORD),
        "WaitForSingleObject": ((wintypes.HANDLE, wintypes.DWORD), wintypes.DWORD),
        "GetExitCodeProcess": ((wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)), wintypes.BOOL),
        "TerminateProcess": ((wintypes.HANDLE, wintypes.UINT), wintypes.BOOL),
        "CloseHandle": ((wintypes.HANDLE,), wintypes.BOOL),
    }
    for nome, (argumentos, retorno) in signatures.items():
        funcao = getattr(kernel, nome)
        funcao.argtypes = argumentos
        funcao.restype = retorno
    return kernel


def _erro():
    return ctypes.WinError(ctypes.get_last_error())


class _Processo:
    def __init__(self, kernel, info, job, stdout, comando):
        self._kernel, self._handle, self._passe1_job = kernel, info.process, job
        self.pid, self.stdout, self.args = info.pid, stdout, comando
        self.returncode = None

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        resultado = self._kernel.WaitForSingleObject(self._handle, 0)
        if resultado == 0x102:
            return None
        if resultado != 0:
            raise _erro()
        codigo = wintypes.DWORD()
        if not self._kernel.GetExitCodeProcess(self._handle, ctypes.byref(codigo)):
            raise _erro()
        self.returncode = codigo.value
        return self.returncode

    def wait(self, timeout=None):
        limite = 0xFFFFFFFF if timeout is None else min(max(int(timeout * 1000), 0), 0xFFFFFFFE)
        resultado = self._kernel.WaitForSingleObject(self._handle, limite)
        if resultado == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if resultado != 0:
            raise _erro()
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self._kernel.TerminateProcess(self._handle, 1):
            raise _erro()

    kill = terminate


def iniciar(comando, *, env=None, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True):
    if stdout != subprocess.PIPE or stderr != subprocess.DEVNULL or not text:
        raise ValueError("Configuração de canais do servidor não suportada.")
    import msvcrt

    env = os.environ.copy() if env is None else env
    kernel = _api()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise _erro()
    leitura = escrita = None
    atributos = None
    info = _ProcessInfo()
    criado = False
    try:
        limites = _ExtendedLimits()
        limites.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limites), ctypes.sizeof(limites)):
            raise _erro()
        leitura, escrita = os.pipe()
        with open(os.devnull, "r+b", buffering=0) as nulo:
            handles = (wintypes.HANDLE * 3)(msvcrt.get_osfhandle(nulo.fileno()), msvcrt.get_osfhandle(escrita), msvcrt.get_osfhandle(nulo.fileno()))
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            tamanho = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(tamanho))
            buffer = ctypes.create_string_buffer(tamanho.value)
            atributos = ctypes.cast(buffer, ctypes.c_void_p)
            if not kernel.InitializeProcThreadAttributeList(atributos, 2, 0, ctypes.byref(tamanho)):
                atributos = None
                raise _erro()
            jobs = (wintypes.HANDLE * 1)(job)
            herdados = (wintypes.HANDLE * 2)(handles[0], handles[1])
            for chave, valor in ((0x00020002, herdados), (0x0002000D, jobs)):
                if not kernel.UpdateProcThreadAttribute(atributos, 0, chave, ctypes.byref(valor), ctypes.sizeof(valor), None, None):
                    raise _erro()
            startup = _StartupInfoEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = handles
            startup.attributes = atributos
            linha = ctypes.create_unicode_buffer(subprocess.list2cmdline(comando))
            ambiente = ctypes.create_unicode_buffer("".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda item: item[0].upper())) + "\0")
            flags = 0x00080000 | 0x00000400 | 0x00000004  # EXTENDED_STARTUPINFO, UNICODE_ENVIRONMENT, SUSPENDED
            if not kernel.CreateProcessW(None, linha, None, None, True, flags, ambiente, None, ctypes.byref(startup), ctypes.byref(info)):
                raise _erro()
            criado = True
            if kernel.ResumeThread(info.thread) != 1:
                raise OSError("Não foi possível retomar o servidor suspenso.")
        os.close(escrita)
        escrita = None
        fluxo = os.fdopen(leitura, "r", encoding=locale.getpreferredencoding(False), errors="replace")
        leitura = None
        return _Processo(kernel, info, job, fluxo, comando)
    except BaseException:
        if criado:
            kernel.TerminateProcess(info.process, 1)
            kernel.CloseHandle(info.process)
        kernel.CloseHandle(job)
        raise
    finally:
        if info.thread:
            kernel.CloseHandle(info.thread)
        if atributos:
            kernel.DeleteProcThreadAttributeList(atributos)
        if leitura is not None:
            os.close(leitura)
        if escrita is not None:
            os.close(escrita)


def fechar(processo):
    job = processo._passe1_job
    if job:
        processo._passe1_job = None
        if not processo._kernel.CloseHandle(job):
            raise _erro()
        processo._kernel.CloseHandle(processo._handle)
