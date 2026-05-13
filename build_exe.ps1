$ErrorActionPreference = "Stop"

Write-Host "Building native Python extension..."
$maturin = python -m pip show maturin 2>$null
if ($LASTEXITCODE -eq 0) {
    python -m maturin develop --release
} else {
    Write-Host "maturin is not installed; using cargo + direct _core.pyd copy fallback."
    cargo build --release
    Copy-Item -LiteralPath "target\release\_core.dll" -Destination "memcleaner\_core.pyd" -Force
}

Write-Host "Building light-mode Rust daemon..."
cargo build --release --bin memcleaner_daemon

Write-Host "Packaging MemCleaner.exe..."
$staleOneDir = "dist\MemCleaner"
if (Test-Path $staleOneDir) {
    Remove-Item -LiteralPath $staleOneDir -Recurse -Force
}
python -m PyInstaller MemCleaner.spec --clean --noconfirm

Write-Host "Done: dist\MemCleaner.exe"
