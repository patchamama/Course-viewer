@echo off
setlocal EnableDelayedExpansion
set "PORT=8080"
set "DIR=%~dp0"
if "%DIR:~-1%"=="\" set "DIR=%DIR:~0,-1%"
set "GITHUB_RAW=https://raw.githubusercontent.com/patchamama/Course-viewer/main"

echo === Course Viewer Launcher ===
echo Directory: !DIR!

:: ── Auto-download or update app files ────────────────────────────────────────
set "_app_files_exist=1"
if not exist "!DIR!\proxy.py"          set "_app_files_exist=0"
if not exist "!DIR!\course-viewer.html" set "_app_files_exist=0"

if "!_app_files_exist!"=="0" (
  :: Fresh install — download both files without asking
  for %%F in (proxy.py course-viewer.html) do (
    if not exist "!DIR!\%%F" (
      echo Downloading %%F from GitHub...
      powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -Uri '!GITHUB_RAW!/%%F' -OutFile '!DIR!\%%F' -UseBasicParsing"
      if !errorlevel! neq 0 (
        echo ERROR: Failed to download %%F. Check your internet connection.
        pause & exit /b 1
      )
      echo   OK %%F
    )
  )
) else (
  :: Files exist — check GitHub for a newer release.
  :: Write a PS1 temp file: avoids quote/caret escaping hell in -Command mode,
  :: and avoids the batch parser misreading parens in !DIR! as block delimiters.
  set "_PS=%TEMP%\cv_check.ps1"
  echo $dir       = '!DIR!'                                                                                    > "%_PS%"
  echo $raw       = '!GITHUB_RAW!'                                                                            >> "%_PS%"
  echo $html      = Get-Content -LiteralPath "$dir\course-viewer.html" -Raw -EA SilentlyContinue             >> "%_PS%"
  echo $localVer  = ''                                                                                         >> "%_PS%"
  echo if ($html -and $html -match "APP_VERSION = '([0-9][0-9.]*)'") { $localVer = $Matches[1] }            >> "%_PS%"
  echo $latestVer = ''                                                                                         >> "%_PS%"
  echo try {                                                                                                   >> "%_PS%"
  echo   $r = Invoke-RestMethod 'https://api.github.com/repos/patchamama/Course-viewer/releases/latest' -TimeoutSec 5 >> "%_PS%"
  echo   $latestVer = $r.tag_name -replace '^^v',''                                                          >> "%_PS%"
  echo } catch {}                                                                                              >> "%_PS%"
  echo if ($latestVer -and $localVer -and ([version]$latestVer -gt [version]$localVer)) {                   >> "%_PS%"
  echo   Write-Host ""                                                                                        >> "%_PS%"
  echo   Write-Host "  New version available: v$latestVer  (installed: v$localVer)"                         >> "%_PS%"
  echo   $choice = Read-Host "  Update now? [y/N]"                                                           >> "%_PS%"
  echo   if ($choice -match '^^[Yy]') {                                                                      >> "%_PS%"
  echo     foreach ($f in @('proxy.py', 'course-viewer.html')) {                                             >> "%_PS%"
  echo       Write-Host "  Updating $f..."                                                                   >> "%_PS%"
  echo       Invoke-WebRequest -Uri "$raw/$f" -OutFile "$dir\$f" -UseBasicParsing                           >> "%_PS%"
  echo       Write-Host "  OK $f"                                                                            >> "%_PS%"
  echo     }                                                                                                   >> "%_PS%"
  echo   }                                                                                                     >> "%_PS%"
  echo }                                                                                                       >> "%_PS%"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS%"
  del "%_PS%" 2>nul
)

:: Only generate course-viewer.config.json if it does not already exist
if exist "!DIR!\course-viewer.config.json" (
  echo course-viewer.config.json already exists -- skipping generation
  goto :start_server
)

