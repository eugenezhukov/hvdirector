$os = Get-CimInstance -ClassName Win32_OperatingSystem
return @{
    memory_free_mb  = [int64][math]::Round([double]$os.FreePhysicalMemory / 1KB)
    memory_total_mb = [int64][math]::Round([double]$os.TotalVisibleMemorySize / 1KB)
}
