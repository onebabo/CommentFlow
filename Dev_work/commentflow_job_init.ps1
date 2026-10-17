# CommentFlow 작업 준비 (PowerShell)
#
# 현재 세션에 적용:
#   . D:\workspaces\github\onebabo\CommentFlow\Dev_work\commentflow_job_init.ps1
#
# 새 창으로 준비:
#   Dev_work\commentflow_job_init.cmd   (또는 commentflow_job_init.vbs 더블클릭)
#   또는 powershell -NoExit -ExecutionPolicy Bypass -File D:\workspaces\github\onebabo\CommentFlow\Dev_work\commentflow_job_init.ps1
#
# 가상환경(.venv)이 없으면 최초 1회 자동 생성 + requirements.txt 설치

$ErrorActionPreference = 'Stop'

$ProjectRoot = 'D:\workspaces\github\onebabo\CommentFlow'
$VenvDir     = Join-Path $ProjectRoot '.venv'
$VenvScripts = Join-Path $VenvDir 'Scripts'
$ActivatePs1 = Join-Path $VenvScripts 'Activate.ps1'
$VenvPython  = Join-Path $VenvScripts 'python.exe'
$Requirements = Join-Path $ProjectRoot 'requirements.txt'
$ConfigIni   = Join-Path $ProjectRoot 'config.ini'
$ConfigExample = Join-Path $ProjectRoot 'config.example.ini'

# 콘솔 한글 출력 (Windows PowerShell 5.x)
try {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [Console]::InputEncoding  = $utf8
    [Console]::OutputEncoding = $utf8
    $OutputEncoding = $utf8
    $null = cmd /c "chcp 65001 >NUL"
} catch {}

Write-Host ''
Write-Host '=== CommentFlow 작업 준비 ===' -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    Write-Error "프로젝트 경로가 없습니다: $ProjectRoot"
    return
}

# 가상환경 최초 생성
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host "가상환경이 없어 새로 만듭니다: $VenvDir" -ForegroundColor Yellow
    $basePy = $null
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    $pathPython = Get-Command python -ErrorAction SilentlyContinue
    if ($pathPython) { $basePy = $pathPython.Source }
    elseif (Test-Path -LiteralPath 'C:\Python\Python313\python.exe') { $basePy = 'C:\Python\Python313\python.exe' }
    if ($pyLauncher) { & py -3 -m venv $VenvDir }
    elseif ($basePy) { & $basePy -m venv $VenvDir }
    else {
        Write-Error 'Python 을 찾지 못했습니다. Python 3.13 설치 후 다시 실행하세요.'
        return
    }
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        Write-Error "가상환경 생성에 실패했습니다: $VenvDir"
        return
    }
    & $VenvPython -m pip install --upgrade pip
    & $VenvPython -m pip install -r $Requirements
    Write-Host '가상환경 준비 완료' -ForegroundColor Green
}
if (-not (Test-Path -LiteralPath $ActivatePs1)) {
    Write-Error "가상환경 활성화 스크립트가 없습니다: $ActivatePs1"
    return
}

Set-Location -LiteralPath $ProjectRoot

# 작업용 환경변수
$env:COMMENTFLOW_PROJECT = $ProjectRoot
$env:PYTHONUTF8   = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue

# venv 활성화 (VIRTUAL_ENV / PATH)
. $ActivatePs1

# activate 이후에도 venv Scripts를 PATH 최상단에 고정
$env:VIRTUAL_ENV = $VenvDir
$pathParts = @($VenvScripts) + @(
    ($env:PATH -split ';' | Where-Object {
        $_ -and ($_ -ne $VenvScripts) -and ($_ -notlike '*\WindowsApps')
    })
)
$env:PATH = ($pathParts -join ';')

# 프롬프트: (.venv) cmt PS
function global:prompt {
    Write-Host -NoNewline -ForegroundColor Green '(.venv) '
    Write-Host -NoNewline -ForegroundColor Magenta 'cmt '
    return 'PS '
}

$py = Get-Command python -ErrorAction SilentlyContinue
Write-Host "cwd   : $PWD"
Write-Host "python: $(if ($py) { $py.Source } else { '(없음)' })"
if (-not (Test-Path -LiteralPath $ConfigIni)) {
    Write-Host "주의: DB 설정 파일이 없습니다 → $ConfigIni" -ForegroundColor Yellow
    Write-Host "      $ConfigExample 를 config.ini 로 복사한 뒤 값을 채우세요." -ForegroundColor Yellow
}
Write-Host ''
Write-Host '준비 완료. 예:' -ForegroundColor Green
Write-Host '  python -c "import db"'
Write-Host ''
