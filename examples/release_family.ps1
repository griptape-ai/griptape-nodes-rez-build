<#
.SYNOPSIS
Promote one Rez family and its resolved dependencies from the local store to the release store.

.DESCRIPTION
An example to adapt: see docs/releasing.md. The PowerShell version of release_family.sh, for
Windows machines without bash. It also runs in PowerShell 7 on macOS and Linux.

Rez's tools (rez-env, rez-context, rez-cp) must be on PATH.

.PARAMETER Family
The family to release, for example griptape_nodes_library_standard.

.PARAMETER LocalStore
The local store the build tool wrote to.

.PARAMETER ReleaseStore
Where released packages go.

.PARAMETER ProductionPaths
The package search path artists use, WITHOUT the local store: the release store, plus the studio
stores holding python, platform, os, and arch. Separate entries with ';' on Windows, ':' elsewhere.

.PARAMETER DryRun
List what would be copied, without copying anything or validating.

.EXAMPLE
.\release_family.ps1 griptape_nodes_library_diffusers P:\griptape\rez\packages\local `
    P:\rez\packages\release "P:\rez\packages\release;P:\rez\packages"

.EXAMPLE
$env:EXTRA_REQUESTS = ".torch_backend-cu118"
.\release_family.ps1 griptape_nodes_library_diffusers ...
Also releases that torch build's packages.
#>
param(
    [Parameter(Mandatory)] [string] $Family,
    [Parameter(Mandatory)] [string] $LocalStore,
    [Parameter(Mandatory)] [string] $ReleaseStore,
    [Parameter(Mandatory)] [string] $ProductionPaths,
    [switch] $DryRun
)

$ErrorActionPreference = "Stop"

# Native commands don't stop the script when they fail, so check each exit code.
function Invoke-Rez {
    param([string] $Tool, [string[]] $Arguments)
    & $Tool @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Attempted to run '$Tool $($Arguments -join ' ')'. Failed with exit code $LASTEXITCODE."
    }
}

$extraRequests = @()
if ($env:EXTRA_REQUESTS) {
    $extraRequests = $env:EXTRA_REQUESTS -split "\s+" | Where-Object { $_ }
}
# Rez separates search paths like the platform does: ';' on Windows, ':' elsewhere.
$separator = [IO.Path]::PathSeparator

New-Item -ItemType Directory -Force -Path $ReleaseStore | Out-Null
$work = New-Item -ItemType Directory -Path (Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName()))
try {
    # 1. Resolve the family the way artists will, but with the local store first.
    $localContext = Join-Path $work "local.rxt"
    Invoke-Rez "rez-env" (@($Family) + $extraRequests + @("--paths", "$LocalStore$separator$ProductionPaths", "--output", $localContext))
    $resolve = (& rez-context --print-resolve $localContext) -split "\s+" | Where-Object { $_ }
    if ($LASTEXITCODE -ne 0) {
        throw "Attempted to read the resolve of $Family. Failed with exit code $LASTEXITCODE."
    }

    # 2. Copy each resolved package that came from the local store and is not yet released.
    foreach ($spec in $resolve) {
        # Family names never contain '-'; versions can.
        $name, $version = $spec -split "-", 2
        if (-not (Test-Path (Join-Path $LocalStore "$name/$version"))) {
            continue  # a studio package, not ours to release
        }
        if (Test-Path (Join-Path $ReleaseStore "$name/$version")) {
            Write-Output "already released: $spec"
            continue
        }
        # rez-cp --dry-run fails on packages without variants (rez 3.4.0), so a dry run only lists them.
        if ($DryRun) {
            Write-Output "would copy: $spec"
            continue
        }
        Invoke-Rez "rez-cp" @($spec, "--paths", $LocalStore, "--dest-path", $ReleaseStore, "--allow-empty", "--keep-timestamp")
    }

    # 3. Validate: the family must resolve without the local store.
    if (-not $DryRun) {
        $productionContext = Join-Path $work "production.rxt"
        Invoke-Rez "rez-env" (@($Family) + $extraRequests + @("--paths", $ProductionPaths, "--output", $productionContext))
        Write-Output "$Family resolves without the local store"
    }
}
finally {
    Remove-Item -Recurse -Force $work
}
