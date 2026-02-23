param(
  [string]$EnvFile = ".env",
  [string]$SqlFile = "scripts/import_program_courses.sql",
  [string]$PsqlPath = "psql"
)

if (-not (Test-Path -LiteralPath $EnvFile)) {
  throw "Env file not found: $EnvFile"
}

if (-not (Test-Path -LiteralPath $SqlFile)) {
  throw "SQL file not found: $SqlFile"
}

# Load KEY=VALUE lines into *process* env
Get-Content -LiteralPath $EnvFile | ForEach-Object {
  $line = $_.Trim()
  if ($line.Length -eq 0) { return }
  if ($line.StartsWith("#")) { return }

  $eq = $line.IndexOf("=")
  if ($eq -lt 1) { return }

  $key = $line.Substring(0, $eq).Trim()
  $val = $line.Substring($eq + 1).Trim()

  # strip surrounding quotes if present
  if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
    $val = $val.Substring(1, $val.Length - 2)
  }

  Set-Item -Path ("Env:" + $key) -Value $val
}

if ([string]::IsNullOrWhiteSpace($env:POSTGRES_HOST)) { throw "Missing POSTGRES_HOST in $EnvFile" }
if ([string]::IsNullOrWhiteSpace($env:POSTGRES_DB)) { throw "Missing POSTGRES_DB in $EnvFile" }
if ([string]::IsNullOrWhiteSpace($env:POSTGRES_USER)) { throw "Missing POSTGRES_USER in $EnvFile" }
if ([string]::IsNullOrWhiteSpace($env:POSTGRES_PASSWORD)) { throw "Missing POSTGRES_PASSWORD in $EnvFile" }
if ([string]::IsNullOrWhiteSpace($env:POSTGRES_PORT)) { throw "Missing POSTGRES_PORT in $EnvFile" }

$env:PGPASSWORD = $env:POSTGRES_PASSWORD

# If psql is not on PATH, use -PsqlPath with the full exe path
if ($PsqlPath -ne "psql" -and -not (Test-Path -LiteralPath $PsqlPath)) {
  throw "psql.exe not found at: $PsqlPath"
}

Write-Host "Running SQL via psql..."
Write-Host "  EnvFile: $EnvFile"
Write-Host "  SqlFile: $SqlFile"
Write-Host "  PsqlPath: $PsqlPath"
Write-Host "  Host: $($env:POSTGRES_HOST)  Port: $($env:POSTGRES_PORT)  DB: $($env:POSTGRES_DB)  User: $($env:POSTGRES_USER)"

& $PsqlPath `
  -h $env:POSTGRES_HOST `
  -p $env:POSTGRES_PORT `
  -U $env:POSTGRES_USER `
  -d $env:POSTGRES_DB `
  -f $SqlFile

if ($LASTEXITCODE -ne 0) {
  throw "psql exited with code $LASTEXITCODE"
}

Write-Host "Done."