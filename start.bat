@echo off
setlocal EnableDelayedExpansion
set "PORT=7478"
set "DIR=%~dp0"
if "!DIR:~-1!"=="\" set "DIR=!DIR:~0,-1!"
set "GITHUB_RAW=https://raw.githubusercontent.com/patchamama/Course-viewer/main"

echo === Course Viewer Launcher ===
echo Directory: !DIR!

:: App files live in system temp — shared across all course folders, never pollutes project dirs
set "APPDIR=%TEMP%\courseviewer_app"
if not exist "!APPDIR!" mkdir "!APPDIR!"

:: ── Decide: fresh install or version check ───────────────────────────────────
set "_app_files_exist=1"
if not exist "!APPDIR!\proxy.py"           set "_app_files_exist=0"
if not exist "!APPDIR!\course-viewer.html" set "_app_files_exist=0"

if "!_app_files_exist!"=="0" goto :fresh_install
goto :version_check

:: ─────────────────────────────────────────────────────────────────────────────
:fresh_install
:: New directory — download required files without asking
for %%F in (proxy.py course-viewer.html) do (
  if not exist "!APPDIR!\%%F" (
    echo Downloading %%F from GitHub...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '!GITHUB_RAW!/%%F' -OutFile '!APPDIR!\%%F' -UseBasicParsing"
    if !errorlevel! neq 0 (
      echo ERROR: Failed to download %%F. Check your internet connection.
      pause & exit /b 1
    )
    echo   OK %%F
  )
)
goto :gen_config

:: ─────────────────────────────────────────────────────────────────────────────
:version_check
:: Files exist — write PS1 to temp (outside any paren block to avoid cmd parse issues)
set "_PS=!TEMP!\cv_check.ps1"
echo $dir       = '!DIR!'                                                                    > "!_PS!"
echo $raw       = '!GITHUB_RAW!'                                                            >> "!_PS!"
echo $html      = Get-Content -LiteralPath "$dir\_app\course-viewer.html" -Raw -EA SilentlyContinue >> "!_PS!"
echo $localVer  = ''                                                                         >> "!_PS!"
echo if ($html -and $html -match "APP_VERSION = '([0-9][0-9.]*)'") {                       >> "!_PS!"
echo   $localVer = $Matches[1]                                                               >> "!_PS!"
echo }                                                                                        >> "!_PS!"
echo $latestVer = ''                                                                          >> "!_PS!"
echo try {                                                                                    >> "!_PS!"
echo   $r = Invoke-RestMethod 'https://api.github.com/repos/patchamama/Course-viewer/releases/latest' -TimeoutSec 5 >> "!_PS!"
echo   $latestVer = $r.tag_name -replace '^v', ''                                           >> "!_PS!"
echo } catch {}                                                                               >> "!_PS!"
echo if ($latestVer -and $localVer -and ([version]$latestVer -gt [version]$localVer)) {     >> "!_PS!"
echo   Write-Host ""                                                                          >> "!_PS!"
echo   Write-Host "  New version available: v$latestVer  (installed: v$localVer)"           >> "!_PS!"
echo   $choice = Read-Host "  Update now? [y/N]"                                             >> "!_PS!"
echo   if ($choice -match '^[Yy]') {                                                         >> "!_PS!"
echo     foreach ($f in @('proxy.py', 'course-viewer.html')) {                              >> "!_PS!"
echo       Write-Host "  Updating $f..."                                                     >> "!_PS!"
echo       Invoke-WebRequest -Uri "$raw/$f" -OutFile "$dir\_app\$f" -UseBasicParsing        >> "!_PS!"
echo       Write-Host "  OK $f"                                                              >> "!_PS!"
echo     }                                                                                    >> "!_PS!"
echo   }                                                                                      >> "!_PS!"
echo }                                                                                        >> "!_PS!"
powershell -NoProfile -ExecutionPolicy Bypass -File "!_PS!"
del "!_PS!" 2>nul

:: ─────────────────────────────────────────────────────────────────────────────
:gen_config
if exist "!DIR!\course-viewer.config.json" (
  echo course-viewer.config.json already exists -- skipping generation
  goto :start_server
)