:: Generate course-viewer.config.json via PS1 temp file (same reason as above)
set "_PS2=%TEMP%\cv_gen.ps1"
echo $dir            = '!DIR!'                                                                                 > "%_PS2%"
echo $courseUrl      = ''                                                                                       >> "%_PS2%"
echo $coursePassword = ''                                                                                       >> "%_PS2%"
echo $readme = Join-Path $dir 'course.readme.txt'                                                             >> "%_PS2%"
echo if (Test-Path -LiteralPath $readme) {                                                                    >> "%_PS2%"
echo   foreach ($line in (Get-Content -Path $readme -Encoding UTF8)) {                                       >> "%_PS2%"
echo     if ($line -match '^^courseUrl\s*:\s*(.+)$')      { $courseUrl      = $Matches[1].Trim() }          >> "%_PS2%"
echo     elseif ($line -match '^^coursePassword\s*:\s*(.+)$') { $coursePassword = $Matches[1].Trim() }      >> "%_PS2%"
echo   }                                                                                                       >> "%_PS2%"
echo } else { Write-Host 'INFO: course.readme.txt not found' }                                               >> "%_PS2%"
echo $mp4s   = Get-ChildItem -LiteralPath $dir -Filter '*.mp4' -File ^| Sort-Object Name                    >> "%_PS2%"
echo $videos = @(); $idx = 0                                                                                   >> "%_PS2%"
echo foreach ($mp4 in $mp4s) {                                                                                >> "%_PS2%"
echo   $idx++                                                                                                  >> "%_PS2%"
echo   $filename  = $mp4.Name                                                                                  >> "%_PS2%"
echo   $title     = [IO.Path]::GetFileNameWithoutExtension($filename)                                        >> "%_PS2%"
echo   $youtubeId = ''                                                                                         >> "%_PS2%"
echo   if ($filename -match '\[([A-Za-z0-9_\-]{11})\]')  { $youtubeId = $Matches[1] }                      >> "%_PS2%"
echo   elseif ($filename -match '\(([A-Za-z0-9_\-]{11})\)') { $youtubeId = $Matches[1] }                   >> "%_PS2%"
echo   $vidId   = if ($youtubeId) { 'yt_' + $youtubeId } else { 'video_' + $idx }                          >> "%_PS2%"
echo   $srtFile = ''                                                                                           >> "%_PS2%"
echo   foreach ($ext in @('srt','vtt')) {                                                                    >> "%_PS2%"
echo     $cand = [IO.Path]::ChangeExtension($mp4.FullName, $ext)                                            >> "%_PS2%"
echo     if (Test-Path -LiteralPath $cand) { $srtFile = [IO.Path]::GetFileName($cand); break }             >> "%_PS2%"
echo   }                                                                                                       >> "%_PS2%"
echo   $videos += [PSCustomObject]@{ id=$vidId; title=$title; youtubeId=$youtubeId; localFile=$filename; subtitleFile=$srtFile } >> "%_PS2%"
echo }                                                                                                         >> "%_PS2%"
echo $cfg  = [ordered]@{ courseUrl=$courseUrl; coursePassword=$coursePassword; videos=$videos }              >> "%_PS2%"
echo $json = $cfg ^| ConvertTo-Json -Depth 5                                                                 >> "%_PS2%"
echo Set-Content -LiteralPath (Join-Path $dir 'course-viewer.config.json') -Value $json -Encoding UTF8      >> "%_PS2%"
echo Write-Host ('Generated course-viewer.config.json (' + $mp4s.Count + ' videos)')                        >> "%_PS2%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%_PS2%"
del "%_PS2%" 2>nul

if !errorlevel! neq 0 (
  echo ERROR: PowerShell failed to generate course-viewer.config.json
  pause & exit /b 1
)

:start_server
echo.
echo Starting proxy server on port %PORT%...
echo Course Viewer at: http://localhost:%PORT%/

:: Open browser after a short delay
start /B cmd /C "ping 127.0.0.1 -n 3 >nul && start http://localhost:%PORT%/"

:: Run proxy server (try py launcher first, then python, then python3)
cd /d "!DIR!"
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
