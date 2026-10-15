param(
    [string]$VenvPath = (Join-Path $PSScriptRoot ".venv"),
    [switch]$KeepVenv
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message, [int]$Code = 1) {
    [Console]::Error.WriteLine($Message)
    if ($Code -eq 0) { $Code = 9 }
    exit $Code
}

function Require-ExitCode([string]$Command, [int]$Expected, [int]$Actual) {
    if ($Actual -ne $Expected) { Fail "$Command expected exit $Expected, got $Actual" 9 }
}

if ($env:OS -ne "Windows_NT" -or [IntPtr]::Size -ne 8 -or [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne "X64") {
    Fail "This bundle requires 64-bit Windows x86_64 (win_amd64)." 2
}
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { Fail "Python Launcher (py.exe) is required to select CPython 3.12." 2 }
$versionText = & py -3.12 -c "import platform,sys; print(platform.python_implementation() + ' ' + '.'.join(map(str,sys.version_info[:2])))"
if ($LASTEXITCODE -ne 0 -or $versionText.Trim() -ne "CPython 3.12") { Fail "CPython 3.12 is required; got $versionText" 2 }

if (-not $KeepVenv -and (Test-Path -LiteralPath $VenvPath)) { Remove-Item -LiteralPath $VenvPath -Recurse -Force }
if (-not (Test-Path -LiteralPath $VenvPath)) { & py -3.12 -m venv $VenvPath; if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" $LASTEXITCODE } }
$venvPython = Join-Path $VenvPath "Scripts\python.exe"
& $venvPython -c "import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8"
if ($LASTEXITCODE -ne 0) { Fail "venv interpreter validation failed" $LASTEXITCODE }
& $venvPython (Join-Path $PSScriptRoot "verify_windows_bundle.py") $PSScriptRoot
if ($LASTEXITCODE -ne 0) { Fail "wheel integrity verification failed" $LASTEXITCODE }
& $venvPython -m pip install --no-index --only-binary=:all: --require-hashes --find-links (Join-Path $PSScriptRoot "wheelhouse") -r (Join-Path $PSScriptRoot "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { Fail "offline wheel installation failed" $LASTEXITCODE }
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { Fail "pip check failed" $LASTEXITCODE }
& $venvPython -c "import simcompare, numpy, pandas, pyarrow, zarr, PIL, yaml; print('imports OK')"
if ($LASTEXITCODE -ne 0) { Fail "runtime import check failed" $LASTEXITCODE }

# All bundle and fixture paths are rooted in $PSScriptRoot, so Unicode and spaces are retained.
$work = Join-Path $env:TEMP ("simcompare_smoke_" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $work | Out-Null
try {
    $input = Join-Path $PSScriptRoot "fixtures\real_hotspot\input"
    $imported = Join-Path $work "imported"
    $fixture = Join-Path $PSScriptRoot "fixtures\real_hotspot"
    & $venvPython -m simcompare import-hotspots --root $input --out $imported --expected-cases (Join-Path $fixture "expected_cases.txt")
    Require-ExitCode "import-hotspots" 3 $LASTEXITCODE
    Copy-Item -LiteralPath (Join-Path $fixture "pairs.csv") -Destination (Join-Path $imported "pairs.csv")
    $config = Join-Path $work "real config 한글.yaml"
    @"
schema_version: numeric_config_v1
mode: real
output_dir: reports
reference:
  data_dir: "$($imported -replace '\\','/')"
  condition_assignment: &assignment
    product_id: BUNDLE_FIXTURE
    design_version: BUNDLE_FIXTURE
    source_id: BUNDLE_FIXTURE
    process_step_id: BUNDLE_FIXTURE
    exposure_id: BUNDLE_FIXTURE
    unit_id: BUNDLE_FIXTURE
    pair_table_path: pairs.csv
    layer_stack:
      - actual_layer: L1
        stack_order: 1
        density_process_layer: P1
target:
  data_dir: "$($imported -replace '\\','/')"
  condition_assignment: *assignment
map:
  input:
    data_dir: "$($imported -replace '\\','/')"
    condition_assignment: *assignment
  diameter_um: 1
"@ | Set-Content -LiteralPath $config -Encoding utf8
    foreach ($command in @("validate-data", "compare", "electrode-map")) {
        & $venvPython -m simcompare $command --config $config
        Require-ExitCode $command 3 $LASTEXITCODE
    }
    $validation = Get-Content -Raw -LiteralPath (Join-Path $work "reports\validation.json") | ConvertFrom-Json
    if ($validation.is_synthetic -ne $false -or $validation.raw_observations_status -ne "available") { Fail "fixture validation output did not match expected real observations" 4 }
    $comparison = Get-Content -Raw -LiteralPath (Join-Path $work "reports\comparison.json") | ConvertFrom-Json
    if ($comparison.is_synthetic -ne $false -or $comparison.weighted_rates_status -ne "unavailable") { Fail "fixture comparison output was not conservatively incomplete" 4 }
    $map = Get-Content -Raw -LiteralPath (Join-Path $work "reports\electrode_map.json") | ConvertFrom-Json
    $expected = Get-Content -Raw -LiteralPath (Join-Path $fixture "expected.json") | ConvertFrom-Json
    $union = $comparison.reference.groups | Where-Object { $_.group.diameter_um -eq 1 } | Select-Object -First 1
    if ($null -eq $union -or $union.union.status -ne $expected.diameter_1_union_status -or [math]::Abs($union.union.value - $expected.diameter_1_union_um2) -gt 1e-9) { Fail "fixture configured comparison did not retain expected diameter-1 union" 4 }
    $mappedLayer = $map.layers | Select-Object -First 1
    if ($map.is_synthetic -ne $false -or $mappedLayer.status -ne $expected.diameter_1_map_status -or $mappedLayer.histogram.'2' -ne $expected.diameter_1_map_cells_at_count_2 -or $map.reasons -contains "electrode_polarity_unknown") { Fail "fixture configured map did not compute the expected observed count" 4 }
    Write-Output "Windows offline smoke passed; configured comparison union and map count match fixture expectations. Unknown electrode polarity limits ROI review only."
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
