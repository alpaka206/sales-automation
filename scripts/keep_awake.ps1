# Keep the system awake — prevents sleep + monitor off + screensaver.
# Calls Windows API SetThreadExecutionState. No admin needed.
# Effect is process-scoped: closing this window restores normal behavior.

$signature = @'
[DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@

$type = Add-Type -MemberDefinition $signature -Name Power -Namespace Win32 -PassThru

# Flag combinations:
#   ES_CONTINUOUS       = 0x80000000  (this setting persists until next call)
#   ES_SYSTEM_REQUIRED  = 0x00000001  (keep system awake)
#   ES_DISPLAY_REQUIRED = 0x00000002  (keep display on - blocks screensaver too)
$flags = [uint32]"0x80000000" -bor [uint32]"0x00000001" -bor [uint32]"0x00000002"

$null = $type::SetThreadExecutionState($flags)

$start = Get-Date
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  컴퓨터 깨어있음 모드 활성화 (Ralph Loop 용)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  - 절전 모드 비활성화" -ForegroundColor Green
Write-Host "  - 화면 끄기 비활성화" -ForegroundColor Green
Write-Host "  - 화면 보호기 비활성화" -ForegroundColor Green
Write-Host ""
Write-Host "  종료하려면 이 창을 닫거나 Ctrl+C 누르세요." -ForegroundColor Yellow
Write-Host "  종료하면 자동으로 원래 절전 설정으로 돌아갑니다." -ForegroundColor Yellow
Write-Host ""
Write-Host "  시작 시각: $start" -ForegroundColor Gray
Write-Host ""

try {
    while ($true) {
        $elapsed = (Get-Date) - $start
        $hh = [math]::Floor($elapsed.TotalHours)
        $mm = $elapsed.Minutes
        Write-Host "`r  경과: ${hh}시간 ${mm}분 (활성)              " -NoNewline -ForegroundColor Gray
        Start-Sleep -Seconds 30
    }
}
finally {
    # 종료 시 깨어있음 모드 해제 — ES_CONTINUOUS 만 호출하면 해제됨
    $null = $type::SetThreadExecutionState([uint32]"0x80000000")
    Write-Host ""
    Write-Host ""
    Write-Host "깨어있음 모드 해제됨. 원래 절전 설정으로 복귀." -ForegroundColor Cyan
}
