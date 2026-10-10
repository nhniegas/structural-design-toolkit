# Build the packaged program: dist\sdt\sdt.exe and its support files.
#
#   .\build_exe.ps1            build dist\sdt
#   .\build_exe.ps1 -Zip       also write dist\sdt-<version>-windows.zip
#
# Run it with the project's environment active (requirements.txt installed).
# The program needs no Python on the machine it runs on; it still needs ETABS
# and, for the PDF reports, a LaTeX distribution (see README.md).
param([switch]$Zip)

# PyInstaller logs on stderr; only exit codes decide whether a step failed.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

python -m pip install --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# A folder (--onedir), not one file: it starts faster and antivirus tools
# flag it less. --console keeps the terminal the commands print to.
# The data files of these packages (section tables, fonts, templates) are not
# found by PyInstaller on its own. The notebook tools are only used by Quarto.
python tools\generate_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m PyInstaller main.py --name sdt --console --onedir --noconfirm --clean `
    --icon assets\sdt.ico `
    --collect-data steelpy --collect-data ezdxf `
    --collect-data sectionproperties --collect-data concreteproperties `
    --collect-data pylatex `
    --exclude-module IPython --exclude-module ipykernel --exclude-module nbformat `
    --exclude-module nbclient --exclude-module pytest --exclude-module tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Copy-Item README.md, LICENSE -Destination dist\sdt
Copy-Item docs -Destination dist\sdt\docs -Recurse -Force

# The program must start and know its commands before it is handed over.
$version = (& dist\sdt\sdt.exe --version) -replace '.* ', ''
if ($LASTEXITCODE -ne 0) { throw "dist\sdt\sdt.exe did not start." }
& dist\sdt\sdt.exe --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "dist\sdt\sdt.exe --help failed." }
Write-Host "Built dist\sdt\sdt.exe (version $version)"

if ($Zip) {
    $archive = "dist\sdt-$version-windows.zip"
    if (Test-Path $archive) { Remove-Item $archive }
    # tar (part of Windows) and not Compress-Archive, which stops on a file
    # that an antivirus scan still holds open
    tar -a -c -f $archive -C dist sdt
    if ($LASTEXITCODE -ne 0) { throw "Could not pack $archive." }
    Write-Host "Packed $archive"
}
