# Windows 작업 스케줄러 등록 — 매일 4회(07:30 / 11:30 / 16:30 / 21:30) 전체 수집.
# 관리자 권한 불필요(현재 사용자 컨텍스트). 재실행 시 기존 작업을 교체한다.
#   실행:  powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
#   해제:  Unregister-ScheduledTask -TaskName "KeywordRankMonitor" -Confirm:$false

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot          # 프로젝트 루트
$python = (Get-Command python).Source
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# cmd /c 래핑으로 stdout/err 를 일자별 로그에 남긴다(스케줄러는 콘솔이 없음).
$cmd = "`"$python`" run.py --all >> `"$logDir\run_%date:~0,4%%date:~5,2%%date:~8,2%.log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $cmd" -WorkingDirectory $root

$triggers = @("07:30", "11:30", "16:30", "21:30") | ForEach-Object {
    New-ScheduledTaskTrigger -Daily -At $_
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -MultipleInstances IgnoreNew   # 직전 런이 안 끝났으면 중복 실행 금지

Register-ScheduledTask -TaskName "KeywordRankMonitor" `
    -Action $action -Trigger $triggers -Settings $settings -Force | Out-Null

Write-Host "등록 완료: KeywordRankMonitor (매일 07:30/11:30/16:30/21:30, 로그: $logDir)"
