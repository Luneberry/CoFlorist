param([string]$OutputPath)

# Windows PowerShell 5.1 exposes the installed OneCore Korean voice through WinRT.
# Input is UTF-8 JSON on stdin: { "text": "...", "outputPath": "absolute.wav" }.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$speech = $null
$audioStream = $null
$memoryStream = $null

try {
    $inputJson = [Console]::In.ReadToEnd()
    if ($inputJson.Length -gt 65536) { throw 'Speech input is too large.' }
    $request = $inputJson | ConvertFrom-Json
    if ($request.text -isnot [string] -or [string]::IsNullOrWhiteSpace($request.text) -or $request.text.Length -gt 5000) {
        throw 'Speech text must contain between 1 and 5000 characters.'
    }
    if ([string]::IsNullOrWhiteSpace($OutputPath)) { $OutputPath = $request.outputPath }
    if ([string]::IsNullOrWhiteSpace($OutputPath) -or -not [IO.Path]::IsPathRooted($OutputPath)) {
        throw 'An absolute output WAV path is required.'
    }
    $OutputPath = [IO.Path]::GetFullPath($OutputPath)
    if ([IO.Path]::GetExtension($OutputPath) -ne '.wav') { throw 'The output file must use the .wav extension.' }
    if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($OutputPath))) { throw 'The output directory does not exist.' }

    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
    $null = [Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime]
    $voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices |
        Where-Object Language -eq 'ko-KR' | Select-Object -First 1
    if ($null -eq $voice) { throw 'No installed Korean Windows speech voice was found. Install the Korean speech language pack.' }

    $speech = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
    $speech.Voice = $voice
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetGenericArguments().Count -eq 1 } |
        Select-Object -First 1
    $operation = $speech.SynthesizeTextToStreamAsync($request.text)
    $task = $asTask.MakeGenericMethod([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]).Invoke($null, @($operation))
    if (-not $task.Wait(40000)) { throw 'Korean speech synthesis timed out.' }
    $audio = $task.Result
    $audioStream = [System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($audio)
    $memoryStream = New-Object System.IO.MemoryStream
    $audioStream.CopyTo($memoryStream)
    $bytes = $memoryStream.ToArray()
    if ($bytes.Length -lt 44 -or [Text.Encoding]::ASCII.GetString($bytes, 0, 4) -ne 'RIFF') {
        throw 'Windows did not return a valid WAV file.'
    }
    [IO.File]::WriteAllBytes($OutputPath, $bytes)
    [Console]::Out.WriteLine((@{
        path = $OutputPath
        voice = $voice.DisplayName
        language = $voice.Language
        contentType = $audio.ContentType
        bytes = $bytes.Length
    } | ConvertTo-Json -Compress))
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
} finally {
    if ($null -ne $memoryStream) { $memoryStream.Dispose() }
    if ($null -ne $audioStream) { $audioStream.Dispose() }
    if ($null -ne $speech) { $speech.Dispose() }
}
