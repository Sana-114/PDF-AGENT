param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$SpecPath = (Join-Path $PSScriptRoot 'demo_narration_20261026.json')
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Media.SpeechSynthesis.SpeechSynthesisStream, Windows.Media.SpeechSynthesis, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
function Await-Voice($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}
$spec = Get-Content -LiteralPath $SpecPath -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object Language -eq 'zh-CN' | Select-Object -First 1
if (-not $voice) { throw 'Chinese speech voice is unavailable' }
New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null
$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$synth.Voice = $voice
foreach ($scene in $spec) {
    for ($i = 0; $i -lt $scene.lines.Count; $i++) {
        $audio = Join-Path $OutputDirectory ($scene.id + '-' + $i + '.wav')
        $stream = Await-Voice ($synth.SynthesizeTextToStreamAsync($scene.lines[$i])) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream])
        $reader = New-Object Windows.Storage.Streams.DataReader($stream.GetInputStreamAt(0))
        $null = Await-Voice ($reader.LoadAsync([uint32]$stream.Size)) ([uint32])
        $bytes = New-Object byte[] ([int]$stream.Size)
        $reader.ReadBytes($bytes)
        [IO.File]::WriteAllBytes($audio, $bytes)
        $reader.Dispose()
        $stream.Dispose()
    }
    Write-Output $scene.id
}
$voice.DisplayName | Set-Content (Join-Path $OutputDirectory 'voice.txt') -Encoding UTF8
$synth.Dispose()
