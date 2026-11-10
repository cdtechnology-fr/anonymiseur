# Installe (ou met à jour) le moteur `ano` et le modèle, puis affiche le chemin de ano.exe.
# Idempotent : ne télécharge que ce qui manque ou a changé. Lancé par le mod au démarrage.
# Sortie standard : UNIQUEMENT le chemin de ano.exe (le mod le lit). Erreurs : stderr, code 1.
param([Parameter(Mandatory = $true)][string]$PluginRoot)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # sans barre de progression : beaucoup plus rapide
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-Verified([string]$Url, [string]$Sha256, [string]$Dest) {
    $tmp = "$Dest.part"
    Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 -Path $tmp).Hash.ToLower()
    if ($actual -ne $Sha256.ToLower()) {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        throw "Empreinte invalide pour $Url (attendu $Sha256, reçu $actual)"
    }
    Move-Item -Force $tmp $Dest
}

try {
    $manifest = Get-Content -Raw -Path (Join-Path $PluginRoot 'engine-manifest.json') | ConvertFrom-Json
    $base = Join-Path $env:LOCALAPPDATA 'ASDVO\anonymiseur'
    $binDir = Join-Path $base ("bin\" + $manifest.version)
    $exe = Join-Path $binDir 'ano.exe'
    $marker = Join-Path $base ("installed-" + $manifest.version + ".ok")

    # déjà installé et vérifié pour cette version : réponse immédiate
    if ((Test-Path $marker) -and (Test-Path $exe)) { Write-Output $exe; exit 0 }

    if (-not (Test-Path $exe)) {
        New-Item -ItemType Directory -Force -Path $binDir | Out-Null
        $zip = Join-Path $binDir 'engine.zip'
        Get-Verified $manifest.engine.url $manifest.engine.sha256 $zip
        Expand-Archive -Path $zip -DestinationPath $binDir -Force
        Remove-Item $zip -Force
        if (-not (Test-Path $exe)) { throw "ano.exe absent de l'archive du moteur" }
        # nettoyage des anciennes versions (le service résident utilise l'ancienne : on l'arrête)
        Get-ChildItem (Join-Path $base 'bin') -Directory | Where-Object { $_.Name -ne $manifest.version } |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    }

    $modelDir = Join-Path $base ('models\' + $manifest.model.name)
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    foreach ($f in $manifest.model.files) {
        $dest = Join-Path $modelDir $f.name
        $ok = (Test-Path $dest) -and ((Get-FileHash -Algorithm SHA256 -Path $dest).Hash.ToLower() -eq $f.sha256.ToLower())
        if (-not $ok) { Get-Verified $f.url $f.sha256 $dest }
    }

    Set-Content -Path $marker -Value (Get-Date -Format o)
    Write-Output $exe
    exit 0
}
catch {
    [Console]::Error.WriteLine("anonymiseur : installation impossible : $($_.Exception.Message)")
    exit 1
}
