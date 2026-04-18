param(
    [string]$Owner = "rabbit99",
    [string]$Repo = "buytool-report-hub",
    [string]$Branch = "release"
)

$defaultGhPath = "C:\Program Files\GitHub CLI\gh.exe"
$ghCommand = Get-Command gh -ErrorAction SilentlyContinue

if ($ghCommand) {
    $gh = $ghCommand.Source
} elseif (Test-Path $defaultGhPath) {
    $gh = $defaultGhPath
} else {
    Write-Error "GitHub CLI not found. Install gh first."
    exit 1
}

function Invoke-Gh {
    param([string[]]$Args)
    & $gh @Args
    return $LASTEXITCODE
}

Write-Host "Checking GitHub auth..."
Invoke-Gh -Args @("auth", "status") | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Not logged in. Run: gh auth login"
    exit 1
}

$repoSlug = "$Owner/$Repo"

Write-Host "Ensuring GitHub Pages is enabled..."
Invoke-Gh -Args @("api", "repos/$repoSlug/pages") | Out-Null
if ($LASTEXITCODE -ne 0) {
    Invoke-Gh -Args @("api", "-X", "POST", "repos/$repoSlug/pages", "-f", "build_type=workflow") | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to enable GitHub Pages."
        exit 1
    }
}

Write-Host "Triggering manual deployment workflow on branch '$Branch'..."
Invoke-Gh -Args @("workflow", "run", "deploy-pages.yml", "-R", $repoSlug, "--ref", $Branch) | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to trigger deploy workflow."
    exit 1
}

Write-Host "Done. Track runs at: https://github.com/$repoSlug/actions"
Write-Host "Site URL: https://$Owner.github.io/$Repo/"
