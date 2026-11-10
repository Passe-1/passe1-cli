$ErrorActionPreference = 'Stop'
$arquivo = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetRandomFileName() + '.py')
try {
    Invoke-WebRequest -Uri 'https://github.com/Passe-1/passe1-cli/releases/latest/download/install.py' -OutFile $arquivo
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 $arquivo
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python $arquivo
    } else {
        throw 'Instale Python 3 para continuar.'
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Remove-Item -LiteralPath $arquivo -Force -ErrorAction SilentlyContinue
}
