@echo off
setlocal EnableDelayedExpansion
set "PORT=8080"
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "GITHUB_RAW=https://raw.githubusercontent.com/patchamama/Course-viewer/main"

echo === Course Viewer Launcher ===
echo Directory: %DIR%

:: ── Download helper (PowerShell) ─────────────────────────────────────────────
set "PS_DL=powershell -NoProfile -ExecutionPolicy Bypass -Command Invoke-WebRequest -Uri"

:: ── Auto-download or update app files ────────────────────────────────────────
set "_app_files_exist=1"
for %%F in (proxy.py course-viewer.html) do (
  if not exist "%DIR%\%%F" set "_app_files_exist=0"
)

if "%_app_files_exist%"=="0" (
  :: Fresh install — download without asking
  for %%F in (proxy.py course-viewer.html) do (
    if not exist "%DIR%\%%F" (
      echo Downloading %%F from GitHub...
      powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Invoke-WebRequest -Uri '%GITHUB_RAW%/%%F' -OutFile '%DIR%\%%F' -UseBasicParsing" 2>nul
      if !errorlevel! neq 0 (
        echo ERROR: Failed to download %%F. Check your internet connection.
        pause & exit /b 1
      )
      echo   OK %%F
    )
  )
) else (
  :: Files exist — check for a newer release
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$html = Get-Content '%DIR%\course-viewer.html' -Raw -ErrorAction SilentlyContinue;" ^
    "$localVer = '';" ^
    "if ($html -match \"APP_VERSION = '([0-9][0-9.]*)'\") { $localVer = $Matches[1] };" ^
    "$latestVer = '';" ^
    "try {" ^
      "$rel = Invoke-RestMethod 'https://api.github.com/repos/patchamama/Course-viewer/releases/latest' -TimeoutSec 5;" ^
      "$latestVer = $rel.tag_name -replace '^v','';" ^
    "} catch {};" ^
    "if ($latestVer -and $localVer) {" ^
      "$la = [version]$latestVer; $lo = [version]$localVer;" ^
      "if ($la -gt $lo) {" ^
        "Write-Host '';" ^
        "Write-Host ('  New version available: v' + $latestVer + '  (installed: v' + $localVer + ')');" ^
        "$choice = Read-Host '  Update now? [y/N]';" ^
        "if ($choice -match '^[Yy]') {" ^
          "foreach ($f in @('proxy.py','course-viewer.html')) {" ^
            "Write-Host ('  Updating ' + $f + '...');" ^
            "Invoke-WebRequest -Uri ('%GITHUB_RAW%/' + $f) -OutFile ('%DIR%\\' + $f) -UseBasicParsing;" ^
            "Write-Host ('  OK ' + $f)" ^
          "}" ^
        "}" ^
      "}" ^
    "}"
)

:: Only generate course-viewer.config.json if it does not already exist
if exist "%DIR%\course-viewer.config.json" (
  echo course-viewer.config.json already exists -- skipping generation
  goto :start_server
)

:: Generate course-viewer.config.json via PowerShell (reads course.readme.txt for URL and password)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = '%DIR:\=\\%';" ^
  "$courseUrl = '';" ^
  "$coursePassword = '';" ^
  "$readme = Join-Path $dir 'course.readme.txt';" ^
  "if (Test-Path -LiteralPath $readme) {" ^
    "foreach ($line in (Get-Content -Path $readme -Encoding UTF8)) {" ^
      "if ($line -match '^courseUrl\s*:\s*(.+)$') { $courseUrl = $Matches[1].Trim() }" ^
      "elseif ($line -match '^coursePassword\s*:\s*(.+)$') { $coursePassword = $Matches[1].Trim() }" ^
    "}" ^
  "} else { Write-Host 'INFO: course.readme.txt not found' };" ^
  "$mp4s = Get-ChildItem -Path $dir -Filter '*.mp4' -File | Sort-Object Name;" ^
  "$videos = @(); $idx = 0;" ^
  "foreach ($mp4 in $mp4s) {" ^
    "$idx++;" ^
    "$filename = $mp4.Name;" ^
    "$title = [System.IO.Path]::GetFileNameWithoutExtension($filename);" ^
    "$youtubeId = '';" ^
    "if ($filename -match '\[([A-Za-z0-9_\-]{11})\]') { $youtubeId = $Matches[1] }" ^
    "elseif ($filename -match '\(([A-Za-z0-9_\-]{11})\)') { $youtubeId = $Matches[1] };" ^
    "$vidId = if ($youtubeId) { 'yt_' + $youtubeId } else { 'video_' + $idx };" ^
    "$srtFile = '';" ^
    "foreach ($ext in @('srt','vtt')) {" ^
      "$candidate = [System.IO.Path]::ChangeExtension($mp4.FullName, $ext);" ^
      "if (Test-Path -LiteralPath $candidate) { $srtFile = [System.IO.Path]::GetFileName($candidate); break }" ^
    "};" ^
    "$videos += [PSCustomObject]@{ id=$vidId; title=$title; youtubeId=$youtubeId; localFile=$filename; subtitleFile=$srtFile }" ^
  "};" ^
  "$config = [ordered]@{ courseUrl=$courseUrl; coursePassword=$coursePassword; videos=$videos };" ^
  "$json = $config | ConvertTo-Json -Depth 5;" ^
  "Set-Content -Path (Join-Path $dir 'course-viewer.config.json') -Value $json -Encoding UTF8;" ^
  "Write-Host ('Generated course-viewer.config.json (' + $mp4s.Count + ' videos)')"

if %errorlevel% neq 0 (
  echo ERROR: PowerShell failed to generate course-viewer.config.json
  pause & exit /b 1
)

:start_server
echo.
echo Starting proxy server on port %PORT%...
echo Course Viewer at: http://localhost:%PORT%/

:: Open browser after delay
start /B cmd /C "ping 127.0.0.1 -n 3 >nul && start http://localhost:%PORT%/"

:: Run proxy server (try py launcher first, then python, then python3)
cd /d "%DIR%"
set "OC_PORT=%PORT%"

where py >nul 2>&1
if %errorlevel% == 0 ( py proxy.py & goto :end )

where python >nul 2>&1
if %errorlevel% == 0 ( python proxy.py & goto :end )

where python3 >nul 2>&1
if %errorlevel% == 0 ( python3 proxy.py & goto :end )

echo ERROR: Python not found.
echo Install Python 3 from https://python.org  (check "Add to PATH")
pause

:end
endlocal