:: Generate course-viewer.config.json (also via PS1 temp file — outside any paren block)
set "_PS2=!TEMP!\cv_gen.ps1"
echo $dir            = '!DIR!'                                                               > "!_PS2!"
echo $courseUrl      = ''                                                                     >> "!_PS2!"
echo $coursePassword = ''                                                                     >> "!_PS2!"
echo $readme = Join-Path $dir 'course.readme.txt'                                            >> "!_PS2!"
echo if (Test-Path -LiteralPath $readme) {                                                   >> "!_PS2!"
echo   foreach ($line in (Get-Content -Path $readme -Encoding UTF8)) {                      >> "!_PS2!"
echo     if ($line -match '^courseUrl\s*:\s*(.+)$')          { $courseUrl      = $Matches[1].Trim() } >> "!_PS2!"
echo     elseif ($line -match '^coursePassword\s*:\s*(.+)$') { $coursePassword = $Matches[1].Trim() } >> "!_PS2!"
echo   }                                                                                      >> "!_PS2!"
echo } else { Write-Host 'INFO: course.readme.txt not found' }                              >> "!_PS2!"
echo $mp4s   = Get-ChildItem -LiteralPath $dir -Filter '*.mp4' -File                       >> "!_PS2!"
echo $mp4s   = $mp4s ^| Sort-Object Name                                                    >> "!_PS2!"
echo $videos = @()                                                                            >> "!_PS2!"
echo $idx    = 0                                                                              >> "!_PS2!"
echo foreach ($mp4 in $mp4s) {                                                               >> "!_PS2!"
echo   $idx++                                                                                 >> "!_PS2!"
echo   $filename  = $mp4.Name                                                                >> "!_PS2!"
echo   $title     = [IO.Path]::GetFileNameWithoutExtension($filename)                       >> "!_PS2!"
echo   $youtubeId = ''                                                                        >> "!_PS2!"
echo   if      ($filename -match '\[([A-Za-z0-9_\-]{11})\]')  { $youtubeId = $Matches[1] } >> "!_PS2!"
echo   elseif  ($filename -match '\(([A-Za-z0-9_\-]{11})\)')  { $youtubeId = $Matches[1] } >> "!_PS2!"
echo   $vidId   = if ($youtubeId) { 'yt_' + $youtubeId } else { 'video_' + $idx }         >> "!_PS2!"
echo   $srtFile = ''                                                                          >> "!_PS2!"
echo   foreach ($ext in @('srt', 'vtt')) {                                                  >> "!_PS2!"
echo     $cand = [IO.Path]::ChangeExtension($mp4.FullName, $ext)                           >> "!_PS2!"
echo     if (Test-Path -LiteralPath $cand) { $srtFile = [IO.Path]::GetFileName($cand); break } >> "!_PS2!"
echo   }                                                                                      >> "!_PS2!"
echo   $videos += [PSCustomObject]@{ id=$vidId; title=$title; youtubeId=$youtubeId; localFile=$filename; subtitleFile=$srtFile } >> "!_PS2!"
echo }                                                                                        >> "!_PS2!"
echo $cfg  = [ordered]@{ courseUrl=$courseUrl; coursePassword=$coursePassword; videos=$videos } >> "!_PS2!"
echo $json = $cfg ^| ConvertTo-Json -Depth 5                                                >> "!_PS2!"
echo $utf8NoBom = [System.Text.UTF8Encoding]::new($false)                                    >> "!_PS2!"
echo [System.IO.File]::WriteAllText((Join-Path $dir 'course-viewer.config.json'), $json, $utf8NoBom) >> "!_PS2!"
echo Write-Host ('Generated course-viewer.config.json (' + $mp4s.Count + ' videos)')       >> "!_PS2!"
powershell -NoProfile -ExecutionPolicy Bypass -File "!_PS2!"
if !errorlevel! neq 0 (
  echo ERROR: PowerShell failed to generate course-viewer.config.json
  del "!_PS2!" 2>nul
  pause & exit /b 1
)
del "!_PS2!" 2>nul

:: ─────────────────────────────────────────────────────────────────────────────
:start_server
echo.

:: Kill any existing proxy on this port so the new directory always wins
for /f "tokens=5" %%P in ('netstat -aon 2^>nul ^| findstr /R ":%PORT% .*LISTENING"') do taskkill /F /PID %%P >nul 2>&1
ping 127.0.0.1 -n 2 >nul

echo Starting proxy server on port %PORT%...
echo Course Viewer at: http://localhost:%PORT%/

start /B cmd /C "ping 127.0.0.1 -n 3 >nul && start http://localhost:%PORT%/?%random%%random%"

cd /d "!APPDIR!"
set "OC_PORT=%PORT%"
set "OC_STATIC_DIR=!DIR!"

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
