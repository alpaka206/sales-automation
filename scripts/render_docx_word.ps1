param(
    [Parameter(Mandatory = $true)]
    [string]$InputDocx,
    [Parameter(Mandatory = $true)]
    [string]$OutputPdf
)

$inputPath = (Resolve-Path -LiteralPath $InputDocx).Path
$outputPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPdf))
$outputDir = [System.IO.Path]::GetDirectoryName($outputPath)
[System.IO.Directory]::CreateDirectory($outputDir) | Out-Null

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($inputPath, $false, $true)
    # 17 = wdExportFormatPDF; 0 = screen-optimized but print-quality vectors/text remain.
    $document.ExportAsFixedFormat($outputPath, 17, $false, 0)
}
finally {
    if ($document -ne $null) {
        $document.Close($false)
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($document) | Out-Null
    }
    if ($word -ne $null) {
        $word.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

Write-Output $outputPath
