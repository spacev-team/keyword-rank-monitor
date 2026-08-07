# Windows 작업 스케줄러 등록 — 매일 07:00 구글 SERP 로컬 브라우저 수집.
# 07:30 daily Actions 런이 소비할 인박스(google-serp 브랜치)를 미리 채운다.
# 관리자 권한 불필요(현재 사용자 컨텍스트). 재실행 시 기존 작업을 교체한다.
#   사전 준비:  pip install playwright ; playwright install chromium
#   실행:  powershell -ExecutionPolicy Bypass -File scripts\register_google_task.ps1
#   해제:  Unregister-ScheduledTask -TaskName "KeywordRankMonitor-Google" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # 프로젝트 루트
$python = (Get-Command python).Source
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# cmd /c 래핑으로 stdout/err 를 일자별 로그에 남긴다(스케줄러는 콘솔이 없음).
$cmd = "`"$python`" scripts\collect_google_local.py >> `"$logDir\google_%date:~0,4%%date:~5,2%%date:~8,2%.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -Daily -At "07:00"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew   # 직전 런이 안 끝났으면 중복 실행 금지

Register-ScheduledTask -TaskName "KeywordRankMonitor-Google" `
    -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Host "등록 완료: KeywordRankMonitor-Google (매일 07:00, 로그: $logDir)"
